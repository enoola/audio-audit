from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from senate_audio_audit import visualizer
from senate_audio_audit.activity import SAMPLE_RATE, EnergyActivityDetector
from senate_audio_audit.artifacts import MetricsPaths
from senate_audio_audit.errors import AnalysisError, ArtifactError
from senate_audio_audit.models import SpeechInterval
from senate_audio_audit.visualizer import timeline as timeline_module
from senate_audio_audit.visualizer.timeline import (
    ENVELOPE_SCALE,
    TimelineConfig,
    activity_buckets,
    bucket_count,
    build_timeline,
    load_or_build_timeline,
    reduce_envelope,
    scale_dbfs,
    timeline_cache_key,
    timeline_path,
)
from tests.factories import make_result


def snapshot_dict(**overrides: Any) -> dict[str, Any]:
    payload = make_result().to_dict()
    payload["source"]["sha256"] = "a" * 64
    payload["source"]["has_audio"] = True
    payload["scope"] = {"start_ms": 0, "end_ms": 10_000, "language": "fr"}
    payload["measurements"]["speech_ratio"] = 0.8
    payload["quality"]["activity_backend"] = "energy"
    payload.update(overrides)
    return payload


def metrics_paths(tmp_path: Path, stem: str = "sample") -> MetricsPaths:
    latest = tmp_path / "metrics" / f"{stem}.metrics.json"
    return MetricsPaths(
        latest_json=latest,
        latest_text=latest.with_name(f"{stem}.metrics.txt"),
        history_jsonl=latest.with_name(f"{stem}.metrics.history.jsonl"),
        history_text=latest.with_name(f"{stem}.metrics.history.txt"),
        current_pointer=latest.with_name(f".{stem}.current.json"),
        lock_file=latest.with_name(f".{stem}.lock"),
    )


