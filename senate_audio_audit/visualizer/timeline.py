"""Display-only timeline sidecar for the local visualizer.

The visualizer needs a time series to draw, but a metrics snapshot deliberately
stores only aggregates and events. This module produces a compact, regenerable
rendering aid: a dBFS loudness envelope and a speech-activity strip, both reduced
to fixed-width buckets so that an 89-minute recording stays a small JSON payload.

Nothing computed here is a metric. The envelope reuses the same 30 ms RMS frames
the analysis pass already computes, so it is consistent with
``active_speech_median_dbfs`` by construction, and no value from this module may
ever be written into ``*.metrics.json`` or quoted as a finding.

Bucket zero corresponds to ``scope.start_ms`` of the analyzed scope, while event
timestamps in the snapshot are expressed in original-media time. The emitted
``scope_start_ms``/``scope_end_ms`` let a renderer place these buckets on the full
media timeline without guessing.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..activity import SAMPLE_RATE, ActivityConfig, create_activity_detector
from ..artifacts import MetricsPaths
from ..errors import AnalysisError, ArtifactError
from ..media import (
    choose_analysis_channel,
    iter_pcm_chunks,
    probe_media,
    select_or_average_mono,
)
from ..models import SpeechInterval, TranscriptCue
from ..transcripts import discover_transcript
from ..util import atomic_write_text, load_json, pretty_json, stable_hash

TIMELINE_SCHEMA_VERSION = "0.1.0"
ARTIFACT_ROLE = "visualizer_timeline"
TIMELINE_SUFFIX = ".timeline.json"

DEFAULT_BUCKET_MS = 1_000
MIN_BUCKET_MS = 50
MAX_BUCKET_MS = 60_000

#: dBFS is stored as an integer to keep the payload small; decode with ``value / scale``.
ENVELOPE_SCALE = 10
ENVELOPE_FLOOR_DBFS = -120.0
ENVELOPE_CEILING_DBFS = 0.0
#: Fixed rendering domain advertised to the browser so recordings stay comparable.
RENDER_FLOOR_DBFS = -80.0

#: Speech fraction per bucket is stored as 0-255.
ACTIVITY_SCALE = 255

#: Two independent lanes may disagree by this much before the UI is told to warn.
SPEECH_RATIO_TOLERANCE = 0.02

#: ``active_speech_median_dbfs`` is a per-frame median over active speech, while the
#: envelope is a per-bucket energy average over all audio. Measured on the reference
#: recording the two are not interchangeable, so the reference travels with an
#: explicit caveat instead of being drawn as if it were the envelope's own median.
ENVELOPE_REFERENCE_LABEL = "median active-speech level (30 ms frames)"
ENVELOPE_REFERENCE_NOTE = (
    "This reference is a per-frame median over active speech; the envelope is a "
    "per-bucket energy average over all audio. The two are not directly comparable."
)

_FLOOR_CODE = int(round(ENVELOPE_FLOOR_DBFS * ENVELOPE_SCALE))


@dataclass(frozen=True, slots=True)
class TimelineConfig:
    """Display-only settings for the sidecar. None of these affect analysis."""

    bucket_ms: int = DEFAULT_BUCKET_MS
    activity_backend: str = "match"
    chunk_seconds: float = 60.0

    def validated(self) -> TimelineConfig:
        if not MIN_BUCKET_MS <= int(self.bucket_ms) <= MAX_BUCKET_MS:
            raise ArtifactError(
                f"bucket_ms must be between {MIN_BUCKET_MS} and {MAX_BUCKET_MS}, "
                f"got {self.bucket_ms}"
            )
        if self.activity_backend not in {"match", "auto", "energy", "silero"}:
            raise ArtifactError("timeline activity backend must be match, auto, energy, or silero")
        return self


@dataclass(frozen=True, slots=True)
class TimelineArtifact:
    payload: dict[str, Any]
    path: Path
    rebuilt: bool


def bucket_count(duration_ms: int, bucket_ms: int) -> int:
    """Number of buckets needed to cover ``duration_ms``; the last one may be partial."""
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")
    return max(1, math.ceil(max(0, int(duration_ms)) / int(bucket_ms)))


def scale_dbfs(value: float) -> int:
    """Clamp a dBFS reading to the representable domain and encode it as an integer."""
    if not math.isfinite(value):
        return _FLOOR_CODE
    clamped = min(ENVELOPE_CEILING_DBFS, max(ENVELOPE_FLOOR_DBFS, value))
    return int(round(clamped * ENVELOPE_SCALE))


def reduce_envelope(
    levels_dbfs: np.ndarray,
    starts_samples: np.ndarray,
    *,
    offset_ms: int,
    duration_ms: int,
    bucket_ms: int,
    total_buckets: int,
) -> list[int]:
    """Reduce fixed-size RMS frames to one energy-averaged dBFS value per bucket.

    Frames are combined as mean power rather than as a mean of dB values. The mean
    of a bucket's power is exactly the RMS level of that time span, whereas an
    arithmetic mean of dB understates short loud transients inside a quiet bucket.

    ``offset_ms`` is the original-media time of the first decoded sample, so frame
    positions land in the same coordinate system as event timestamps.
    """
    values = [_FLOOR_CODE] * total_buckets
    if levels_dbfs.size == 0 or total_buckets <= 0:
        return values

    frames = np.asarray(levels_dbfs, dtype=np.float64)
    starts_ms = int(offset_ms) + np.asarray(starts_samples, dtype=np.float64) * 1000.0 / (
        SAMPLE_RATE
    )
    index = np.floor(starts_ms / bucket_ms).astype(np.int64)
    np.clip(index, 0, total_buckets - 1, out=index)

    power = np.power(10.0, frames / 10.0)
    power = np.where(np.isfinite(power), power, 0.0)

    sums = np.bincount(index, weights=power, minlength=total_buckets)[:total_buckets]
    counts = np.bincount(index, minlength=total_buckets)[:total_buckets]

    populated = counts > 0
    mean_power = np.zeros(total_buckets, dtype=np.float64)
    mean_power[populated] = sums[populated] / counts[populated]
    with np.errstate(divide="ignore", invalid="ignore"):
        decibels = 10.0 * np.log10(np.maximum(mean_power, 1e-30))
    decibels = np.where(populated & np.isfinite(decibels), decibels, ENVELOPE_FLOOR_DBFS)
    return [scale_dbfs(float(value)) for value in decibels]


def activity_buckets(
    intervals: Iterable[SpeechInterval],
    *,
    duration_ms: int,
    bucket_ms: int,
    total_buckets: int,
) -> list[int]:
    """Encode the speech fraction of each bucket as 0-255.

    The final bucket of a scope whose duration is not a multiple of ``bucket_ms`` is
    normalized against its own shorter span, so a partial bucket is not under-reported.
    """
    covered = [0] * total_buckets
    for interval in intervals:
        start = max(0, int(interval.start_ms))
        end = min(int(duration_ms), int(interval.end_ms))
        if end <= start:
            continue
        first = max(0, start // bucket_ms)
        last = min(total_buckets - 1, (end - 1) // bucket_ms)
        for index in range(first, last + 1):
            bucket_start = index * bucket_ms
            bucket_end = min(int(duration_ms), bucket_start + bucket_ms)
            overlap = min(end, bucket_end) - max(start, bucket_start)
            if overlap > 0:
                covered[index] += overlap

    values: list[int] = []
    for index in range(total_buckets):
        bucket_start = index * bucket_ms
        span = min(int(duration_ms), bucket_start + bucket_ms) - bucket_start
        if span <= 0:
            values.append(0)
            continue
        values.append(int(round(min(1.0, covered[index] / span) * ACTIVITY_SCALE)))
    return values


def timeline_path(paths: MetricsPaths) -> Path:
    """Sidecar location beside the metrics pair, derived from the latest JSON path."""
    name = paths.latest_json.name
    stem = name[: -len(".metrics.json")] if name.endswith(".metrics.json") else name
    return paths.latest_json.with_name(stem + TIMELINE_SUFFIX)


def timeline_cache_key(
    *,
    source_sha256: str,
    run_id: str,
    bucket_ms: int,
    activity_backend: str,
) -> str:
    return stable_hash(
        {
            "source_sha256": source_sha256,
            "run_id": run_id,
            "bucket_ms": int(bucket_ms),
            "activity_backend": activity_backend,
            "timeline_version": TIMELINE_SCHEMA_VERSION,
        },
        length=32,
    )


def _unavailable_payload(
    snapshot: dict[str, Any],
    config: TimelineConfig,
    *,
    duration_ms: int,
    total_buckets: int,
    warnings: list[str],
    cache_key: str,
) -> dict[str, Any]:
    empty = [0] * total_buckets
    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "artifact_role": ARTIFACT_ROLE,
        "cache_key": cache_key,
        "run_id": snapshot.get("run_id"),
        "run_key": snapshot.get("run_key"),
        "source_sha256": str(snapshot.get("source", {}).get("sha256", "")),
        "scope_start_ms": 0,
        "scope_end_ms": int(duration_ms),
        "duration_ms": int(duration_ms),
        "bucket_ms": int(config.bucket_ms),
        "bucket_count": total_buckets,
        "envelope": {
            "available": False,
            "unit": "dbfs",
            "scale": ENVELOPE_SCALE,
            "floor_dbfs": RENDER_FLOOR_DBFS,
            "reference_dbfs": _envelope_reference(snapshot),
            "reference_label": ENVELOPE_REFERENCE_LABEL,
            "reference_note": ENVELOPE_REFERENCE_NOTE,
            "values": empty,
        },
        "activity": {
            "available": False,
            "method": None,
            "backend": None,
            "speech_ratio": None,
            "agrees_with_snapshot": None,
            "values": list(empty),
        },
        "warnings": warnings,
        "generator": {
            "timeline_version": TIMELINE_SCHEMA_VERSION,
            "sample_rate": SAMPLE_RATE,
            "frame_ms": ActivityConfig().frame_ms,
        },
    }


def _resolve_backend(
    snapshot: dict[str, Any], config: TimelineConfig
) -> tuple[str, str | None, list[str]]:
    """Choose a VAD backend that does not contradict the metrics above the lane.

    ``match`` follows whatever the snapshot recorded: an energy-analyzed recording
    forces the cheap energy detector, and a Silero-analyzed recording prefers Silero
    but degrades to energy with a warning rather than failing the whole command.
    """
    snapshot_backend = str(snapshot.get("quality", {}).get("activity_backend") or "energy")
    requested = config.activity_backend
    if requested != "match":
        return requested, None, []
    if snapshot_backend == "silero":
        # "auto" prefers the pinned Silero model but falls back without raising.
        return "auto", "silero", []
    return "energy", "energy", []


def _snapshot_speech_ratio(snapshot: dict[str, Any]) -> float | None:
    measurements = snapshot.get("measurements") or {}
    value = measurements.get("speech_ratio")
    if value is None:
        value = (snapshot.get("summary") or {}).get("speech_ratio")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _transcript_cues(snapshot: dict[str, Any], media_path: Path) -> tuple[TranscriptCue, ...]:
    """Reload the cues the analysis used so the activity lane cannot drift from it."""
    recorded = str((snapshot.get("quality") or {}).get("transcript_path") or "")
    if not recorded:
        return ()
    try:
        transcript = discover_transcript(media_path, explicit_path=Path(recorded))
    except AnalysisError:
        return ()
    except Exception:  # noqa: BLE001 - a missing sidecar must not break the lane
        return ()
    return transcript.cues


def build_timeline(
    media_path: Path,
    snapshot: dict[str, Any],
    config: TimelineConfig | None = None,
    *,
    vad_model: Path | None = None,
) -> dict[str, Any]:
    """Decode the analyzed scope once and return a sidecar payload.

    The envelope and the activity strip come from a single pass: the activity
    detectors already accumulate the 30 ms RMS frames the envelope needs, so the
    lanes cannot be computed from different audio or different settings.
    """
    config = (config or TimelineConfig()).validated()
    source = snapshot.get("source") or {}
    scope = snapshot.get("scope") or {}
    duration_ms = _scope_duration_ms(snapshot)
    total_buckets = bucket_count(duration_ms, config.bucket_ms)
    requested, expected_backend, _ = _resolve_backend(snapshot, config)
    cache_key = timeline_cache_key(
        source_sha256=str(source.get("sha256", "")),
        run_id=str(snapshot.get("run_id", "")),
        bucket_ms=config.bucket_ms,
        activity_backend=requested,
    )
    scope_start_ms = int(scope.get("start_ms") or 0)

    if not source.get("has_audio", False):
        return _unavailable_payload(
            snapshot,
            config,
            duration_ms=duration_ms,
            total_buckets=total_buckets,
            warnings=["The analyzed source has no audio stream; timeline lanes are unavailable"],
            cache_key=cache_key,
        )

    try:
        media = probe_media(media_path)
    except AnalysisError as exc:
        return _unavailable_payload(
            snapshot,
            config,
            duration_ms=duration_ms,
            total_buckets=total_buckets,
            warnings=[f"Timeline lanes unavailable: {exc}"],
            cache_key=cache_key,
        )

    if media.audio is None or media.audio.channels is None:
        return _unavailable_payload(
            snapshot,
            config,
            duration_ms=duration_ms,
            total_buckets=total_buckets,
            warnings=["Probed media has no usable channel metadata"],
            cache_key=cache_key,
        )

    warnings: list[str] = []
    try:
        detector, _metadata = create_activity_detector(backend=requested, model_path=vad_model)
        strategy, channel_index, _channel_metrics = choose_analysis_channel(
            media_path, channels=media.audio.channels, start_ms=scope_start_ms
        )
        for pcm in iter_pcm_chunks(
            media_path,
            channels=media.audio.channels,
            start_ms=scope_start_ms,
            end_ms=scope_start_ms + duration_ms,
            chunk_seconds=config.chunk_seconds,
        ):
            detector.add_chunk(select_or_average_mono(pcm, strategy, channel_index))
        activity = detector.finish(duration_ms, _transcript_cues(snapshot, media_path))
        levels, starts = detector.levels_and_starts()
    except AnalysisError as exc:
        return _unavailable_payload(
            snapshot,
            config,
            duration_ms=duration_ms,
            total_buckets=total_buckets,
            warnings=[f"Timeline lanes unavailable: {exc}"],
            cache_key=cache_key,
        )

    envelope_values = reduce_envelope(
        levels,
        starts,
        offset_ms=scope_start_ms,
        duration_ms=duration_ms,
        bucket_ms=config.bucket_ms,
        total_buckets=total_buckets,
    )
    activity_values = activity_buckets(
        activity.intervals,
        duration_ms=duration_ms,
        bucket_ms=config.bucket_ms,
        total_buckets=total_buckets,
    )

    agrees = _agrees_with_snapshot(snapshot, detector.backend, activity.speech_ratio)
    if expected_backend is not None and detector.backend != expected_backend:
        warnings.append(
            f"Activity lane used the {detector.backend} backend while the metrics used "
            f"{expected_backend}; treat the lane as indicative only"
        )
    if agrees is False:
        warnings.append(
            "Recomputed activity does not match the recorded speech ratio; the lane was "
            "recomputed independently of the analysis run"
        )
    warnings.extend(activity.warnings)

    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "artifact_role": ARTIFACT_ROLE,
        "cache_key": cache_key,
        "run_id": snapshot.get("run_id"),
        "run_key": snapshot.get("run_key"),
        "source_sha256": str(source.get("sha256", "")),
        "scope_start_ms": scope_start_ms,
        "scope_end_ms": scope_start_ms + duration_ms,
        "duration_ms": duration_ms,
        "bucket_ms": int(config.bucket_ms),
        "bucket_count": total_buckets,
        "envelope": {
            "available": True,
            "unit": "dbfs",
            "scale": ENVELOPE_SCALE,
            "floor_dbfs": RENDER_FLOOR_DBFS,
            "reference_dbfs": _envelope_reference(snapshot),
            "reference_label": ENVELOPE_REFERENCE_LABEL,
            "reference_note": ENVELOPE_REFERENCE_NOTE,
            "values": envelope_values,
        },
        "activity": {
            "available": bool(activity_values) and activity.speech_present != "no",
            "method": activity.method,
            "backend": detector.backend,
            "speech_ratio": activity.speech_ratio,
            "agrees_with_snapshot": agrees,
            "values": activity_values,
        },
        "warnings": list(dict.fromkeys(warnings)),
        "generator": {
            "timeline_version": TIMELINE_SCHEMA_VERSION,
            "sample_rate": SAMPLE_RATE,
            "frame_ms": ActivityConfig().frame_ms,
        },
    }


def _envelope_reference(snapshot: dict[str, Any]) -> float | None:
    """The recorded active-speech median level, offered as a labeled reference only."""
    value = (snapshot.get("measurements") or {}).get("active_speech_median_dbfs")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _scope_duration_ms(snapshot: dict[str, Any]) -> int:
    scope = snapshot.get("scope") or {}
    start = int(scope.get("start_ms") or 0)
    end = int(scope.get("end_ms") or 0)
    if end > start:
        return end - start
    duration = int((snapshot.get("source") or {}).get("duration_ms") or 0)
    return max(1, duration)


def _agrees_with_snapshot(
    snapshot: dict[str, Any], backend: str, speech_ratio: float | None
) -> bool | None:
    """Whether the recomputed lane is consistent with the committed metrics."""
    expected_backend = str((snapshot.get("quality") or {}).get("activity_backend") or "")
    if expected_backend and expected_backend != backend:
        return False
    expected_ratio = _snapshot_speech_ratio(snapshot)
    if expected_ratio is None or speech_ratio is None:
        return None
    return abs(expected_ratio - speech_ratio) <= SPEECH_RATIO_TOLERANCE


def load_or_build_timeline(
    paths: MetricsPaths,
    media_path: Path,
    snapshot: dict[str, Any],
    config: TimelineConfig | None = None,
    *,
    vad_model: Path | None = None,
    force: bool = False,
) -> TimelineArtifact:
    """Return a valid sidecar, computing it only when the cache does not match."""
    config = (config or TimelineConfig()).validated()
    path = timeline_path(paths)
    source = snapshot.get("source") or {}
    _, expected_backend, _ = _resolve_backend(snapshot, config)
    requested = expected_backend if config.activity_backend == "match" else config.activity_backend
    cache_key = timeline_cache_key(
        source_sha256=str(source.get("sha256", "")),
        run_id=str(snapshot.get("run_id", "")),
        bucket_ms=config.bucket_ms,
        activity_backend=requested,
    )

    if not force and path.is_file():
        try:
            cached = load_json(path)
        except Exception:  # noqa: BLE001 - a corrupt cache is simply rebuilt
            cached = None
        if (
            isinstance(cached, dict)
            and cached.get("artifact_role") == ARTIFACT_ROLE
            and cached.get("cache_key") == cache_key
            and cached.get("source_sha256") == str(source.get("sha256", ""))
        ):
            return TimelineArtifact(payload=cached, path=path, rebuilt=False)

    payload = build_timeline(media_path, snapshot, config, vad_model=vad_model)
    atomic_write_text(path, pretty_json(payload))
    return TimelineArtifact(payload=payload, path=path, rebuilt=True)
