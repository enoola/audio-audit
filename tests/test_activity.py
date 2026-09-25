from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from senate_audio_audit.activity import (
    EnergyActivityDetector,
    create_activity_detector,
    merge_intervals,
)
from senate_audio_audit.errors import AnalysisError
from senate_audio_audit.models import SpeechInterval, TranscriptCue


def test_constant_amplitude_is_about_minus_6_dbfs() -> None:
    detector = EnergyActivityDetector()
    detector.add_chunk(np.full(16_000, 0.5, dtype=np.float32))
    detector.finish(1000)
    levels, _ = detector.levels_and_starts()
    assert float(np.median(levels)) == pytest_approx(-6.0206, abs=0.01)


def test_energy_activity_finds_burst_and_refines_with_transcript() -> None:
    sample_rate = 16_000
    mono = np.zeros(sample_rate * 10, dtype=np.float32)
    mono[2 * sample_rate : 5 * sample_rate] = 0.1
    detector = EnergyActivityDetector()
    detector.add_chunk(mono)
    cues = (TranscriptCue(1, 1800, 5200, "une phrase"),)
    result = detector.finish(10_000, cues)
    assert result.speech_present == "yes"
    assert result.speech_duration_ms > 2500
    assert result.method == "energy+transcript"
    assert all(
        interval.start_ms >= 0 and interval.end_ms <= 10_000 for interval in result.intervals
    )


def test_merge_intervals_is_sorted_and_non_overlapping() -> None:
    merged = merge_intervals(
        [
            SpeechInterval(500, 1000),
            SpeechInterval(0, 400),
            SpeechInterval(900, 1500),
        ]
    )
    assert merged == (SpeechInterval(0, 400), SpeechInterval(500, 1500))


def test_explicit_energy_backend_does_not_require_onnx() -> None:
    detector, metadata = create_activity_detector("energy")
    assert detector.backend == "energy"
    assert metadata["model_sha256"] is None


def test_auto_backend_reports_energy_when_model_is_absent(tmp_path: Path) -> None:
    detector, metadata = create_activity_detector("auto", tmp_path / "missing.onnx")
    assert detector.backend == "energy"
    assert "not found" in str(metadata["selection_reason"])


def test_explicit_silero_backend_fails_when_model_is_absent(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError):
        create_activity_detector("silero", tmp_path / "missing.onnx")


def test_silero_backend_loads_pinned_model_when_available() -> None:
    model = Path(__file__).resolve().parents[1] / "models" / "silero_vad_16k_op15.onnx"
    if not model.exists():
        pytest.skip("optional Silero model has not been provisioned")
    pytest.importorskip("onnxruntime")
    detector, metadata = create_activity_detector("silero", model)
    assert detector.backend == "silero"
    assert (
        metadata["model_sha256"]
        == "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"
    )


def pytest_approx(expected: float, *, abs: float):
    return pytest.approx(expected, abs=abs)