def frames(dbfs: list[float], frame_ms: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """Build the (levels, starts) pair a _LevelCollector would produce."""
    level_array = np.asarray(dbfs, dtype=np.float64)
    frame_samples = round(SAMPLE_RATE * frame_ms / 1000)
    starts = np.arange(level_array.size, dtype=np.int64) * frame_samples
    return level_array, starts


def test_bucket_count_rounds_up_and_never_returns_zero() -> None:
    assert bucket_count(5_343_033, 1_000) == 5_344
    assert bucket_count(10_000, 1_000) == 10
    assert bucket_count(1_001, 1_000) == 2
    assert bucket_count(0, 1_000) == 1
    assert bucket_count(500, 1_000) == 1


def test_scale_dbfs_clamps_to_representable_domain() -> None:
    assert scale_dbfs(-18.44) == -184
    assert scale_dbfs(3.0) == 0
    assert scale_dbfs(-400.0) == -1_200
    assert scale_dbfs(float("nan")) == -1_200
    assert scale_dbfs(float("inf")) == -1_200


def test_reduce_envelope_is_flat_for_constant_frames() -> None:
    levels, starts = frames([-18.0] * 100)
    values = reduce_envelope(
        levels, starts, offset_ms=0, duration_ms=3_000, bucket_ms=1_000, total_buckets=3
    )
    assert values == [-180] * 3


def test_reduce_envelope_offsets_into_media_time() -> None:
    """A frame at the start of a scope that begins at 3000 ms belongs in bucket 3."""
    levels, starts = frames([-20.0] * 400)
    values = reduce_envelope(
        levels,
        starts,
        offset_ms=3_000,
        duration_ms=10_000,
        bucket_ms=1_000,
        total_buckets=10,
    )
    assert values[0] == -1_200
    assert values[1] == -1_200
    assert values[2] == -1_200
    assert values[3] != -1_200


def test_reduce_envelope_keeps_a_short_transient_visible() -> None:
    """Mean power, not mean dB: one loud frame in a quiet bucket must not vanish."""
    quiet = [-60.0] * 99
    levels, starts = frames([*quiet, 0.0])
    values = reduce_envelope(
        levels, starts, offset_ms=0, duration_ms=30, bucket_ms=30, total_buckets=1
    )
    mean_db = 10.0 * math.log10(
        sum(10.0 ** (value / ENVELOPE_SCALE) for value in levels) / levels.size
    )
    assert values[0] == scale_dbfs(mean_db)
    assert values[0] > scale_dbfs(-60.0) + 10


def test_reduce_envelope_handles_no_frames() -> None:
    assert reduce_envelope(
        np.empty(0),
        np.empty(0, dtype=np.int64),
        offset_ms=0,
        duration_ms=2_000,
        bucket_ms=1_000,
        total_buckets=2,
    ) == [-1_200, -1_200]


def test_activity_buckets_encodes_full_and_empty_spans() -> None:
    values = activity_buckets(
        [SpeechInterval(0, 500)],
        duration_ms=1_000,
        bucket_ms=1_000,
        total_buckets=1,
    )
    assert values == [128]  # 500/1000 of the bucket


def test_activity_buckets_normalizes_the_partial_final_bucket() -> None:
    """A 600 ms final bucket is normalized against its own span, not a full 1000 ms."""
    full = activity_buckets(
        [SpeechInterval(1_000, 1_600)],
        duration_ms=1_600,
        bucket_ms=1_000,
        total_buckets=2,
    )
    assert full == [0, 255]  # 600/600, not 600/1000

    partial = activity_buckets(
        [SpeechInterval(1_000, 1_400)],
        duration_ms=1_600,
        bucket_ms=1_000,
        total_buckets=2,
    )
    assert partial == [0, 170]  # 400/600, not 400/1000


def test_activity_buckets_clamps_to_scope() -> None:
    values = activity_buckets(
        [SpeechInterval(-500, 99_000)],
        duration_ms=2_000,
        bucket_ms=1_000,
        total_buckets=2,
    )
    assert values == [255, 255]


def test_timeline_path_sits_beside_the_metrics_pair(tmp_path: Path) -> None:
    paths = metrics_paths(tmp_path)
    assert timeline_path(paths).name == "sample.timeline.json"
    assert timeline_path(paths).parent == paths.latest_json.parent


def test_cache_key_depends_on_every_component() -> None:
    base = {
        "source_sha256": "a" * 64,
        "run_id": "run-1",
        "bucket_ms": 1000,
        "activity_backend": "energy",
    }
    reference = timeline_cache_key(**base)
    assert reference == timeline_cache_key(**base)
    for field, other in (
        ("source_sha256", "b" * 64),
        ("run_id", "run-2"),
        ("bucket_ms", 500),
        ("activity_backend", "silero"),
    ):
        assert timeline_cache_key(**{**base, field: other}) != reference


def test_timeline_config_rejects_out_of_range_bucket() -> None:
    with pytest.raises(ArtifactError):
        TimelineConfig(bucket_ms=1).validated()
    with pytest.raises(ArtifactError):
        TimelineConfig(bucket_ms=10_000_000).validated()
    with pytest.raises(ArtifactError):
        TimelineConfig(activity_backend="nonsense").validated()
    assert TimelineConfig(bucket_ms=250).validated().bucket_ms == 250


def test_unavailable_payload_when_source_has_no_audio() -> None:
    payload = snapshot_dict()
    payload["source"]["has_audio"] = False
    result = build_timeline(Path("unused.mp3"), payload, TimelineConfig(bucket_ms=1_000))
    assert result["envelope"]["available"] is False
    assert result["activity"]["available"] is False
    assert result["envelope"]["values"] == [0] * 10
    assert "no audio stream" in " ".join(result["warnings"])


def test_missing_ffmpeg_degrades_instead_of_raising(
    tmp_path: Path, synthetic_wav_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = synthetic_wav_factory("degrade.wav", duration_seconds=4.0)

    def unavailable(name: str) -> str:
        raise AnalysisError(f"Required executable not found on PATH: {name}")

    monkeypatch.setattr("senate_audio_audit.media.shutil.which", unavailable)
    result = build_timeline(media, snapshot_dict(), TimelineConfig(bucket_ms=1_000))
    assert result["envelope"]["available"] is False
    assert any("Timeline lanes unavailable" in warning for warning in result["warnings"])


def test_envelope_reference_carries_its_own_caveat(synthetic_wav_factory) -> None:
    """The active-speech median must not be presented as the envelope's median."""
    media = synthetic_wav_factory("reference.wav", duration_seconds=4.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    payload["measurements"]["active_speech_median_dbfs"] = -37.722
    result = build_timeline(media, payload, TimelineConfig(bucket_ms=1_000))

    envelope = result["envelope"]
    assert envelope["reference_dbfs"] == pytest.approx(-37.722)
    assert "active-speech" in envelope["reference_label"]
    assert "not directly comparable" in envelope["reference_note"]


def test_envelope_reference_is_null_when_no_median_was_recorded(
    synthetic_wav_factory,
) -> None:
    media = synthetic_wav_factory("noref.wav", duration_seconds=4.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    payload["measurements"]["active_speech_median_dbfs"] = None
    result = build_timeline(media, payload, TimelineConfig(bucket_ms=1_000))
    assert result["envelope"]["reference_dbfs"] is None


def test_activity_lane_records_its_provenance(synthetic_wav_factory) -> None:
    """A transcript-fallback lane must be identifiable as such by the renderer."""
    media = synthetic_wav_factory("provenance.wav", duration_seconds=12.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 12_000})
    result = build_timeline(media, payload, TimelineConfig(bucket_ms=1_000))
    assert result["activity"]["method"]
    assert result["activity"]["backend"] in {"energy", "silero"}
    # A transcript-fallback method name must survive into the payload verbatim.
    assert "transcript" in result["activity"]["method"] or (
        result["activity"]["method"] == "energy"
    )


def test_build_timeline_produces_both_lanes(synthetic_wav_factory) -> None:
    media = synthetic_wav_factory("lanes.wav", duration_seconds=12.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 12_000, "language": "fr"})
    payload["measurements"]["speech_ratio"] = 0.0
    result = build_timeline(media, payload, TimelineConfig(bucket_ms=1_000))

    assert result["artifact_role"] == visualizer.ARTIFACT_ROLE
    assert result["bucket_count"] == 12
    assert len(result["envelope"]["values"]) == 12
    assert len(result["activity"]["values"]) == 12
    assert result["envelope"]["available"] is True
    assert result["activity"]["available"] is True
    assert result["activity"]["backend"] == "energy"
    # The synthetic fixture is silent outside 3.0-5.5 s and 7.0-9.5 s.
    assert max(result["envelope"]["values"][3:6]) > -1_200
    assert result["envelope"]["values"][0] == -1_200
    assert result["activity"]["values"][0] == 0


def test_sidecar_never_duplicates_events(synthetic_wav_factory) -> None:
    """Events live only in *.metrics.json; the sidecar must not restate them."""
    media = synthetic_wav_factory("noevents.wav", duration_seconds=4.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    result = build_timeline(media, payload, TimelineConfig(bucket_ms=1_000))
    assert "events" not in result
    assert "vividness" not in result


def test_envelope_agrees_with_recorded_median_level(sine_wav_factory) -> None:
    """A constant-amplitude tone must reduce to the same dBFS the analysis pass records."""
    amplitude = 0.5
    frequency = 220.0
    seconds = 6
    media = sine_wav_factory("tone.wav", seconds=seconds, frequency=frequency, amplitude=amplitude)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": seconds * 1_000})
    payload["measurements"]["speech_ratio"] = 1.0
    result = build_timeline(media, payload, TimelineConfig(bucket_ms=1_000))

    # A sine of amplitude A has RMS A/sqrt(2), not A.
    expected = scale_dbfs(20.0 * math.log10(amplitude / math.sqrt(2.0)))

    # Cross-check against the very frames the analysis pass records for these samples.
    time = np.arange(SAMPLE_RATE * seconds, dtype=np.float64) / SAMPLE_RATE
    mono = (amplitude * np.sin(2 * math.pi * frequency * time)).astype(np.float32)
    detector = EnergyActivityDetector()
    detector.add_chunk(mono)
    levels, _starts = detector.levels_and_starts()
    assert abs(expected - scale_dbfs(float(np.median(levels)))) <= 1

    # Every bucket holds the same constant tone, so all six must land on that value.
    assert result["envelope"]["values"] == [expected] * seconds
    # The energy backend is relative: a constant tone has zero dynamic range, so its
    # adaptive threshold finds no speech. The lane stays empty rather than fabricating one.
    assert result["activity"]["speech_ratio"] == 0.0
    assert result["activity"]["available"] is False


def test_activity_disagreement_is_reported(synthetic_wav_factory) -> None:
    media = synthetic_wav_factory("disagree.wav", duration_seconds=12.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 12_000})
    payload["measurements"]["speech_ratio"] = 0.99
    result = build_timeline(media, payload, TimelineConfig(bucket_ms=1_000))
    assert result["activity"]["agrees_with_snapshot"] is False
    assert any("does not match" in warning for warning in result["warnings"])


def test_match_backend_follows_a_silero_snapshot_without_requiring_onnx(
    synthetic_wav_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Silero snapshot requests "auto" so a missing model degrades instead of raising."""
    media = synthetic_wav_factory("silero.wav", duration_seconds=4.0)
    requested: list[str] = []

    def record(backend: str = "auto", model_path: Path | None = None):
        requested.append(backend)
        return EnergyActivityDetector(), {"backend": "energy"}

    monkeypatch.setattr(timeline_module, "create_activity_detector", record)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    payload["quality"]["activity_backend"] = "silero"
    result = build_timeline(media, payload, TimelineConfig())

    assert requested == ["auto"]
    assert result["activity"]["backend"] == "energy"
    assert result["activity"]["agrees_with_snapshot"] is False
    assert any("while the metrics used silero" in w for w in result["warnings"])


def test_match_backend_forces_energy_when_the_snapshot_used_energy(
    synthetic_wav_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An energy-analyzed recording must not silently upgrade to Silero later."""
    media = synthetic_wav_factory("energy.wav", duration_seconds=4.0)
    requested: list[str] = []

    def record(backend: str = "auto", model_path: Path | None = None):
        requested.append(backend)
        return EnergyActivityDetector(), {"backend": "energy"}

    monkeypatch.setattr(timeline_module, "create_activity_detector", record)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    build_timeline(media, payload, TimelineConfig())
    assert requested == ["energy"]


def test_cached_sidecar_is_reused_without_recomputing(
    tmp_path: Path, synthetic_wav_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = synthetic_wav_factory("cache.wav", duration_seconds=4.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    paths = metrics_paths(tmp_path)
    config = TimelineConfig(bucket_ms=1_000)

    first = load_or_build_timeline(paths, media, payload, config)
    assert first.rebuilt is True
    assert first.path.is_file()

    def explode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("cached sidecar must not trigger a rebuild")

    monkeypatch.setattr(timeline_module, "build_timeline", explode)
    second = load_or_build_timeline(paths, media, payload, config)
    assert second.rebuilt is False
    assert second.payload["cache_key"] == first.payload["cache_key"]


def test_force_rebuilds_even_with_a_valid_cache(tmp_path: Path, synthetic_wav_factory) -> None:
    media = synthetic_wav_factory("force.wav", duration_seconds=4.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    paths = metrics_paths(tmp_path)
    config = TimelineConfig(bucket_ms=1_000)

    load_or_build_timeline(paths, media, payload, config)
    again = load_or_build_timeline(paths, media, payload, config, force=True)
    assert again.rebuilt is True


def test_changed_bucket_size_invalidates_the_cache(tmp_path: Path, synthetic_wav_factory) -> None:
    media = synthetic_wav_factory("resize.wav", duration_seconds=4.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    paths = metrics_paths(tmp_path)

    load_or_build_timeline(paths, media, payload, TimelineConfig(bucket_ms=1_000))
    resized = load_or_build_timeline(paths, media, payload, TimelineConfig(bucket_ms=500))
    assert resized.rebuilt is True
    assert resized.payload["bucket_count"] == 8


def test_corrupt_cache_is_rebuilt(tmp_path: Path, synthetic_wav_factory) -> None:
    media = synthetic_wav_factory("corrupt.wav", duration_seconds=4.0)
    payload = snapshot_dict(scope={"start_ms": 0, "end_ms": 4_000})
    paths = metrics_paths(tmp_path)
    config = TimelineConfig(bucket_ms=1_000)

    load_or_build_timeline(paths, media, payload, config)
    paths.latest_json.parent.mkdir(parents=True, exist_ok=True)
    timeline_path(paths).write_text("{not json", encoding="utf-8")
    recovered = load_or_build_timeline(paths, media, payload, config)
    assert recovered.rebuilt is True
