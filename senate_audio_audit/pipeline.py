from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .acoustics import AcousticStreamAnalyzer
from .activity import ActivityDetector, create_activity_detector
from .artifacts import (
    MetricsPaths,
    PublishOutcome,
    compute_run_key,
    metrics_paths,
    publish_result,
    read_latest,
)
from .errors import AnalysisError, InputError
from .fusion import build_candidate_events
from .media import (
    choose_analysis_channel,
    iter_pcm_chunks,
    probe_media,
    select_or_average_mono,
)
from .models import (
    AcousticMetrics,
    AnalysisResult,
    CandidateEvent,
    MediaInfo,
    SpeakerSummary,
    TranscriptInfo,
    VividnessResult,
)
from .scoring import calculate_vividness
from .text_signals import analyze_transcript
from .transcripts import discover_transcript
from .util import new_run_id, sha256_file, stable_hash, utc_now


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    metrics_dir: Path
    analyses_dir: Path
    text_report: Path
    input_root: Path | None = None
    transcript_path: Path | None = None
    language: str = "fr"
    start_ms: int | None = None
    end_ms: int | None = None
    chunk_seconds: float = 60.0
    force: bool = False
    offline: bool = True
    rights_status: str = "unverified_internal_pilot"
    permission_reference: str | None = None
    vad_backend: str = "auto"
    vad_model_path: Path | None = None


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    result: AnalysisResult
    publish: PublishOutcome


