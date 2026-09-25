from __future__ import annotations

from collections import defaultdict

from .acoustics import LoudWindow
from .models import CandidateEvent
from .text_signals import TextSignal, signals_overlapping
from .util import stable_hash


def build_candidate_events(
    *,
    run_id: str,
    loud_windows: tuple[LoudWindow, ...],
    text_signals: tuple[TextSignal, ...],
) -> tuple[CandidateEvent, ...]:
    events: list[CandidateEvent] = []
    covered_text: set[tuple[int, str]] = set()

    for window in loud_windows:
        overlapping = signals_overlapping(text_signals, window.start_ms, window.end_ms)
        for signal in overlapping:
            covered_text.add((signal.cue_index, signal.claim_type))
        disrespect = _strongest(overlapping, "potentially_disrespectful_act")
        sarcasm = _strongest(overlapping, "sarcasm_or_irony_candidate")
        anger_signal = None
        if window.high_arousal_signal > 0 and disrespect is not None:
            anger_signal = min(
                1.0,
                0.70 * window.high_arousal_signal + 0.30 * disrespect.signal_strength,
            )
        elif window.high_arousal_signal >= 0.75 and disrespect is not None:
            anger_signal = 0.70
        if anger_signal is not None:
            claim_type = "perceived_anger_expression"
        elif window.high_arousal_signal > 0:
            claim_type = "high_arousal_forceful_delivery"
        else:
            claim_type = "relative_loud_delivery"
        strongest_text = disrespect or sarcasm
        decision = (
            "supported_candidate"
            if max(
                window.level_signal,
                window.high_arousal_signal,
                anger_signal or 0.0,
            )
            >= 0.65
            else "uncertain_candidate"
        )
        events.append(
            CandidateEvent(
                event_id=_event_id(run_id, window.start_ms, window.end_ms, claim_type),
                claim_type=claim_type,
                epistemic_status="inferred_perception",
                decision_state=decision,
                start_ms=window.start_ms,
                end_ms=window.end_ms,
                speaker_id=None,
                score_semantics="uncalibrated_signal_strength",
                calibration_id=None,
                signal_strengths={
                    "relative_loud_delivery": round(window.level_signal, 6),
                    "high_arousal_forceful_delivery": round(window.high_arousal_signal, 6),
                    "perceived_anger_expression": round(anger_signal, 6)
                    if anger_signal is not None
                    else None,
                    "potentially_disrespectful_act": round(disrespect.signal_strength, 6)
                    if disrespect
                    else None,
                    "sarcasm_or_irony_candidate": round(sarcasm.signal_strength, 6)
                    if sarcasm
                    else None,
                },
                evidence={
                    "relative_level_db": round(window.relative_level_db, 3),
                    "active_duration_ms": window.active_duration_ms,
                    "speech_rate_wpm": round(window.speech_rate_wpm, 3)
                    if window.speech_rate_wpm
                    else None,
                    "rate_ratio": round(window.rate_ratio, 3) if window.rate_ratio else None,
                    "text_subtypes": [signal.subtype for signal in overlapping],
                    "speaker_attribution": "unavailable_without_diarization",
                },
                transcript=strongest_text.text if strongest_text else None,
                model_versions={
                    "activity": "energy+transcript-v0",
                    "acoustic_events": "relative-level-rule-v0",
                    "text_events": "fr-lexical-review-rules-v0",
                },
            )
        )

    grouped: dict[tuple[int, str], list[TextSignal]] = defaultdict(list)
    for signal in text_signals:
        grouped[(signal.cue_index, signal.claim_type)].append(signal)
    for (cue_index, claim_type), signals in sorted(grouped.items()):
        if (cue_index, claim_type) in covered_text:
            continue
        strongest = max(signals, key=lambda item: item.signal_strength)
        events.append(
            CandidateEvent(
                event_id=_event_id(run_id, strongest.start_ms, strongest.end_ms, claim_type),
                claim_type=claim_type,
                epistemic_status="inferred_perception",
                decision_state="uncertain_candidate",
                start_ms=strongest.start_ms,
                end_ms=strongest.end_ms,
                speaker_id=None,
                score_semantics="uncalibrated_signal_strength",
                calibration_id=None,
                signal_strengths={
                    "relative_loud_delivery": None,
                    "high_arousal_forceful_delivery": None,
                    "perceived_anger_expression": None,
                    "potentially_disrespectful_act": round(strongest.signal_strength, 6)
                    if claim_type == "potentially_disrespectful_act"
                    else None,
                    "sarcasm_or_irony_candidate": round(strongest.signal_strength, 6)
                    if claim_type == "sarcasm_or_irony_candidate"
                    else None,
                },
                evidence={
                    "matched_text": strongest.matched_text,
                    "subtype": strongest.subtype,
                    "rule_reason": strongest.reason,
                    "speaker_attribution": "unavailable_without_diarization",
                },
                transcript=strongest.text,
                model_versions={"text_events": "fr-lexical-review-rules-v0"},
            )
        )

    return tuple(sorted(events, key=lambda item: (item.start_ms, item.end_ms, item.claim_type)))


def _strongest(signals: tuple[TextSignal, ...], claim_type: str) -> TextSignal | None:
    matching = [signal for signal in signals if signal.claim_type == claim_type]
    return max(matching, key=lambda item: item.signal_strength) if matching else None


def _event_id(run_id: str, start_ms: int, end_ms: int, claim_type: str) -> str:
    digest = stable_hash([run_id, start_ms, end_ms, claim_type], length=16)
    return f"evt-{digest}"
