from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from .errors import AnalysisError, InputError
from .models import AudioStreamInfo, MediaInfo
from .util import sha256_file

MODEL_SAMPLE_RATE = 16_000


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise AnalysisError(f"Required executable not found on PATH: {name}")
    return executable


def probe_media(path: Path) -> MediaInfo:
    if not path.exists():
        raise InputError(f"Input does not exist: {path}")
    if not path.is_file():
        raise InputError(f"Input is not a regular file: {path}")
    if path.stat().st_size == 0:
        raise InputError(f"Input is empty: {path}")

    ffprobe = require_tool("ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name,size:stream=index,codec_type,codec_name,sample_rate,channels,channel_layout",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        message = process.stderr.strip() or "unknown ffprobe error"
        raise InputError(f"ffprobe could not read {path}: {message}")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise AnalysisError("ffprobe returned invalid JSON") from exc

    streams = payload.get("streams") or []
    format_info = payload.get("format") or {}
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not audio_streams:
        return MediaInfo(
            path=str(path.resolve()),
            filename=path.name,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            duration_ms=_duration_ms(format_info),
            format_name=format_info.get("format_name"),
            has_audio=False,
            has_video=bool(video_streams),
            audio=None,
        )

    duration_ms = _duration_ms(format_info)

    first_audio = audio_streams[0]
    audio = AudioStreamInfo(
        codec=first_audio.get("codec_name"),
        sample_rate=_safe_int(first_audio.get("sample_rate")),
        channels=_safe_int(first_audio.get("channels")),
        channel_layout=first_audio.get("channel_layout"),
    )
    if audio.channels is None or audio.channels < 1:
        raise AnalysisError("Could not determine audio channel count")

    try:
        logical_size = int(format_info.get("size") or path.stat().st_size)
    except (TypeError, ValueError):
        logical_size = path.stat().st_size

    return MediaInfo(
        path=str(path.resolve()),
        filename=path.name,
        sha256=sha256_file(path),
        size_bytes=logical_size,
        duration_ms=duration_ms,
        format_name=format_info.get("format_name"),
        has_audio=True,
        has_video=bool(video_streams),
        audio=audio,
    )


def _duration_ms(format_info: dict[str, object]) -> int:
    try:
        duration_ms = int(round(float(format_info.get("duration", 0.0)) * 1000))
    except (TypeError, ValueError) as exc:
        raise AnalysisError("Could not determine media duration") from exc
    if duration_ms <= 0:
        raise AnalysisError("Media duration is invalid")
    return duration_ms


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def choose_analysis_channel(
    path: Path,
    channels: int,
    start_ms: int = 0,
    probe_ms: int = 120_000,
) -> tuple[str, int | None, dict[str, float]]:
    """Choose a mono policy while preserving phase-cancellation resistance.

    Near-identical channels are safely averaged. Otherwise, one channel is selected
    and the strategy is recorded as uncertain. This is a transparent MVP policy,
    not source-separation.
    """
    if channels == 1:
        return "mono", 0, {"correlation": 1.0, "channel_rms_difference_db": 0.0}

    end_ms = start_ms + probe_ms
    pcm = _decode_pcm_range(path, start_ms, end_ms, channels=channels)
    if pcm.size == 0:
        return "channel-0-fallback", 0, {"correlation": 0.0, "channel_rms_difference_db": 0.0}

    left = pcm[:, 0].astype(np.float64, copy=False)
    right = pcm[:, 1].astype(np.float64, copy=False)
    left_rms = float(np.sqrt(np.mean(np.square(left))) + 1e-12)
    right_rms = float(np.sqrt(np.mean(np.square(right))) + 1e-12)
    difference_db = float(20.0 * np.log2(max(left_rms, right_rms) / min(left_rms, right_rms)))
    correlation = float(np.corrcoef(left, right)[0, 1]) if left.size > 1 else 0.0
    if not np.isfinite(correlation):
        correlation = 0.0

    metrics = {
        "correlation": correlation,
        "channel_rms_difference_db": difference_db,
        "left_rms": left_rms,
        "right_rms": right_rms,
    }
    if correlation >= 0.98 and difference_db <= 1.5:
        return "average-near-identical", None, metrics
    channel = 0 if left_rms >= right_rms else 1
    return "select-louder-channel", channel, metrics


def iter_pcm_chunks(
    path: Path,
    channels: int,
    start_ms: int = 0,
    end_ms: int | None = None,
    chunk_seconds: float = 60.0,
) -> Iterator[np.ndarray]:
    """Yield decoded float32 PCM chunks without loading an entire recording."""
    ffmpeg = require_tool("ffmpeg")
    frames_per_chunk = max(1, int(MODEL_SAMPLE_RATE * chunk_seconds))
    command = [ffmpeg, "-nostdin", "-v", "error", "-i", str(path)]
    if start_ms > 0:
        command.extend(["-ss", f"{start_ms / 1000:.3f}"])
    command.extend(["-map", "0:a:0", "-ar", str(MODEL_SAMPLE_RATE)])
    if end_ms is not None:
        command.extend(["-t", f"{max(0, end_ms - start_ms) / 1000:.3f}"])
    command.extend(["-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"])

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        process.kill()
        raise AnalysisError("Could not open FFmpeg stdout")
    bytes_per_frame = channels * 4
    completed = False
    try:
        while True:
            payload = process.stdout.read(frames_per_chunk * bytes_per_frame)
            if not payload:
                completed = True
                break
            usable = len(payload) - (len(payload) % bytes_per_frame)
            if usable <= 0:
                continue
            array = np.frombuffer(payload[:usable], dtype="<f4")
            yield array.reshape(-1, channels).copy()
    finally:
        if not completed and process.poll() is None:
            process.terminate()
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr else b""
        if process.stderr:
            process.stderr.close()
        return_code = process.wait()
        if completed and return_code != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise AnalysisError(f"FFmpeg decode failed: {message or return_code}")


def _decode_pcm_range(path: Path, start_ms: int, end_ms: int, channels: int) -> np.ndarray:
    chunks = list(
        iter_pcm_chunks(
            path,
            channels=channels,
            start_ms=start_ms,
            end_ms=end_ms,
            chunk_seconds=120.0,
        )
    )
    if not chunks:
        return np.empty((0, channels), dtype=np.float32)
    return np.concatenate(chunks, axis=0)


def select_or_average_mono(chunk: np.ndarray, strategy: str, channel: int | None) -> np.ndarray:
    if chunk.ndim == 1:
        return chunk.astype(np.float32, copy=False)
    if chunk.shape[1] == 1:
        return chunk[:, 0]
    if strategy == "average-near-identical" or channel is None:
        return np.mean(chunk, axis=1).astype(np.float32)
    return chunk[:, min(channel, chunk.shape[1] - 1)].astype(np.float32, copy=False)