def analyze_media(media_path: Path, config: AnalysisConfig) -> PipelineOutcome:
    media_path = media_path.expanduser()
    media = probe_media(media_path)
    scope_start = 0 if config.start_ms is None else config.start_ms
    scope_end = media.duration_ms if config.end_ms is None else config.end_ms
    _validate_scope(scope_start, scope_end, media.duration_ms)

    transcript = discover_transcript(
        media_path,
        explicit_path=config.transcript_path,
        duration_ms=media.duration_ms,
    )
    transcript_sha = sha256_file(Path(transcript.path)) if transcript.path else None
    activity_detector, vad_metadata = create_activity_detector(
        backend=config.vad_backend,
        model_path=config.vad_model_path,
    )
    model_manifest = _model_manifest(vad_metadata)
    model_manifest_hash = stable_hash(model_manifest)
    input_root = config.input_root or media_path.parent
    paths = metrics_paths(
        media_path,
        config.metrics_dir,
        input_root,
        media.sha256,
    )
    config_payload = {
        "language": config.language,
        "chunk_seconds": config.chunk_seconds,
        "activity": vad_metadata["backend"],
        "vad_model_id": vad_metadata["model_id"],
        "vad_model_sha256": vad_metadata["model_sha256"],
        "event_policy": "transparent-provisional-v0",
        "scoring": "peak-coverage-persistence-v0",
    }
    config_hash = stable_hash(config_payload)
    run_key = compute_run_key(
        source_sha256=media.sha256,
        transcript_sha256=transcript_sha,
        start_ms=scope_start,
        end_ms=scope_end,
        pipeline_version=__version__,
        config_hash=config_hash,
        model_manifest_hash=model_manifest_hash,
    )
    latest = read_latest(paths)
    if latest and latest.get("run_key") == run_key and not config.force:
        existing_result = _snapshot_to_result(latest)
        # Reconcile a missing/corrupt history side or latest text even when the
        # semantic run is unchanged; this does not append a duplicate run.
        publish_result(existing_result, paths, config.analyses_dir)
        return PipelineOutcome(
            result=existing_result,
            publish=PublishOutcome(
                state="idempotent_noop",
                latest_run_id=str(latest["run_id"]),
                paths=paths,
            ),
        )

    supersedes = str(latest["run_id"]) if latest else None
    run_id = new_run_id()
    if media.audio is None:
        result = _no_audio_result(
            run_id=run_id,
            run_key=run_key,
            supersedes=supersedes,
            media=media,
            config=config,
            paths=paths,
            transcript=transcript,
            transcript_sha=transcript_sha,
            scope_start=scope_start,
            scope_end=scope_end,
            config_payload=config_payload,
            config_hash=config_hash,
            model_manifest_hash=model_manifest_hash,
            model_manifest=model_manifest,
            vad_metadata=vad_metadata,
        )
        outcome = publish_result(
            result,
            paths,
            config.analyses_dir,
            force=config.force,
        )
        return PipelineOutcome(result=result, publish=outcome)
    try:
        acoustic, post_media_sha, post_transcript_sha = _analyze_audio(
            media,
            transcript,
            scope_start=scope_start,
            scope_end=scope_end,
            chunk_seconds=config.chunk_seconds,
            activity_detector=activity_detector,
        )
        if post_media_sha != media.sha256:
            raise AnalysisError("Source media changed during analysis; results were not published")
        if post_transcript_sha != transcript_sha:
            raise AnalysisError("Transcript changed during analysis; results were not published")

        text_signals = analyze_transcript(transcript.cues) if transcript.cues else ()
        events = build_candidate_events(
            run_id=run_id,
            loud_windows=acoustic.loud_windows,
            text_signals=text_signals,
        )
        vividness = calculate_vividness(acoustic, events)
        status = _analysis_status(acoustic.activity.speech_present, bool(transcript.cues))
        warnings = _quality_warnings(media, transcript, acoustic, config)
        result = AnalysisResult(
            schema_version="0.1.0",
            artifact_role="per_audio_latest_metrics",
            run_id=run_id,
            run_key=run_key,
            supersedes_run_id=supersedes,
            analysis_status=status,
            created_at=utc_now(),
            history={
                "jsonl": paths.history_jsonl.name,
                "text": paths.history_text.name,
            },
            source=_source_payload(media, config, paths),
            scope={
                "start_ms": scope_start,
                "end_ms": scope_end,
                "language": config.language,
            },
            pipeline={
                "version": __version__,
                "config": config_payload,
                "config_hash": config_hash,
                "model_manifest_hash": model_manifest_hash,
                "models": model_manifest,
                "offline": config.offline,
            },
            quality={
                "overall": _quality_level(acoustic.activity.confidence),
                "activity_method": acoustic.activity.method,
                "activity_confidence": acoustic.activity.confidence,
                "activity_backend": vad_metadata["backend"],
                "vad_model": vad_metadata,
                "transcript": transcript.format,
                "transcript_path": transcript.path,
                "transcript_sha256": transcript_sha,
                "transcript_alignment": "cue_timestamps_unverified"
                if transcript.cues
                else "unavailable",
                "diarization": "not_enabled",
                "channel_strategy": acoustic.channel_strategy,
                "channel_metrics": acoustic.channel_metrics,
                "warnings": warnings,
            },
            measurements=acoustic.metrics,
            summary={
                "speech_present": acoustic.activity.speech_present,
                "speech_ratio": round(acoustic.activity.speech_ratio, 6),
                "candidate_event_count": len(events),
                "status_note": (
                    "provisional research output; not a determination of intent or character"
                ),
            },
            vividness=vividness,
            events=events,
            speaker_summaries=(),
            errors=(),
        )
    except (AnalysisError, OSError, ValueError) as exc:
        result = _failed_result(
            run_id=run_id,
            run_key=run_key,
            supersedes=supersedes,
            media=media,
            config=config,
            paths=paths,
            transcript=transcript,
            transcript_sha=transcript_sha,
            scope_start=scope_start,
            scope_end=scope_end,
            config_payload=config_payload,
            config_hash=config_hash,
            model_manifest_hash=model_manifest_hash,
            model_manifest=model_manifest,
            vad_metadata=vad_metadata,
            error=exc,
        )

    outcome = publish_result(
        result,
        paths,
        config.analyses_dir,
        force=config.force,
    )
    return PipelineOutcome(result=result, publish=outcome)


