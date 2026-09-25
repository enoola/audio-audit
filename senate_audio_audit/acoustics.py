from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .activity import ActivityDetector, ActivityResult, EnergyActivityDetector, SpeechInterval
from .models import AcousticMetrics, TranscriptCue
from .transcripts import count_words

SAMPLE_RATE = 16_000


@dataclass(frozen=True, slots=True)
class CueRate:
    index: int
    start_ms: int
    end_ms: int
    words_per_minute: float
    text: str


@dataclass(frozen=True, slots=True)
class LoudWindow:
    start_ms: int
    end_ms: int
    active_duration_ms: int
    relative_level_db: float
    level_signal: float
    speech_rate_wpm: float | None
    rate_ratio: float | None
    high_arousal_signal: float


@dataclass(frozen=True, slots=True)
class AcousticAnalysis:
    metrics: AcousticMetrics
    activity: ActivityResult
    frame_levels_dbfs: np.ndarray
    frame_start_ms: np.ndarray
    active_frame_mask: np.ndarray
    active_median_dbfs: float
    cue_rates: tuple[CueRate, ...]
    median_cue_rate_wpm: float | None
    loud_windows: tuple[LoudWindow, ...]
    channel_strategy: str
    channel_index: int | None
    channel_metrics: dict[str, float]
    f0_median_hz: float | None
    warnings: tuple[str, ...]


class AcousticStreamAnalyzer:
    def __init__(
        self,
        *,
        scope_start_ms: int,
        scope_end_ms: int,
        channel_strategy: str,
        channel_index: int | None,
        channel_metrics: dict[str, float],
        activity_detector: ActivityDetector | None = None,
    ) -> None:
        self.scope_start_ms = scope_start_ms
        self.scope_end_ms = scope_end_ms
        self.scope_duration_ms = max(1, scope_end_ms - scope_start_ms)
        self.channel_strategy = channel_strategy
        self.channel_index = channel_index
        self.channel_metrics = dict(channel_metrics)
        self.activity_detector = activity_detector or EnergyActivityDetector()
        self._sample_count = 0
        self._clipping_count = 0
        self._f0_values: list[float] = []

    def add_chunk(self, mono: np.ndarray, source_pcm: np.ndarray | None = None) -> None:
        if mono.ndim != 1 or mono.size == 0:
            return
        self.activity_detector.add_chunk(mono)
        clipping_source = source_pcm if source_pcm is not None else mono
        self._sample_count += int(clipping_source.size)
        self._clipping_count += int(np.count_nonzero(np.abs(clipping_source) >= 0.999))
        self._collect_f0(mono)

    def finish(self, transcript: tuple[TranscriptCue, ...]) -> AcousticAnalysis:
        activity = self.activity_detector.finish(self.scope_duration_ms, transcript)
        levels, starts_samples = self.activity_detector.levels_and_starts()
        starts_ms = self.scope_start_ms + starts_samples * 1000.0 / SAMPLE_RATE
        active_mask = _active_frame_mask(starts_ms, levels.shape[0], activity.intervals)
        active_levels = levels[active_mask] if np.any(active_mask) else np.empty(0)
        if active_levels.size < 10:
            threshold = activity.noise_floor_dbfs + 6.0
            active_levels = levels[levels >= threshold]
            active_mask = levels >= threshold
            if active_levels.size:
                activity = ActivityResult(
                    intervals=activity.intervals,
                    method=f"{activity.method}+fallback-level-mask",
                    speech_present=activity.speech_present,
                    speech_duration_ms=activity.speech_duration_ms,
                    speech_ratio=activity.speech_ratio,
                    confidence="low",
                    threshold_dbfs=threshold,
                    noise_floor_dbfs=activity.noise_floor_dbfs,
                    warnings=activity.warnings
                    + ("Activity intervals were unavailable; a level mask was used",),
                    backend=activity.backend,
                    probability_threshold=activity.probability_threshold,
                )

        if active_levels.size:
            active_median = float(np.median(active_levels))
            active_p95 = float(np.percentile(active_levels, 95))
        else:
            active_median = float(np.percentile(levels, 50)) if levels.size else -120.0
            active_p95 = float(np.percentile(levels, 95)) if levels.size else -120.0

        cue_rates = _cue_rates(transcript, self.scope_start_ms, self.scope_end_ms)
        rate_values = [item.words_per_minute for item in cue_rates if item.words_per_minute > 0]
        median_rate = float(np.median(rate_values)) if rate_values else None
        active_minutes = activity.active_seconds / 60.0
        word_count = sum(count_words(cue.text) for cue in transcript)
        speech_rate = word_count / active_minutes if active_minutes >= 0.1 and word_count else None
        pause_ratio = max(0.0, 1.0 - activity.speech_ratio)
        clipping_fraction = self._clipping_count / max(1, self._sample_count)
        f0_values = np.asarray(self._f0_values, dtype=np.float64)
        f0_median = float(np.median(f0_values)) if f0_values.size >= 3 else None
        f0_variation = _semitone_std(f0_values) if f0_values.size >= 3 else None

        metrics = AcousticMetrics(
            active_speech_median_dbfs=round(active_median, 3),
            active_speech_p95_dbfs=round(active_p95, 3),
            clipping_fraction=round(clipping_fraction, 8),
            median_speech_rate_wpm=round(speech_rate, 3) if speech_rate is not None else None,
            median_pause_ratio=round(pause_ratio, 6),
            f0_variation_semitones=round(f0_variation, 3) if f0_variation is not None else None,
            overlap_fraction=None,
            anonymous_speaker_count=None,
            active_speech_duration_ms=activity.speech_duration_ms,
            speech_ratio=round(activity.speech_ratio, 6),
            activity_method=activity.method,
            activity_threshold_dbfs=round(activity.threshold_dbfs, 3),
            activity_noise_floor_dbfs=round(activity.noise_floor_dbfs, 3),
            channel_strategy=self.channel_strategy,
            f0_median_hz=round(f0_median, 3) if f0_median is not None else None,
            activity_backend=activity.backend,
            activity_probability_threshold=activity.probability_threshold,
        )
        loud_windows = _detect_loud_windows(
            starts_ms=starts_ms,
            levels=levels,
            active_mask=active_mask,
            active_median_dbfs=active_median,
            scope_start_ms=self.scope_start_ms,
            scope_end_ms=self.scope_end_ms,
            cue_rates=cue_rates,
            median_cue_rate_wpm=median_rate,
        )
        warnings = list(activity.warnings)
        if transcript:
            warnings.append("Speaker overlap is unavailable until diarization is enabled")
        else:
            warnings.extend(
                (
                    "No timed transcript is available; speech rate and text behavior "
                    "are not assessed",
                    "Speaker overlap is unavailable until diarization is enabled",
                )
            )
        return AcousticAnalysis(
            metrics=metrics,
            activity=activity,
            frame_levels_dbfs=levels,
            frame_start_ms=starts_ms,
            active_frame_mask=active_mask,
            active_median_dbfs=active_median,
            cue_rates=cue_rates,
            median_cue_rate_wpm=median_rate,
            loud_windows=loud_windows,
            channel_strategy=self.channel_strategy,
            channel_index=self.channel_index,
            channel_metrics=self.channel_metrics,
            f0_median_hz=round(f0_median, 3) if f0_median is not None else None,
            warnings=tuple(warnings),
        )

    def _collect_f0(self, mono: np.ndarray) -> None:
        frame_size = 640  # 40 ms
        frame_count = mono.size // frame_size
        if frame_count <= 0:
            return
        frames = mono[: frame_count * frame_size].reshape(frame_count, frame_size)
        rms = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1) + 1e-12)
        if rms.size < 5:
            return
        threshold = float(np.percentile(rms, 85))
        candidates = np.flatnonzero(rms >= threshold)
        max_per_chunk = 16
        if candidates.size > max_per_chunk:
            positions = np.linspace(0, candidates.size - 1, max_per_chunk, dtype=int)
            candidates = candidates[positions]
        for index in candidates:
            value = _estimate_f0(frames[index])
            if value is not None:
                self._f0_values.append(value)
        if len(self._f0_values) > 3000:
            step = len(self._f0_values) / 3000.0
            self._f0_values = [self._f0_values[int(i * step)] for i in range(3000)]


