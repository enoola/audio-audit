from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from .errors import AnalysisError
from .models import SpeechInterval, TranscriptCue
from .util import sha256_file

SAMPLE_RATE = 16_000
NEURAL_FRAME_SAMPLES = 512
NEURAL_FRAME_MS = NEURAL_FRAME_SAMPLES * 1000 // SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class ActivityConfig:
    frame_ms: int = 30
    minimum_speech_ms: int = 240
    minimum_silence_ms: int = 240
    padding_ms: int = 30
    minimum_threshold_dbfs: float = -55.0
    maximum_snr_db: float = 18.0
    minimum_snr_db: float = 6.0
    neural_speech_threshold: float = 0.50
    neural_release_threshold: float = 0.35


@dataclass(frozen=True, slots=True)
class ActivityResult:
    intervals: tuple[SpeechInterval, ...]
    method: str
    speech_present: Literal["yes", "no", "uncertain"]
    speech_duration_ms: int
    speech_ratio: float
    confidence: Literal["low", "medium", "high"]
    threshold_dbfs: float
    noise_floor_dbfs: float
    warnings: tuple[str, ...]
    backend: str = "energy"
    probability_threshold: float | None = None

    @property
    def active_seconds(self) -> float:
        return self.speech_duration_ms / 1000.0


class ActivityDetector(Protocol):
    backend: str
    probability_threshold: float | None

    def add_chunk(self, mono: np.ndarray) -> None: ...

    def finish(
        self,
        scope_duration_ms: int,
        transcript_cues: tuple[TranscriptCue, ...] = (),
    ) -> ActivityResult: ...

    def levels_and_starts(self) -> tuple[np.ndarray, np.ndarray]: ...