def _analyze_audio(
    media: MediaInfo,
    transcript: TranscriptInfo,
    *,
    scope_start: int,
    scope_end: int,
    chunk_seconds: float,
    activity_detector: ActivityDetector,
) -> tuple[Any, str, str | None]:
    if media.audio is None or media.audio.channels is None:
        raise AnalysisError("Accepted media has no usable channel metadata")
    strategy, channel_index, channel_metrics = choose_analysis_channel(
        Path(media.path),
        channels=media.audio.channels,
        start_ms=scope_start,
    )
    analyzer = AcousticStreamAnalyzer(
        scope_start_ms=scope_start,
        scope_end_ms=scope_end,
        channel_strategy=strategy,
        channel_index=channel_index,
        channel_metrics=channel_metrics,
        activity_detector=activity_detector,
    )
    for pcm in iter_pcm_chunks(
        Path(media.path),
        channels=media.audio.channels,
        start_ms=scope_start,
        end_ms=scope_end,
        chunk_seconds=chunk_seconds,
    ):
        mono = select_or_average_mono(pcm, strategy, channel_index)
        analyzer.add_chunk(mono, source_pcm=pcm)
    acoustic = analyzer.finish(transcript.cues)
    post_media_sha = sha256_file(Path(media.path))
    post_transcript_sha = sha256_file(Path(transcript.path)) if transcript.path else None
    return acoustic, post_media_sha, post_transcript_sha


def _model_manifest(vad_metadata: dict[str, object]) -> dict[str, object]:
    return {
        "activity": {
            "backend": vad_metadata["backend"],
            "model_id": vad_metadata["model_id"],
            "model_sha256": vad_metadata["model_sha256"],
            "probability_threshold": vad_metadata["probability_threshold"],
        },
        "acoustic_events": {
            "model_id": "relative-level-rule-v0",
            "kind": "auditable-rules",
        },
        "text_events": {
            "model_id": "fr-lexical-review-rules-v0",
            "kind": "auditable-rules",
        },
        "diarization": None,
        "probability_calibration": None,
    }


def _validate_scope(start_ms: int, end_ms: int, duration_ms: int) -> None:
    if start_ms < 0 or end_ms < 0:
        raise InputError("Scope timestamps cannot be negative")
    if end_ms <= start_ms:
        raise InputError("Scope end must be after scope start")
    if end_ms > duration_ms:
        raise InputError(f"Scope end {end_ms} ms exceeds media duration {duration_ms} ms")


def _analysis_status(speech_present: str, has_transcript: bool) -> str:
    if speech_present == "no":
        return "no_speech"
    if speech_present == "uncertain":
        return "not_assessable"
    return "completed" if has_transcript else "partial"


def _quality_level(confidence: str) -> str:
    return {"high": "good", "medium": "fair", "low": "poor"}.get(confidence, "unknown")


def _quality_warnings(
    media: MediaInfo,
    transcript: TranscriptInfo,
    acoustic: Any,
    config: AnalysisConfig,
) -> list[str]:
    warnings = list(transcript.warnings) + list(acoustic.warnings)
    if config.rights_status == "unverified_internal_pilot":
        warnings.append("Source rights status has not been verified")
    if acoustic.channel_strategy.startswith("select-louder"):
        warnings.append(
            "Stereo channels were not near-identical; one channel was selected "
            "and speaker/event attribution is uncertain"
        )
    clipping = acoustic.metrics.clipping_fraction
    if clipping is not None and clipping > 0.02:
        warnings.append("Clipping exceeds 2%; level-based events may be unreliable")
    return list(dict.fromkeys(warnings))


def _source_payload(
    media: MediaInfo,
    config: AnalysisConfig,
    paths: MetricsPaths,
) -> dict[str, Any]:
    return {
        "source_id": Path(media.path).stem,
        "filename": media.filename,
        "path": media.path,
        "sha256": media.sha256,
        "duration_ms": media.duration_ms,
        "size_bytes": media.size_bytes,
        "format_name": media.format_name,
        "has_audio": media.has_audio,
        "has_video": media.has_video,
        "audio": asdict(media.audio) if media.audio is not None else None,
        "rights_status": config.rights_status,
        "permission_reference": config.permission_reference,
        "metrics_filename": paths.latest_text.name,
        "metrics_json": str(paths.latest_json),
        "metrics_text": str(paths.latest_text),
    }


