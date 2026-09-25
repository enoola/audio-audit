from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def synthetic_wav_factory(tmp_path: Path):
    def factory(
        name: str = "sample.wav",
        *,
        duration_seconds: float = 12.0,
        channels: int = 1,
        silent: bool = False,
        stereo_phase_inverted: bool = False,
    ) -> Path:
        sample_rate = 16_000
        count = int(duration_seconds * sample_rate)
        time = np.arange(count, dtype=np.float64) / sample_rate
        signal = np.zeros(count, dtype=np.float64)
        if not silent:
            # Two speech-like bursts separated by silence.
            for start, end in ((3.0, 5.5), (7.0, 9.5)):
                mask = (time >= start) & (time < end)
                envelope = np.sin(np.pi * (time[mask] - start) / (end - start)) ** 2
                signal[mask] = 0.16 * envelope * np.sin(2 * np.pi * 220 * time[mask])
        pcm = np.clip(signal, -1.0, 1.0)
        if channels == 1:
            interleaved = pcm
        else:
            left = pcm
            right = -pcm if stereo_phase_inverted else pcm
            interleaved = np.column_stack([left, right]).reshape(-1)
        pcm16 = (interleaved * 32767.0).astype("<i2")
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm16.tobytes())
        return path

    return factory


@pytest.fixture
def synthetic_srt_factory(tmp_path: Path):
    def factory(
        media_path: Path,
        *,
        text: str = "Voici une phrase de démonstration avec plusieurs mots.",
        start: str = "00:00:02,500",
        end: str = "00:00:10,000",
    ) -> Path:
        path = media_path.with_suffix(".srt")
        path.write_text(
            f"1\n{start} --> {end}\n{text}\n",
            encoding="utf-8",
        )
        return path

    return factory


@pytest.fixture
def sine_wav_factory(tmp_path: Path):
    def factory(
        name: str,
        *,
        seconds: float,
        frequency: float,
        amplitude: float,
        channels: int = 1,
    ) -> Path:
        sample_rate = 16_000
        t = np.arange(int(seconds * sample_rate)) / sample_rate
        mono = amplitude * np.sin(2 * math.pi * frequency * t)
        data = mono if channels == 1 else np.column_stack([mono, mono]).reshape(-1)
        pcm = (data * 32767).astype("<i2")
        path = tmp_path / name
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm.tobytes())
        return path

    return factory