class _LevelCollector:
    """Collect fixed-size RMS frames for acoustic measurements."""

    def __init__(self, frame_ms: int = 30) -> None:
        self.frame_ms = frame_ms
        self.frame_samples = max(1, round(SAMPLE_RATE * frame_ms / 1000))
        self.levels: list[np.ndarray] = []
        self.starts: list[np.ndarray] = []
        self.sample_cursor = 0
        self.leftover = np.empty(0, dtype=np.float32)
        self.leftover_start = 0

    def add(self, mono: np.ndarray) -> None:
        if mono.ndim != 1 or mono.size == 0:
            return
        if self.leftover.size:
            samples = np.concatenate([self.leftover, mono])
            start = self.leftover_start
        else:
            samples = mono
            start = self.sample_cursor
        usable = (samples.size // self.frame_samples) * self.frame_samples
        if usable:
            frames = samples[:usable].reshape(-1, self.frame_samples)
            rms = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1) + 1e-12)
            self.levels.append(20.0 * np.log10(rms))
            self.starts.append(
                start + np.arange(frames.shape[0], dtype=np.int64) * self.frame_samples
            )
        self.leftover = samples[usable:].copy()
        self.leftover_start = start + usable
        self.sample_cursor = start + samples.size

    def result(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.levels:
            return np.empty(0), np.empty(0, dtype=np.int64)
        return np.concatenate(self.levels), np.concatenate(self.starts)


class EnergyActivityDetector:
    """Transparent energy activity fallback; it is not presented as neural VAD."""

    backend = "energy"
    probability_threshold = None

    def __init__(self, config: ActivityConfig | None = None) -> None:
        self.config = config or ActivityConfig()
        self._collector = _LevelCollector(self.config.frame_ms)

    @property
    def frame_count(self) -> int:
        return self._collector.result()[0].size

    def add_chunk(self, mono: np.ndarray) -> None:
        self._collector.add(mono)

    def finish(
        self,
        scope_duration_ms: int,
        transcript_cues: tuple[TranscriptCue, ...] = (),
    ) -> ActivityResult:
        levels, starts_samples = self._collector.result()
        if not levels.size:
            return _empty_activity(
                backend=self.backend,
                method="none",
                threshold_dbfs=self.config.minimum_threshold_dbfs,
                warning="No decodable audio samples were available for activity detection",
            )

        low = float(np.percentile(levels, 10))
        high = float(np.percentile(levels, 90))
        dynamic = max(0.0, high - low)
        threshold = low + min(
            self.config.maximum_snr_db,
            max(self.config.minimum_snr_db, dynamic * 0.35),
        )
        threshold = max(self.config.minimum_threshold_dbfs, threshold)
        active = levels >= threshold
        starts_ms = starts_samples * 1000.0 / SAMPLE_RATE
        intervals = _frames_to_intervals(
            starts_ms=starts_ms,
            active=active,
            frame_ms=self.config.frame_ms,
            min_speech_ms=self.config.minimum_speech_ms,
            min_silence_ms=self.config.minimum_silence_ms,
            padding_ms=self.config.padding_ms,
            scope_duration_ms=scope_duration_ms,
        )
        return _finalize_activity(
            intervals=intervals,
            scope_duration_ms=scope_duration_ms,
            transcript_cues=transcript_cues,
            method="energy",
            confidence="low",
            threshold_dbfs=threshold,
            noise_floor_dbfs=low,
            warnings=(),
            backend=self.backend,
            probability_threshold=None,
        )

    def levels_and_starts(self) -> tuple[np.ndarray, np.ndarray]:
        return self._collector.result()


class SileroActivityDetector:
    """Local Silero VAD adapter using the pinned ONNX artifact and CPU provider."""

    backend = "silero"

    def __init__(self, model_path: Path, config: ActivityConfig | None = None) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise AnalysisError("onnxruntime is required for the Silero VAD backend") from exc
        if not model_path.exists():
            raise AnalysisError(f"Silero VAD model does not exist: {model_path}")
        try:
            self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        except Exception as exc:  # pragma: no cover - backend-specific load failure
            raise AnalysisError(f"Could not load Silero VAD model: {model_path}") from exc
        input_names = {item.name for item in self.session.get_inputs()}
        if not {"input", "state", "sr"}.issubset(input_names):
            raise AnalysisError("Silero VAD model has an unsupported input signature")
        self.config = config or ActivityConfig()
        self.probability_threshold = self.config.neural_speech_threshold
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._pending = np.empty(0, dtype=np.float32)
        self._pending_start = 0
        self._sample_cursor = 0
        self._probabilities: list[float] = []
        self._probability_starts: list[int] = []
        self._collector = _LevelCollector(self.config.frame_ms)

    def add_chunk(self, mono: np.ndarray) -> None:
        if mono.ndim != 1 or mono.size == 0:
            return
        self._collector.add(mono)
        if self._pending.size:
            samples = np.concatenate([self._pending, mono])
            start = self._pending_start
        else:
            samples = mono
            start = self._sample_cursor
        usable = (samples.size // NEURAL_FRAME_SAMPLES) * NEURAL_FRAME_SAMPLES
        for index in range(usable // NEURAL_FRAME_SAMPLES):
            frame = samples[index * NEURAL_FRAME_SAMPLES : (index + 1) * NEURAL_FRAME_SAMPLES]
            self._run_frame(frame, start + index * NEURAL_FRAME_SAMPLES)
        self._pending = samples[usable:].copy()
        self._pending_start = start + usable
        self._sample_cursor = start + samples.size

    def finish(
        self,
        scope_duration_ms: int,
        transcript_cues: tuple[TranscriptCue, ...] = (),
    ) -> ActivityResult:
        warnings: list[str] = []
        if self._pending.size:
            padded = np.pad(self._pending, (0, NEURAL_FRAME_SAMPLES - self._pending.size))
            self._run_frame(padded, self._pending_start)
            self._pending = np.empty(0, dtype=np.float32)
        if not self._probabilities:
            warnings.append("Silero VAD produced no complete 32 ms frames")
            levels, _ = self._collector.result()
            noise_floor = float(np.percentile(levels, 10)) if levels.size else -120.0
            return _finalize_activity(
                intervals=(),
                scope_duration_ms=scope_duration_ms,
                transcript_cues=transcript_cues,
                method="silero-vad",
                confidence="low",
                threshold_dbfs=-120.0,
                noise_floor_dbfs=noise_floor,
                warnings=tuple(warnings),
                backend=self.backend,
                probability_threshold=self.probability_threshold,
            )

        probabilities = np.asarray(self._probabilities, dtype=np.float64)
        starts_ms = np.asarray(self._probability_starts, dtype=np.float64) * 1000.0 / SAMPLE_RATE
        active = probabilities >= self.config.neural_speech_threshold
        # Retain a short release tail so a single lower-confidence frame does
        # not split a speech segment.
        for index in range(1, active.size):
            if active[index - 1] and probabilities[index] >= self.config.neural_release_threshold:
                active[index] = True
        intervals = _frames_to_intervals(
            starts_ms=starts_ms,
            active=active,
            frame_ms=NEURAL_FRAME_MS,
            min_speech_ms=self.config.minimum_speech_ms,
            min_silence_ms=self.config.minimum_silence_ms,
            padding_ms=self.config.padding_ms,
            scope_duration_ms=scope_duration_ms,
        )
        levels, _ = self._collector.result()
        noise_floor = float(np.percentile(levels, 10)) if levels.size else -120.0
        return _finalize_activity(
            intervals=intervals,
            scope_duration_ms=scope_duration_ms,
            transcript_cues=transcript_cues,
            method="silero-vad",
            confidence="medium",
            threshold_dbfs=-120.0,
            noise_floor_dbfs=noise_floor,
            warnings=tuple(warnings),
            backend=self.backend,
            probability_threshold=self.probability_threshold,
        )

    def levels_and_starts(self) -> tuple[np.ndarray, np.ndarray]:
        return self._collector.result()

    def _run_frame(self, frame: np.ndarray, start_sample: int) -> None:
        output, next_state = self.session.run(
            ["output", "stateN"],
            {
                "input": frame.astype(np.float32, copy=False).reshape(1, -1),
                "state": self._state,
                "sr": np.array(SAMPLE_RATE, dtype=np.int64),
            },
        )
        self._state = np.asarray(next_state, dtype=np.float32)
        self._probabilities.append(float(np.asarray(output).reshape(-1)[0]))
        self._probability_starts.append(start_sample)


def create_activity_detector(
    backend: str = "auto",
    model_path: Path | None = None,
) -> tuple[ActivityDetector, dict[str, object]]:
    """Select a local VAD backend without downloading anything at analysis time."""
    requested = backend.strip().lower()
    if requested not in {"auto", "silero", "energy"}:
        raise AnalysisError("--vad-backend must be auto, silero, or energy")
    if requested == "energy":
        detector = EnergyActivityDetector()
        return detector, _detector_metadata(detector, None, "explicit energy fallback")

    resolved = _resolve_model_path(model_path)
    if resolved is None and requested == "silero":
        raise AnalysisError("Silero VAD was requested but no local model was found")
    reason = "local model not found"
    try:
        detector = SileroActivityDetector(resolved) if resolved is not None else None
    except (AnalysisError, ImportError) as exc:
        if requested == "silero":
            raise AnalysisError(f"Silero VAD was requested but is unavailable: {exc}") from exc
        detector = None
        reason = str(exc)
    else:
        if detector is not None:
            reason = "pinned local ONNX model"
    if detector is not None:
        return detector, _detector_metadata(detector, resolved, reason)
    fallback = EnergyActivityDetector()
    return fallback, _detector_metadata(
        fallback,
        None,
        f"Silero unavailable; energy fallback used ({reason})",
    )


def _resolve_model_path(model_path: Path | None) -> Path | None:
    if model_path is not None:
        candidate = model_path.expanduser()
        return candidate.resolve() if candidate.is_file() else None
    candidates: list[Path] = []
    environment_path = os.environ.get("SENATE_AUDIO_VAD_MODEL")
    if environment_path:
        candidates.append(Path(environment_path))
    candidates.append(Path.cwd() / "models" / "silero_vad_16k_op15.onnx")
    candidates.append(Path(__file__).resolve().parents[1] / "models" / "silero_vad_16k_op15.onnx")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _detector_metadata(
    detector: ActivityDetector,
    model_path: Path | None,
    reason: str,
) -> dict[str, object]:
    return {
        "backend": detector.backend,
        "model_id": "silero_vad_16k_op15" if detector.backend == "silero" else "energy-fallback-v0",
        "model_path": str(model_path) if model_path else None,
        "model_sha256": sha256_file(model_path) if model_path else None,
        "probability_threshold": detector.probability_threshold,
        "selection_reason": reason,
    }


def _empty_activity(
    *,
    backend: str,
    method: str,
    threshold_dbfs: float,
    warning: str,
    probability_threshold: float | None = None,
) -> ActivityResult:
    return ActivityResult(
        intervals=(),
        method=method,
        speech_present="no",
        speech_duration_ms=0,
        speech_ratio=0.0,
        confidence="low",
        threshold_dbfs=threshold_dbfs,
        noise_floor_dbfs=-120.0,
        warnings=(warning,),
        backend=backend,
        probability_threshold=probability_threshold,
    )


def _finalize_activity(
    *,
    intervals: tuple[SpeechInterval, ...],
    scope_duration_ms: int,
    transcript_cues: tuple[TranscriptCue, ...],
    method: str,
    confidence: str,
    threshold_dbfs: float,
    noise_floor_dbfs: float,
    warnings: tuple[str, ...],
    backend: str,
    probability_threshold: float | None,
) -> ActivityResult:
    warnings_list = list(warnings)
    method_name = method
    confidence_name = confidence
    if transcript_cues:
        transcript_ms = sum(max(0, cue.end_ms - cue.start_ms) for cue in transcript_cues)
        if not intervals and transcript_ms >= 1000:
            intervals = tuple(
                SpeechInterval(cue.start_ms, min(cue.end_ms, scope_duration_ms))
                for cue in transcript_cues
                if cue.start_ms < scope_duration_ms
            )
            method_name += "+transcript-fallback"
            confidence_name = "low"
            warnings_list.append(
                "VAD found no reliable speech; transcript intervals were used as "
                "low-confidence fallback"
            )
        elif intervals:
            intervals = _refine_with_transcript(intervals, transcript_cues, scope_duration_ms)
            method_name += "+transcript"
    speech_ms = sum(interval.end_ms - interval.start_ms for interval in intervals)
    ratio = speech_ms / max(1, scope_duration_ms)
    transcript_ms = sum(max(0, cue.end_ms - cue.start_ms) for cue in transcript_cues)
    if speech_ms >= 1000 and ratio >= 0.005:
        speech_present: Literal["yes", "no", "uncertain"] = "yes"
    elif transcript_ms >= 1000:
        speech_present = "uncertain"
    else:
        speech_present = "no"
    if not intervals and speech_present != "no":
        warnings_list.append(
            "Speech may be present but no reliable activity interval could be localized"
        )
    return ActivityResult(
        intervals=intervals,
        method=method_name,
        speech_present=speech_present,
        speech_duration_ms=speech_ms,
        speech_ratio=ratio,
        confidence=confidence_name,  # type: ignore[arg-type]
        threshold_dbfs=threshold_dbfs,
        noise_floor_dbfs=noise_floor_dbfs,
        warnings=tuple(dict.fromkeys(warnings_list)),
        backend=backend,
        probability_threshold=probability_threshold,
    )


def _frames_to_intervals(
    *,
    starts_ms: np.ndarray,
    active: np.ndarray,
    frame_ms: int,
    min_speech_ms: int,
    min_silence_ms: int,
    padding_ms: int,
    scope_duration_ms: int,
) -> tuple[SpeechInterval, ...]:
    if active.size == 0 or not active.any():
        return ()
    padded = np.pad(active.astype(bool), (1, 1), constant_values=False)
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    raw: list[SpeechInterval] = []
    for start_index, end_index in changes.reshape(-1, 2):
        start = max(0, int(starts_ms[start_index]) - padding_ms)
        end = min(
            scope_duration_ms,
            int(starts_ms[end_index - 1]) + frame_ms + padding_ms,
        )
        if end > start:
            raw.append(SpeechInterval(start, end))
    merged: list[SpeechInterval] = []
    for interval in raw:
        if not merged or interval.start_ms - merged[-1].end_ms >= min_silence_ms:
            merged.append(interval)
        else:
            merged[-1] = SpeechInterval(
                merged[-1].start_ms, max(merged[-1].end_ms, interval.end_ms)
            )
    minimum = max(frame_ms, min_speech_ms)
    return tuple(interval for interval in merged if interval.end_ms - interval.start_ms >= minimum)


def _refine_with_transcript(
    intervals: tuple[SpeechInterval, ...],
    cues: tuple[TranscriptCue, ...],
    scope_duration_ms: int,
) -> tuple[SpeechInterval, ...]:
    accepted: list[SpeechInterval] = []
    for cue in cues:
        expanded_start = max(0, cue.start_ms - 500)
        expanded_end = min(scope_duration_ms, cue.end_ms + 500)
        for interval in intervals:
            overlap = min(interval.end_ms, expanded_end) - max(interval.start_ms, expanded_start)
            if overlap > 0:
                accepted.append(
                    SpeechInterval(
                        max(interval.start_ms, expanded_start),
                        min(interval.end_ms, expanded_end),
                    )
                )
    return merge_intervals(tuple(accepted))


def merge_intervals(
    intervals: tuple[SpeechInterval, ...] | list[SpeechInterval],
) -> tuple[SpeechInterval, ...]:
    if not intervals:
        return ()
    ordered = sorted(intervals, key=lambda item: (item.start_ms, item.end_ms))
    merged = [ordered[0]]
    for interval in ordered[1:]:
        previous = merged[-1]
        if interval.start_ms <= previous.end_ms:
            merged[-1] = SpeechInterval(previous.start_ms, max(previous.end_ms, interval.end_ms))
        else:
            merged.append(interval)
    return tuple(merged)


def interval_duration_ms(intervals: tuple[SpeechInterval, ...]) -> int:
    return sum(interval.end_ms - interval.start_ms for interval in intervals)
