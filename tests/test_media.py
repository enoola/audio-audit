from __future__ import annotations

import shutil
import subprocess

import pytest

from senate_audio_audit.media import (
    choose_analysis_channel,
    iter_pcm_chunks,
    probe_media,
    select_or_average_mono,
)


def test_probe_generated_wav(synthetic_wav_factory) -> None:
    path = synthetic_wav_factory("probe.wav", duration_seconds=2.0)
    media = probe_media(path)
    assert media.duration_ms == 2000
    assert media.has_audio is True
    assert media.has_video is False
    assert media.audio is not None
    assert media.audio.channels == 1
    assert len(media.sha256) == 64


def test_video_without_audio_is_probeable_and_marked(tmp_path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is not available")
    path = tmp_path / "silent-video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=5",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    media = probe_media(path)
    assert media.has_video is True
    assert media.has_audio is False
    assert media.audio is None


def test_near_identical_stereo_is_averaged(synthetic_wav_factory) -> None:
    path = synthetic_wav_factory("stereo.wav", duration_seconds=12.0, channels=2)
    strategy, channel, metrics = choose_analysis_channel(path, channels=2)
    assert strategy == "average-near-identical"
    assert channel is None
    assert metrics["correlation"] > 0.99
    chunks = list(iter_pcm_chunks(path, channels=2, chunk_seconds=0.5))
    mono = select_or_average_mono(chunks[0], strategy, channel)
    assert mono.ndim == 1
    assert mono.size > 0


def test_phase_inverted_stereo_selects_one_channel(synthetic_wav_factory) -> None:
    path = synthetic_wav_factory(
        "inverted.wav", duration_seconds=12.0, channels=2, stereo_phase_inverted=True
    )
    strategy, channel, metrics = choose_analysis_channel(path, channels=2)
    assert strategy == "select-louder-channel"
    assert channel in {0, 1}
    assert metrics["correlation"] < 0.9
