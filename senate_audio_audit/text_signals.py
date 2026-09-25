from __future__ import annotations

import re
from dataclasses import dataclass

from .models import TranscriptCue
from .transcripts import context_text

_WORD_BOUNDARY = r"(?<![\wÀ-ÖØ-öø-ÿ]){}(?![\wÀ-ÖØ-öø-ÿ])"
_RULES: tuple[tuple[str, str, re.Pattern[str], float], ...] = (
    (
        "potentially_disrespectful_act",
        "degrading_label",
        re.compile(
            _WORD_BOUNDARY.format(r"(?:imbécile|idiote?|crétin(?:e|s)?|abruti(?:e|s)?)"),
            re.IGNORECASE,
        ),
        0.72,
    ),
    (
        "potentially_disrespectful_act",
        "personal_invalidation",
        re.compile(
            _WORD_BOUNDARY.format(r"(?:incompétent(?:e|s)?|inutile| incapable)"),
            re.IGNORECASE,
        ),
        0.62,
    ),
    (
        "potentially_disrespectful_act",
        "hostile_command",
        re.compile(
            _WORD_BOUNDARY.format(r"(?:taisez-vous|fermez-la|laissons-les|cessez donc)"),
            re.IGNORECASE,
        ),
        0.48,
    ),
    (
        "sarcasm_or_irony_candidate",
        "figurative_praise_or_exclamation",
        re.compile(
            r"\b(?:quel dommage|merci beaucoup|bien joué|bravo(?: à)?|"
            r"magnifique(?: performance)?)\b",
            re.IGNORECASE,
        ),
        0.34,
    ),
)
_QUOTATION_RE = re.compile(r"[«\"](?:(?![»\"]).)*[»\"]", re.DOTALL)
_REPORTED_CONTEXT_RE = re.compile(
    r"\b(?:a dit|dit-il|dit-elle|selon|propos de|citation|il a déclaré|elle a déclaré)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TextSignal:
    cue_index: int
    claim_type: str
    subtype: str
    start_ms: int
    end_ms: int
    text: str
    context: str
    matched_text: str
    signal_strength: float
    reason: str
    decision_state: str


def analyze_transcript(cues: tuple[TranscriptCue, ...]) -> tuple[TextSignal, ...]:
    signals: list[TextSignal] = []
    for cue_index, cue in enumerate(cues):
        context = context_text(cues, cue_index, before=1, after=1)
        quotation = bool(_QUOTATION_RE.search(cue.text))
        reported = bool(_REPORTED_CONTEXT_RE.search(cue.text))
        for claim_type, subtype, pattern, base_strength in _RULES:
            match = pattern.search(cue.text)
            if match is None:
                continue
            if claim_type == "potentially_disrespectful_act" and (quotation or reported):
                continue
            if claim_type == "sarcasm_or_irony_candidate":
                strength = base_strength
                decision = "uncertain_candidate"
                reason = "Figurative wording is context-dependent and requires human review"
            else:
                strength = base_strength
                decision = "uncertain_candidate"
                reason = "Lexical rule is a review prompt, not proof of disrespect"
            signals.append(
                TextSignal(
                    cue_index=cue_index,
                    claim_type=claim_type,
                    subtype=subtype,
                    start_ms=cue.start_ms,
                    end_ms=cue.end_ms,
                    text=cue.text,
                    context=context,
                    matched_text=match.group(0),
                    signal_strength=strength,
                    reason=reason,
                    decision_state=decision,
                )
            )
    return tuple(signals)


def signals_overlapping(
    signals: tuple[TextSignal, ...], start_ms: int, end_ms: int
) -> tuple[TextSignal, ...]:
    return tuple(
        signal for signal in signals if min(end_ms, signal.end_ms) > max(start_ms, signal.start_ms)
    )


def strongest_signal(signals: tuple[TextSignal, ...], claim_type: str) -> TextSignal | None:
    matching = [signal for signal in signals if signal.claim_type == claim_type]
    return max(matching, key=lambda item: item.signal_strength) if matching else None


def is_hostile_signal(signal: TextSignal | None) -> bool:
    return bool(signal and signal.claim_type == "potentially_disrespectful_act")
