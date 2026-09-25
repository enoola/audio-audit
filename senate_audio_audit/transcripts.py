from __future__ import annotations

import html
import re
from pathlib import Path

from .errors import InputError
from .models import TranscriptCue, TranscriptInfo
from .util import parse_timestamp_ms, read_text

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TIMESTAMP_LINE_RE = re.compile(
    r"(?P<start>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})"
)
_WORD_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿ]+(?:['’\-][\wÀ-ÖØ-öø-ÿ]+)*", re.UNICODE)


def discover_transcript(
    media_path: Path,
    explicit_path: Path | None = None,
    duration_ms: int | None = None,
) -> TranscriptInfo:
    if explicit_path is not None:
        if str(explicit_path).lower().endswith(".sentiment.srt"):
            raise InputError("Refusing to use a *.sentiment.srt artifact as a primary transcript")
        transcript_path = explicit_path
    else:
        transcript_path = media_path.with_suffix(".srt")
        if not transcript_path.exists():
            transcript_path = media_path.with_suffix(".vtt")

    if not transcript_path.exists():
        if explicit_path is not None:
            raise InputError(f"Explicit transcript does not exist: {transcript_path}")
        text_path = media_path.with_suffix(".txt")
        if text_path.exists():
            text = read_text(text_path).strip()
            return TranscriptInfo(
                path=str(text_path.resolve()),
                format="txt",
                language=None,
                cues=(),
                warnings=("Only an untimed TXT transcript is available",),
                original_end_ms=0,
                clamped_end_ms=0,
            )
        return TranscriptInfo(
            path=None,
            format=None,
            language=None,
            cues=(),
            warnings=("No SRT, VTT, or TXT transcript sidecar was found",),
            original_end_ms=0,
            clamped_end_ms=0,
        )

    if str(transcript_path).lower().endswith(".sentiment.srt"):
        raise InputError("Refusing to use a *.sentiment.srt artifact as a primary transcript")

    suffix = transcript_path.suffix.lower()
    text = read_text(transcript_path)
    if suffix == ".srt":
        return parse_srt(text, transcript_path, duration_ms)
    if suffix == ".vtt":
        return parse_vtt(text, transcript_path, duration_ms)
    raise InputError(f"Unsupported transcript format: {suffix}")


def parse_srt(text: str, path: Path, duration_ms: int | None = None) -> TranscriptInfo:
    cues, warnings, original_max_end = _parse_cue_blocks(text, duration_ms, format_name="srt")
    if not cues:
        warnings.append("No valid timed cues were parsed")
    return TranscriptInfo(
        path=str(path.resolve()),
        format="srt",
        language=None,
        cues=tuple(cues),
        warnings=tuple(warnings),
        original_end_ms=original_max_end,
        clamped_end_ms=max((cue.end_ms for cue in cues), default=0),
    )


def parse_vtt(text: str, path: Path, duration_ms: int | None = None) -> TranscriptInfo:
    cues, warnings, original_max_end = _parse_cue_blocks(text, duration_ms, format_name="vtt")
    if text.lstrip().upper().startswith("WEBVTT"):
        warnings.append("WEBVTT header parsed")
    if not cues:
        warnings.append("No valid timed cues were parsed")
    return TranscriptInfo(
        path=str(path.resolve()),
        format="vtt",
        language=None,
        cues=tuple(cues),
        warnings=tuple(warnings),
        original_end_ms=original_max_end,
        clamped_end_ms=max((cue.end_ms for cue in cues), default=0),
    )


def _parse_cue_blocks(
    text: str,
    duration_ms: int | None,
    format_name: str,
) -> tuple[list[TranscriptCue], list[str], int]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    blocks = re.split(r"\n{2,}", normalized.strip()) if normalized.strip() else []
    cues: list[TranscriptCue] = []
    warnings: list[str] = []
    previous_end = 0
    total_overlap = 0
    original_max_end = 0

    for block_number, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        upper = {line.upper() for line in lines}
        if format_name == "vtt" and any(
            line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) for line in lines
        ):
            continue

        timestamp_index = next(
            (index for index, line in enumerate(lines) if _TIMESTAMP_LINE_RE.search(line)),
            None,
        )
        if timestamp_index is None:
            if format_name == "vtt" and not upper:
                continue
            warnings.append(f"Skipped block {block_number}: no timestamp line")
            continue

        match = _TIMESTAMP_LINE_RE.search(lines[timestamp_index])
        if match is None:
            continue
        try:
            start_ms = parse_timestamp_ms(match.group("start"))
            end_ms = parse_timestamp_ms(match.group("end"))
        except ValueError as exc:
            warnings.append(f"Skipped block {block_number}: {exc}")
            continue
        if end_ms <= start_ms:
            warnings.append(f"Skipped block {block_number}: end is not after start")
            continue
        if start_ms < previous_end:
            total_overlap += max(0, previous_end - start_ms)
        previous_end = max(previous_end, end_ms)

        original_end = end_ms
        original_max_end = max(original_max_end, original_end)
        if duration_ms is not None and end_ms > duration_ms:
            warnings.append(
                f"Clamped cue {block_number} end from {original_end} ms to {duration_ms} ms"
            )
            end_ms = duration_ms
            start_ms = min(start_ms, end_ms)
        if end_ms <= start_ms:
            warnings.append(f"Dropped cue {block_number}: entirely outside media duration")
            continue

        payload_lines = lines[timestamp_index + 1 :]
        payload = html.unescape(_HTML_TAG_RE.sub("", " ".join(payload_lines)))
        payload = re.sub(r"\s+", " ", payload).strip()
        if not payload:
            warnings.append(f"Dropped cue {block_number}: empty text")
            continue
        cues.append(
            TranscriptCue(
                index=len(cues) + 1,
                start_ms=start_ms,
                end_ms=end_ms,
                text=payload,
            )
        )

    if total_overlap:
        warnings.append(f"Transcript contains {total_overlap} ms of overlapping cues")
    return cues, warnings, original_max_end


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def cue_word_count(cues: tuple[TranscriptCue, ...] | list[TranscriptCue]) -> int:
    return sum(count_words(cue.text) for cue in cues)


def context_text(
    cues: tuple[TranscriptCue, ...], center_index: int, before: int = 1, after: int = 1
) -> str:
    start = max(0, center_index - before)
    end = min(len(cues), center_index + after + 1)
    return " ".join(cue.text for cue in cues[start:end])


def transcript_intervals(transcript: TranscriptInfo) -> list[tuple[int, int]]:
    return [(cue.start_ms, cue.end_ms) for cue in transcript.cues]