def _active_frame_mask(
    starts_ms: np.ndarray,
    frame_count: int,
    intervals: tuple[SpeechInterval, ...],
) -> np.ndarray:
    mask = np.zeros(frame_count, dtype=bool)
    if not intervals:
        return mask
    for interval in intervals:
        mask |= (starts_ms >= interval.start_ms) & (starts_ms < interval.end_ms)
    return mask


def _cue_rates(
    cues: tuple[TranscriptCue, ...],
    scope_start_ms: int,
    scope_end_ms: int,
) -> tuple[CueRate, ...]:
    result: list[CueRate] = []
    for cue in cues:
        start = max(scope_start_ms, cue.start_ms)
        end = min(scope_end_ms, cue.end_ms)
        if end <= start:
            continue
        words = count_words(cue.text)
        wpm = words / ((end - start) / 60_000.0) if words else 0.0
        result.append(
            CueRate(
                index=cue.index,
                start_ms=start,
                end_ms=end,
                words_per_minute=wpm,
                text=cue.text,
            )
        )
    return tuple(result)


def _detect_loud_windows(
    *,
    starts_ms: np.ndarray,
    levels: np.ndarray,
    active_mask: np.ndarray,
    active_median_dbfs: float,
    scope_start_ms: int,
    scope_end_ms: int,
    cue_rates: tuple[CueRate, ...],
    median_cue_rate_wpm: float | None,
) -> tuple[LoudWindow, ...]:
    if levels.size == 0:
        return ()
    window_ms = 5_000
    hop_ms = 2_500
    result: list[LoudWindow] = []
    for window_start in range(scope_start_ms, scope_end_ms, hop_ms):
        window_end = min(scope_end_ms, window_start + window_ms)
        if window_end <= window_start:
            break
        mask = active_mask & (starts_ms >= window_start) & (starts_ms < window_end)
        count = int(np.count_nonzero(mask))
        if count < max(5, window_ms // 30 // 2):
            continue
        window_levels = levels[mask]
        p90 = float(np.percentile(window_levels, 90))
        delta = p90 - active_median_dbfs
        if delta < 6.0:
            continue
        active_ms = count * 30
        words = sum(
            item.words_per_minute
            * ((min(window_end, item.end_ms) - max(window_start, item.start_ms)) / 60_000.0)
            for item in cue_rates
            if min(window_end, item.end_ms) > max(window_start, item.start_ms)
        )
        wpm = words / (active_ms / 60_000.0) if active_ms else None
        rate_ratio = wpm / median_cue_rate_wpm if wpm is not None and median_cue_rate_wpm else None
        level_signal = max(0.0, min(1.0, (delta - 4.0) / 10.0))
        high_signal = 0.0
        if rate_ratio is not None and rate_ratio >= 1.15:
            high_signal = max(
                0.0, min(1.0, 0.65 * level_signal + 0.35 * min(1.0, (rate_ratio - 1.0) / 0.8))
            )
        result.append(
            LoudWindow(
                start_ms=window_start,
                end_ms=window_end,
                active_duration_ms=active_ms,
                relative_level_db=delta,
                level_signal=level_signal,
                speech_rate_wpm=wpm,
                rate_ratio=rate_ratio,
                high_arousal_signal=high_signal,
            )
        )
    return _merge_loud_windows(result)


def _merge_loud_windows(windows: list[LoudWindow]) -> tuple[LoudWindow, ...]:
    if not windows:
        return ()
    ordered = sorted(windows, key=lambda item: item.start_ms)
    merged: list[LoudWindow] = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        if item.start_ms - previous.end_ms <= 2_000:
            total_active = previous.active_duration_ms + item.active_duration_ms
            weighted_level = (
                previous.relative_level_db * previous.active_duration_ms
                + item.relative_level_db * item.active_duration_ms
            ) / total_active
            merged[-1] = LoudWindow(
                start_ms=previous.start_ms,
                end_ms=item.end_ms,
                active_duration_ms=total_active,
                relative_level_db=weighted_level,
                level_signal=max(previous.level_signal, item.level_signal),
                speech_rate_wpm=item.speech_rate_wpm or previous.speech_rate_wpm,
                rate_ratio=item.rate_ratio or previous.rate_ratio,
                high_arousal_signal=max(previous.high_arousal_signal, item.high_arousal_signal),
            )
        else:
            merged.append(item)
    return tuple(merged)


def _estimate_f0(frame: np.ndarray) -> float | None:
    signal = frame.astype(np.float64)
    signal -= np.mean(signal)
    rms = float(np.sqrt(np.mean(np.square(signal))))
    if rms < 0.005:
        return None
    correlation = np.correlate(signal, signal, mode="full")[signal.size - 1 :]
    if correlation[0] <= 0:
        return None
    min_lag = max(2, int(SAMPLE_RATE / 400.0))
    max_lag = min(signal.size - 2, int(SAMPLE_RATE / 65.0))
    if max_lag <= min_lag:
        return None
    segment = correlation[min_lag : max_lag + 1]
    lag = int(np.argmax(segment)) + min_lag
    if correlation[lag] / correlation[0] < 0.30:
        return None
    return float(SAMPLE_RATE / lag)


def _semitone_std(values: np.ndarray) -> float:
    positive = values[(values > 0) & np.isfinite(values)]
    if positive.size < 3:
        return math.nan
    semitones = 12.0 * np.log2(positive / np.median(positive))
    return float(np.std(semitones, ddof=1))


def acoustic_metrics_to_dict(metrics: AcousticMetrics) -> dict[str, Any]:
    return {
        "active_speech_median_dbfs": metrics.active_speech_median_dbfs,
        "active_speech_p95_dbfs": metrics.active_speech_p95_dbfs,
        "clipping_fraction": metrics.clipping_fraction,
        "median_speech_rate_wpm": metrics.median_speech_rate_wpm,
        "median_pause_ratio": metrics.median_pause_ratio,
        "f0_variation_semitones": metrics.f0_variation_semitones,
        "overlap_fraction": metrics.overlap_fraction,
        "anonymous_speaker_count": metrics.anonymous_speaker_count,
        "active_speech_duration_ms": metrics.active_speech_duration_ms,
        "speech_ratio": metrics.speech_ratio,
        "activity_method": metrics.activity_method,
        "activity_threshold_dbfs": metrics.activity_threshold_dbfs,
        "activity_noise_floor_dbfs": metrics.activity_noise_floor_dbfs,
        "channel_strategy": metrics.channel_strategy,
        "f0_median_hz": metrics.f0_median_hz,
        "activity_backend": metrics.activity_backend,
        "activity_probability_threshold": metrics.activity_probability_threshold,
    }
