from __future__ import annotations

import math

from .acoustics import AcousticAnalysis
from .models import CandidateEvent, SpeechInterval, VividnessResult


def calculate_vividness(
    acoustic: AcousticAnalysis,
    events: tuple[CandidateEvent, ...],
) -> VividnessResult:
    if acoustic.activity.speech_present == "no" or acoustic.activity.speech_duration_ms <= 0:
        return VividnessResult(
            score=None,
            expected_rating=None,
            status="not_assessable",
            confidence_band="low",
            prediction_interval_80=None,
            components={
                "peak": None,
                "coverage": None,
                "persistence": None,
                "high_event_duration_ms": 0,
                "longest_high_event_ms": 0,
            },
            explanation=(
                "No assessable speech was localized; a numeric vividness score would be misleading"
            ),
        )

    intensities = [event_intensity(event) for event in events]
    if intensities:
        peak_normalized = (0.65 * max(intensities) + 0.35 * _percentile(intensities, 90)) / 10.0
    else:
        peak_normalized = 0.0

    high_intervals = tuple(
        SpeechInterval(event.start_ms, event.end_ms)
        for event in events
        if event_intensity(event) >= 6.0
    )
    merged = _merge_intervals(high_intervals)
    high_duration = sum(interval.end_ms - interval.start_ms for interval in merged)
    longest = max((interval.end_ms - interval.start_ms for interval in merged), default=0)
    active_ms = max(1, acoustic.activity.speech_duration_ms)
    coverage = min(1.0, high_duration / max(1.0, 0.15 * active_ms))
    persistence = min(1.0, longest / 30_000.0)
    expected = 10.0 * (0.55 * peak_normalized + 0.25 * coverage + 0.20 * persistence)
    expected = max(0.0, min(10.0, expected))
    score = int(math.floor(expected + 0.5))

    has_transcript = bool(acoustic.cue_rates)
    confidence = "medium" if has_transcript and acoustic.activity.speech_present == "yes" else "low"
    if score == 0:
        explanation = (
            "Assessable speech was found, but no qualifying expressive event cleared "
            "the provisional threshold"
        )
    else:
        explanation = (
            "Provisional combination of robust peak event intensity, high-event coverage, "
            "and persistence; not calibrated to human whole-recording ratings"
        )
    return VividnessResult(
        score=score,
        expected_rating=round(expected, 4),
        status="provisional_rubric",
        confidence_band=confidence,
        prediction_interval_80=None,
        components={
            "peak": round(peak_normalized, 6),
            "coverage": round(coverage, 6),
            "persistence": round(persistence, 6),
            "high_event_duration_ms": high_duration,
            "longest_high_event_ms": longest,
        },
        explanation=explanation,
    )


def event_intensity(event: CandidateEvent) -> float:
    scores = event.signal_strengths
    return max(
        10.0 * (scores.get("relative_loud_delivery") or 0.0),
        9.0 * (scores.get("high_arousal_forceful_delivery") or 0.0),
        9.0 * (scores.get("perceived_anger_expression") or 0.0),
        6.0 * (scores.get("potentially_disrespectful_act") or 0.0),
        5.0 * (scores.get("sarcasm_or_irony_candidate") or 0.0),
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _merge_intervals(intervals: tuple[SpeechInterval, ...]) -> tuple[SpeechInterval, ...]:
    if not intervals:
        return ()
    ordered = sorted(intervals, key=lambda item: item.start_ms)
    merged = [ordered[0]]
    for interval in ordered[1:]:
        previous = merged[-1]
        if interval.start_ms <= previous.end_ms:
            merged[-1] = SpeechInterval(previous.start_ms, max(previous.end_ms, interval.end_ms))
        else:
            merged[-1] = previous
            merged.append(interval)
    return tuple(merged)
