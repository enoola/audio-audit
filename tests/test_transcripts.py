from __future__ import annotations

from pathlib import Path

import pytest
from senate_audio_audit.errors import InputError
from senate_audio_audit.transcripts import discover_transcript, parse_srt, parse_vtt


def test_parse_srt_bom_multiline_and_drift(tmp_path: Path) -> None:
    path = tmp_path / "captions.srt"
    path.write_text(
        "\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\nPremière ligne\r\nseconde ligne\r\n\r\n"
        "00:00:03,000 --> 00:00:04,000\r\n<b>Bonjour</b> &amp; merci\r\n",
        encoding="utf-8",
    )
    result = parse_srt(path.read_text(encoding="utf-8"), path, duration_ms=3500)
    assert [(c.start_ms, c.end_ms, c.text) for c in result.cues] == [
        (1000, 2500, "Première ligne seconde ligne"),
        (3000, 3500, "Bonjour & merci"),
    ]
    assert result.original_end_ms == 4000
    assert result.clamped_end_ms == 3500
    assert any("Clamped cue 2" in warning for warning in result.warnings)


def test_parse_vtt_ignores_metadata_blocks(tmp_path: Path) -> None:
    path = tmp_path / "captions.vtt"
    text = """WEBVTT

NOTE this is metadata

STYLE
::cue { color: white; }

cue-1
00:00:01.000 --> 00:00:02.000 align:start position:10%
Bonjour
"""
    result = parse_vtt(text, path)
    assert len(result.cues) == 1
    assert result.cues[0].text == "Bonjour"
    assert result.format == "vtt"


def test_discovery_prefers_srt(tmp_path: Path) -> None:
    media = tmp_path / "speech.mp3"
    media.write_bytes(b"not decoded here")
    (tmp_path / "speech.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nSRT\n", encoding="utf-8"
    )
    (tmp_path / "speech.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nVTT\n", encoding="utf-8"
    )
    result = discover_transcript(media, duration_ms=1000)
    assert result.format == "srt"
    assert result.cues[0].text == "SRT"


def test_sentiment_srt_is_rejected(tmp_path: Path) -> None:
    media = tmp_path / "speech.mp3"
    media.write_bytes(b"x")
    sentiment = tmp_path / "speech.sentiment.srt"
    sentiment.write_text("1\n00:00:00,000 --> 00:00:01,000\nNEUTRAL\n", encoding="utf-8")
    with pytest.raises(InputError):
        discover_transcript(media, explicit_path=sentiment, duration_ms=1000)
