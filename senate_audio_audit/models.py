from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

AnalysisStatus = Literal[
    "completed",
    "partial",
    "no_speech",
    "not_assessable",
    "failed",
]

DecisionState = Literal[
    "supported_candidate",
    "uncertain_candidate",
    "no_qualifying_evidence",
    "not_assessable",
    "human_confirmed",
    "human_rejected",
    "human_uncertain",
]


@dataclass(frozen=True, slots=True)
class AudioStreamInfo:
    codec: str | None
    sample_rate: int | None
    channels: int | None
    channel_layout: str | None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: str
    filename: str
    sha256: str
    size_bytes: int
    duration_ms: int
    format_name: str | None
    has_audio: bool
    has_video: bool
    audio: AudioStreamInfo | None


@dataclass(frozen=True, slots=True)
class TranscriptCue:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptInfo:
    path: str | None
    format: str | None
    language: str | None
    cues: tuple[TranscriptCue, ...]
    warnings: tuple[str, ...]
    original_end_ms: int
    clamped_end_ms: int

    @property
    def coverage_ms(self) -> int:
        return sum(max(0, cue.end_ms - cue.start_ms) for cue in self.cues)


@dataclass(frozen=True, slots=True)
class SpeechInterval:
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class AcousticMetrics:
    active_speech_median_dbfs: float | None
    active_speech_p95_dbfs: float | None
    clipping_fraction: float | None
    median_speech_rate_wpm: float | None
    median_pause_ratio: float | None
    f0_variation_semitones: float | None
    overlap_fraction: float | None
    anonymous_speaker_count: int | None
    active_speech_duration_ms: int
    speech_ratio: float
    activity_method: str
    activity_threshold_dbfs: float
    activity_noise_floor_dbfs: float
    channel_strategy: str
    f0_median_hz: float | None
    activity_backend: str = "energy"
    activity_probability_threshold: float | None = None


@dataclass(frozen=True, slots=True)
class CandidateEvent:
    event_id: str
    claim_type: str
    epistemic_status: Literal["signal_observation", "inferred_perception"]
    decision_state: DecisionState
    start_ms: int
    end_ms: int
    speaker_id: str | None
    score_semantics: str
    calibration_id: str | None
    signal_strengths: dict[str, float | None]
    evidence: dict[str, Any]
    transcript: str | None
    model_versions: dict[str, str]
    review_status: str = "unreviewed"


@dataclass(frozen=True, slots=True)
class SpeakerSummary:
    speaker_id: str
    identity: None
    active_speech_ms: int
    speech_share: float | None
    median_relative_level_db: float | None
    high_delivery_fraction: float | None
    candidate_event_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class VividnessResult:
    score: int | None
    expected_rating: float | None
    status: Literal["not_assessable", "provisional_rubric", "pilot_calibrated", "validated"]
    confidence_band: Literal["low", "medium", "high"]
    prediction_interval_80: tuple[float, float] | None
    components: dict[str, float | None]
    explanation: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    schema_version: str
    artifact_role: str
    run_id: str
    run_key: str
    supersedes_run_id: str | None
    analysis_status: AnalysisStatus
    created_at: str
    history: dict[str, str]
    source: dict[str, Any]
    scope: dict[str, Any]
    pipeline: dict[str, Any]
    quality: dict[str, Any]
    measurements: AcousticMetrics
    summary: dict[str, Any]
    vividness: VividnessResult
    events: tuple[CandidateEvent, ...] = ()
    speaker_summaries: tuple[SpeakerSummary, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    review: dict[str, Any] = field(default_factory=lambda: {"status": "unreviewed"})

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.vividness.prediction_interval_80 is not None:
            value["vividness"]["prediction_interval_80"] = list(
                self.vividness.prediction_interval_80
            )
        return value


def jsonable(value: Any) -> Any:
    """Recursively convert dataclasses and Paths for JSON serialization."""
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