def _no_audio_result(
    *,
    run_id: str,
    run_key: str,
    supersedes: str | None,
    media: MediaInfo,
    config: AnalysisConfig,
    paths: MetricsPaths,
    transcript: TranscriptInfo,
    transcript_sha: str | None,
    scope_start: int,
    scope_end: int,
    config_payload: dict[str, Any],
    config_hash: str,
    model_manifest_hash: str,
    model_manifest: dict[str, object],
    vad_metadata: dict[str, object],
) -> AnalysisResult:
    placeholder = AcousticMetrics(
        active_speech_median_dbfs=None,
        active_speech_p95_dbfs=None,
        clipping_fraction=None,
        median_speech_rate_wpm=None,
        median_pause_ratio=None,
        f0_variation_semitones=None,
        overlap_fraction=None,
        anonymous_speaker_count=None,
        active_speech_duration_ms=0,
        speech_ratio=0.0,
        activity_method="not_assessable_no_audio",
        activity_threshold_dbfs=-120.0,
        activity_noise_floor_dbfs=-120.0,
        channel_strategy="not_available",
        f0_median_hz=None,
        activity_backend=str(vad_metadata["backend"]),
        activity_probability_threshold=vad_metadata["probability_threshold"],
    )
    return AnalysisResult(
        schema_version="0.1.0",
        artifact_role="per_audio_latest_metrics",
        run_id=run_id,
        run_key=run_key,
        supersedes_run_id=supersedes,
        analysis_status="not_assessable",
        created_at=utc_now(),
        history={"jsonl": paths.history_jsonl.name, "text": paths.history_text.name},
        source=_source_payload(media, config, paths),
        scope={"start_ms": scope_start, "end_ms": scope_end, "language": config.language},
        pipeline={
            "version": __version__,
            "config": config_payload,
            "config_hash": config_hash,
            "model_manifest_hash": model_manifest_hash,
            "models": model_manifest,
            "offline": config.offline,
        },
        quality={
            "overall": "poor",
            "activity_method": "not_assessable_no_audio",
            "activity_confidence": "low",
            "activity_backend": vad_metadata["backend"],
            "vad_model": vad_metadata,
            "transcript": transcript.format,
            "transcript_path": transcript.path,
            "transcript_sha256": transcript_sha,
            "transcript_alignment": "unavailable",
            "diarization": "not_enabled",
            "channel_strategy": "not_available",
            "channel_metrics": {},
            "warnings": [
                "The accepted media has no audio stream; speech metrics are not assessable"
            ],
        },
        measurements=placeholder,
        summary={
            "speech_present": "uncertain",
            "speech_ratio": 0.0,
            "candidate_event_count": 0,
            "status_note": "no audio stream; no speech or vividness claim was made",
        },
        vividness=VividnessResult(
            score=None,
            expected_rating=None,
            status="not_assessable",
            confidence_band="low",
            prediction_interval_80=None,
            components={},
            explanation="The media has no audio stream, so a vividness score is not assessable",
        ),
        events=(),
        errors=(),
    )


