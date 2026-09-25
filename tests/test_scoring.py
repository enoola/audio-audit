from __future__ import annotations

import numpy as np
from senate_audio_audit.acoustics import AcousticAnalysis
from senate_audio_audit.activity import ActivityResult
from senate_audio_audit.models import AcousticMetrics, CandidateEvent
from senate_audio_audit.scoring import calculate_vividness, event_intensity


def _metrics(active_ms: int) -> AcousticMetrics:
    return AcousticMetrics(
        active_speech_median_dbfs=-20.0,
        active_speech_p95_dbfs=-10.0,
        clipping_fraction=0.0,
        median_speech_rate_wpm=120.0,
        median_pause_ratio=0.1,
        f0_variation_semitones=1.0,
        overlap_fraction=None,
        anonymous_speaker_count=None,
        active_speech_duration_ms=active_ms,
        speech_ratio=1.0,
        activity_method="test",
        activity_threshold_dbfs=-40.0,
        activity_noise_floor_dbfs=-60.0,
        channel_strategy="mono",
        f0_median_hz=200.0,
    )


def _acoustic(active_ms: int) -> AcousticAnalysis:
    activity = ActivityResult(
        intervals=(),
        method="test",
        speech_present="yes" if active_ms else "no",
        speech_duration_ms=active_ms,
        speech_ratio=1.0 if active_ms else 0.0,
        confidence="high",
        threshold_dbfs=-40.0,
        noise_floor_dbfs=-60.0,
        warnings=(),
    )
    return AcousticAnalysis(
        metrics=_metrics(active_ms),
        activity=activity,
        frame_levels_dbfs=np.array([]),
        frame_start_ms=np.array([]),
        active_frame_mask=np.array([], dtype=bool),
        active_median_dbfs=-20.0,
        cue_rates=(),
        median_cue_rate_wpm=120.0,
        loud_windows=(),
        channel_strategy="mono",
        channel_index=None,
        channel_metrics={},
        f0_median_hz=200.0,
        warnings=(),
    )


def _event(score: float) -> CandidateEvent:
    return CandidateEvent(
        event_id="evt",
        claim_type="relative_loud_delivery",
        epistemic_status="inferred_perception",
        decision_state="supported_candidate",
        start_ms=0,
        end_ms=5000,
        speaker_id=None,
        score_semantics="uncalibrated_signal_strength",
        calibration_id=None,
        signal_strengths={"relative_loud_delivery": score},
        evidence={},
        transcript=None,
        model_versions={},
    )


def test_no_speech_returns_not_assessable_not_zero() -> None:
    result = calculate_vividness(_acoustic(0), ())
    assert result.score is None
    assert result.status == "not_assessable"


def test_vividness_is_bounded_and_never_probability() -> None:
    result = calculate_vividness(_acoustic(10_000), (_event(0.8),))
    assert result.score is not None
    assert 0 <= result.score <= 10
    assert result.status == "provisional_rubric"
    assert result.prediction_interval_80 is None


def test_event_intensity_uses_strongest_head() -> None:
    event = _event(0.2)
    object.__setattr__(
        event,
        "signal_strengths",
        {
            "relative_loud_delivery": 0.2,
            "potentially_disrespectful_act": 0.5,
        },
    )
    assert event_intensity(event) == 3.0
