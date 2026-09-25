from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .util import format_timestamp_ms

_LIMITATION = (
    "Local speaker IDs are not identities. Outputs are estimates of observable or "
    "perceived behavior, not internal emotion, intent, diagnosis, or moral character."
)


def render_metrics_text(snapshot: dict[str, Any]) -> str:
    source = snapshot.get("source", {})
    scope = snapshot.get("scope", {})
    quality = snapshot.get("quality", {})
    measurements = snapshot.get("measurements", {})
    summary = snapshot.get("summary", {})
    vividness = snapshot.get("vividness", {})
    status = snapshot.get("analysis_status", "unknown")
    filename = source.get("filename", "unknown")
    lines = [
        "=" * 80,
        f"PER-AUDIO METRICS: {source.get('metrics_filename', filename + '.metrics.txt')}",
        f"FILE: {filename}",
        f"RUN: {snapshot.get('run_id', 'unknown')} | "
        f"generated: {snapshot.get('created_at', 'unknown')}",
        f"RUN STATUS: {status}",
        f"SUPERSEDES: {snapshot.get('supersedes_run_id') or 'none'}",
        f"HISTORY: {snapshot.get('history', {}).get('jsonl', 'unknown')} / "
        f"{snapshot.get('history', {}).get('text', 'unknown')}",
        f"SCOPE: {format_timestamp_ms(scope.get('start_ms', 0))}–"
        f"{format_timestamp_ms(scope.get('end_ms', 0))}",
        f"STATUS: {summary.get('status_note', 'provisional research output')}",
        "",
        "SPEECH",
        f"  Present: {summary.get('speech_present', 'unknown')}",
        f"  Active speech: {_percent(summary.get('speech_ratio'))} "
        f"({_number(measurements.get('active_speech_duration_ms'), 1)} ms; quality: "
        f"{quality.get('overall', 'unknown')})",
        f"  Activity method: {quality.get('activity_method', 'unknown')}",
        f"  VAD backend: {quality.get('activity_backend', 'unknown')}",
        f"  VAD model: {quality.get('vad_model', {}).get('model_id', 'none')}",
        f"  VAD model SHA-256: {quality.get('vad_model', {}).get('model_sha256') or 'none'}",
        f"  VAD probability threshold: "
        f"{_number(quality.get('vad_model', {}).get('probability_threshold'), 3)}",
        "",
        "MEASURED AUDIO METRICS",
        f"  Active-speech median level: "
        f"{_number(measurements.get('active_speech_median_dbfs'), 2)} dBFS",
        f"  Active-speech p95 level: {_number(measurements.get('active_speech_p95_dbfs'), 2)} dBFS",
        f"  Clipping fraction: {_number(measurements.get('clipping_fraction'), 6)}",
        f"  Median speech rate: {_number(measurements.get('median_speech_rate_wpm'), 2)} words/min",
        f"  Median pause ratio: {_number(measurements.get('median_pause_ratio'), 4)}",
        f"  F0 variation: {_number(measurements.get('f0_variation_semitones'), 3)} semitones",
        f"  Overlap fraction: {_number(measurements.get('overlap_fraction'), 4)}",
        f"  Anonymous speakers detected: {_number(measurements.get('anonymous_speaker_count'), 0)}",
        "",
        "ANONYMOUS SPEAKER SUMMARIES",
    ]
    speaker_summaries = snapshot.get("speaker_summaries", [])
    if speaker_summaries:
        for speaker in speaker_summaries:
            lines.append(
                f"  {speaker.get('speaker_id')}: active speech "
                f"{_number(speaker.get('active_speech_ms'), 0)} ms; high-delivery fraction "
                f"{_number(speaker.get('high_delivery_fraction'), 4)}; candidate events "
                f"{sum((speaker.get('candidate_event_counts') or {}).values())}"
            )
        lines.append("  Identity: not inferred; labels are local to this recording")
    else:
        lines.append("  None; diarization is not enabled in this MVP")

    lines.extend(
        [
            "",
            "AUDIO VIVIDNESS",
            f"  Score: {_score(vividness.get('score'))}",
            f"  Status: {vividness.get('status', 'unknown')}",
            f"  Confidence: {vividness.get('confidence_band', 'unknown')}",
            f"  Expected rating: {_number(vividness.get('expected_rating'), 3)}",
            f"  Note: {vividness.get('explanation', '')}",
            "",
            "CANDIDATE EVENTS",
        ]
    )
    events = snapshot.get("events", [])
    if not events:
        lines.append("  No qualifying candidate events")
    for event in events:
        lines.extend(_render_event(event))

    warnings = list(quality.get("warnings", []))
    errors = list(snapshot.get("errors", []))
    if warnings:
        lines.extend(["", "WARNINGS"])
        lines.extend(f"  - {warning}" for warning in warnings)
    if errors:
        lines.extend(["", "ERRORS"])
        lines.extend(
            f"  - {error.get('stage', 'unknown')}: {error.get('message', 'unknown')}"
            for error in errors
        )
    lines.extend(
        [
            "",
            "LIMITATIONS",
            f"  {_LIMITATION}",
            "  Decimal values are uncalibrated signal strengths until probability "
            "calibration exists.",
            "  Review the original audio before quoting or publishing this event.",
            "=" * 80,
            "",
        ]
    )
    return "\n".join(lines)