def _failed_result(
    *,
    run_id: str,
    run_key: str,
    supersedes: str | None,
    media: MediaInfo,
    config: AnalysisConfig,
    paths: MetricsPaths,
    transcript: TranscriptInfo,
    transcript_sha: str | None,
    scope_start: int,
    scope_end: int,
    config_payload: dict[str, Any],
    config_hash: str,
    model_manifest_hash: str,
    model_manifest: dict[str, object],
    vad_metadata: dict[str, object],
    error: Exception,
) -> AnalysisResult:
    placeholder = AcousticMetrics(
        active_speech_median_dbfs=None,
        active_speech_p95_dbfs=None,
        clipping_fraction=None,
        median_speech_rate_wpm=None,
        median_pause_ratio=None,
        f0_variation_semitones=None,
        overlap_fraction=None,
        anonymous_speaker_count=None,
        active_speech_duration_ms=0,
        speech_ratio=0.0,
        activity_method="not_completed",
        activity_threshold_dbfs=-120.0,
        activity_noise_floor_dbfs=-120.0,
        channel_strategy="not_completed",
        f0_median_hz=None,
        activity_backend=str(vad_metadata["backend"]),
        activity_probability_threshold=vad_metadata["probability_threshold"],
    )
    return AnalysisResult(
        schema_version="0.1.0",
        artifact_role="per_audio_latest_metrics",
        run_id=run_id,
        run_key=run_key,
        supersedes_run_id=supersedes,
        analysis_status="failed",
        created_at=utc_now(),
        history={"jsonl": paths.history_jsonl.name, "text": paths.history_text.name},
        source=_source_payload(media, config, paths),
        scope={"start_ms": scope_start, "end_ms": scope_end, "language": config.language},
        pipeline={
            "version": __version__,
            "config": config_payload,
            "config_hash": config_hash,
            "model_manifest_hash": model_manifest_hash,
            "models": model_manifest,
            "offline": config.offline,
        },
        quality={
            "overall": "poor",
            "activity_method": "not_completed",
            "activity_confidence": "low",
            "activity_backend": vad_metadata["backend"],
            "vad_model": vad_metadata,
            "transcript": transcript.format,
            "transcript_path": transcript.path,
            "transcript_sha256": transcript_sha,
            "transcript_alignment": "unavailable",
            "diarization": "not_enabled",
            "channel_strategy": "not_completed",
            "channel_metrics": {},
            "warnings": list(transcript.warnings),
        },
        measurements=placeholder,
        summary={
            "speech_present": "uncertain",
            "speech_ratio": 0.0,
            "candidate_event_count": 0,
            "status_note": "analysis failed; metrics are not assessable",
        },
        vividness=VividnessResult(
            score=None,
            expected_rating=None,
            status="not_assessable",
            confidence_band="low",
            prediction_interval_80=None,
            components={},
            explanation="Analysis failed before a valid speech assessment was available",
        ),
        events=(),
        errors=(
            {
                "stage": "analysis",
                "code": type(error).__name__,
                "message": str(error),
                "retryable": True,
            },
        ),
    )


def _snapshot_to_result(snapshot: dict[str, Any]) -> AnalysisResult:
    # This lightweight adapter is used only to return the existing latest record
    # from an idempotent no-op. It intentionally preserves the persisted schema.
    return AnalysisResult(
        schema_version=str(snapshot.get("schema_version", "0.1.0")),
        artifact_role=str(snapshot.get("artifact_role", "per_audio_latest_metrics")),
        run_id=str(snapshot.get("run_id")),
        run_key=str(snapshot.get("run_key")),
        supersedes_run_id=snapshot.get("supersedes_run_id"),
        analysis_status=snapshot.get("analysis_status", "failed"),
        created_at=str(snapshot.get("created_at")),
        history=dict(snapshot.get("history", {})),
        source=dict(snapshot.get("source", {})),
        scope=dict(snapshot.get("scope", {})),
        pipeline=dict(snapshot.get("pipeline", {})),
        quality=dict(snapshot.get("quality", {})),
        measurements=AcousticMetrics(**snapshot.get("measurements", {})),
        summary=dict(snapshot.get("summary", {})),
        vividness=VividnessResult(**snapshot.get("vividness", {})),
        events=tuple(CandidateEvent(**event) for event in snapshot.get("events", [])),
        speaker_summaries=tuple(
            SpeakerSummary(**speaker) for speaker in snapshot.get("speaker_summaries", [])
        ),
        errors=tuple(snapshot.get("errors", [])),
        review=dict(snapshot.get("review", {})),
    )
