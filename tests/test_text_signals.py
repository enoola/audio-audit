from __future__ import annotations

from senate_audio_audit.models import TranscriptCue
from senate_audio_audit.text_signals import analyze_transcript


def test_direct_insult_is_only_uncertain_candidate() -> None:
    cues = (TranscriptCue(1, 0, 1000, "Votre argument est imbécile."),)
    signals = analyze_transcript(cues)
    assert len(signals) == 1
    assert signals[0].claim_type == "potentially_disrespectful_act"
    assert signals[0].decision_state == "uncertain_candidate"
    assert signals[0].signal_strength < 1.0


def test_quoted_insult_is_suppressed() -> None:
    cues = (TranscriptCue(1, 0, 1000, "Il a dit « vous êtes un imbécile »."),)
    assert analyze_transcript(cues) == ()


def test_sarcasm_marker_remains_low_uncertainty() -> None:
    cues = (TranscriptCue(1, 0, 1000, "Quel dommage, quelle belle réussite."),)
    signals = analyze_transcript(cues)
    assert signals[0].claim_type == "sarcasm_or_irony_candidate"
    assert signals[0].signal_strength <= 0.4
    assert signals[0].decision_state == "uncertain_candidate"