def render_global_ledger(snapshots: Iterable[dict[str, Any]]) -> str:
    lines = [
        "SENAT AUDIO AUDIT — GLOBAL OBSERVATION LEDGER",
        "Generated from per-audio canonical metrics; no internal emotion is inferred.",
        "=" * 80,
        "",
    ]
    ordered = sorted(snapshots, key=lambda item: item.get("source", {}).get("filename", ""))
    for snapshot in ordered:
        source = snapshot.get("source", {})
        summary = snapshot.get("summary", {})
        vividness = snapshot.get("vividness", {})
        lines.extend(
            [
                f"FILE: {source.get('filename', 'unknown')}",
                f"  Run: {snapshot.get('run_id', 'unknown')} | "
                f"status: {snapshot.get('analysis_status', 'unknown')}",
                f"  Speech: {summary.get('speech_present', 'unknown')}",
                f"  Vividness: {_score(vividness.get('score'))} "
                f"({vividness.get('status', 'unknown')})",
                f"  Candidate events: {len(snapshot.get('events', []))}",
                f"  Metrics: {source.get('metrics_json', 'unknown')}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_event(event: dict[str, Any]) -> list[str]:
    lines = [
        f"  {format_timestamp_ms(event.get('start_ms', 0))}–"
        f"{format_timestamp_ms(event.get('end_ms', 0))} | "
        f"{event.get('speaker_id') or 'speaker unavailable'}",
        f"    Type: {event.get('claim_type', 'unknown')}",
        f"    State: {event.get('decision_state', 'unknown')}",
    ]
    for name, value in sorted((event.get("signal_strengths") or {}).items()):
        lines.append(f"    {name}: {_signal(value)}")
    evidence = event.get("evidence") or {}
    evidence_bits = []
    if evidence.get("relative_level_db") is not None:
        evidence_bits.append(f"{_number(evidence['relative_level_db'], 2)} dB above active median")
    if evidence.get("rate_ratio") is not None:
        evidence_bits.append(f"speech-rate ratio {_number(evidence['rate_ratio'], 2)}")
    if evidence.get("subtype"):
        evidence_bits.append(f"text subtype {evidence['subtype']}")
    if evidence_bits:
        lines.append(f"    Evidence: {'; '.join(evidence_bits)}")
    if event.get("transcript"):
        lines.append(f"    Context: “{event['transcript']}”")
    lines.append(f"    Human review: {event.get('review_status', 'unreviewed')}")
    return lines


def _score(value: object) -> str:
    return "N/A" if value is None else f"{int(value)}/10"


def _number(value: object, digits: int) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _percent(value: object) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _signal(value: object) -> str:
    if value is None:
        return "not_assessable"
    return f"uncalibrated signal {_number(value, 3)}"
