from __future__ import annotations

import json
from pathlib import Path

from senate_audio_audit.artifacts import metrics_paths, read_latest
from senate_audio_audit.media import probe_media
from senate_audio_audit.pipeline import AnalysisConfig, analyze_media


def _config(tmp_path: Path, media: Path, **overrides) -> AnalysisConfig:
    values = {
        "metrics_dir": tmp_path / "metrics",
        "analyses_dir": tmp_path / "analyses",
        "text_report": tmp_path / "observations.txt",
        "input_root": tmp_path,
        "rights_status": "approved_test",
        "chunk_seconds": 0.5,
    }
    values.update(overrides)
    return AnalysisConfig(**values)


def test_end_to_end_wav_creates_metrics_and_is_idempotent(
    tmp_path: Path, synthetic_wav_factory, synthetic_srt_factory
) -> None:
    media = synthetic_wav_factory("hearing.wav", duration_seconds=12.0)
    synthetic_srt_factory(media)
    before = media.read_bytes()
    config = _config(tmp_path, media)
    first = analyze_media(media, config)
    assert first.publish.state == "published"
    assert first.result.analysis_status in {"completed", "partial", "not_assessable"}
    assert media.read_bytes() == before

    paths = metrics_paths(media, config.metrics_dir, tmp_path, probe_media(media).sha256)
    snapshot = read_latest(paths)
    assert snapshot is not None
    assert snapshot["artifact_role"] == "per_audio_latest_metrics"
    assert paths.latest_text.exists()
    assert paths.history_jsonl.exists()
    assert len(paths.history_jsonl.read_text(encoding="utf-8").splitlines()) == 1
    assert "PER-AUDIO METRICS" in paths.latest_text.read_text(encoding="utf-8")
    assert "uncalibrated" in paths.latest_text.read_text(encoding="utf-8").lower()

    latest_before = paths.latest_json.read_bytes()
    history_before = paths.history_jsonl.read_bytes()
    second = analyze_media(media, config)
    assert second.publish.state == "idempotent_noop"
    assert paths.latest_json.read_bytes() == latest_before
    assert paths.history_jsonl.read_bytes() == history_before

    forced = analyze_media(media, _config(tmp_path, media, force=True))
    assert forced.publish.state == "published"
    assert len(paths.history_jsonl.read_text(encoding="utf-8").splitlines()) == 2


def test_silence_without_transcript_is_no_speech_but_gets_artifacts(
    tmp_path: Path, synthetic_wav_factory
) -> None:
    media = synthetic_wav_factory("silence.wav", duration_seconds=3.0, silent=True)
    outcome = analyze_media(media, _config(tmp_path, media))
    assert outcome.result.analysis_status == "no_speech"
    assert outcome.result.vividness.score is None
    assert outcome.publish.paths.latest_json.exists()
    assert outcome.publish.paths.latest_text.exists()
    assert outcome.publish.paths.history_jsonl.exists()


def test_video_without_audio_gets_not_assessable_artifacts(tmp_path: Path) -> None:
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        return
    media = tmp_path / "silent-video.mp4"
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
            str(media),
        ],
        check=True,
    )
    outcome = analyze_media(media, _config(tmp_path, media, vad_backend="energy"))
    assert outcome.result.analysis_status == "not_assessable"
    assert outcome.result.vividness.score is None
    assert outcome.publish.paths.latest_json.exists()
    assert outcome.publish.paths.history_jsonl.exists()


def test_missing_transcript_is_partial(tmp_path: Path, synthetic_wav_factory) -> None:
    media = synthetic_wav_factory("no-caption.wav", duration_seconds=8.0)
    outcome = analyze_media(media, _config(tmp_path, media, vad_backend="energy"))
    assert outcome.result.analysis_status == "partial"
    snapshot = json.loads(outcome.publish.paths.latest_json.read_text(encoding="utf-8"))
    assert snapshot["quality"]["transcript"] is None
    assert all(
        event["signal_strengths"]["potentially_disrespectful_act"] is None
        and event["signal_strengths"]["sarcasm_or_irony_candidate"] is None
        for event in snapshot["events"]
    )
    assert snapshot["vividness"]["status"] == "provisional_rubric"
