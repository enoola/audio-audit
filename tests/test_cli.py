from __future__ import annotations

import json
from pathlib import Path

from senate_audio_audit.cli import main


def test_doctor(capsys) -> None:
    assert main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ffmpeg"]
    assert payload["offline_default"] is True


def test_inspect_and_analyze_cli(
    tmp_path: Path, synthetic_wav_factory, synthetic_srt_factory, capsys
) -> None:
    media = synthetic_wav_factory("cli.wav", duration_seconds=6.0)
    synthetic_srt_factory(media, start="00:00:01,000", end="00:00:05,000")
    metrics = tmp_path / "metrics"
    analyses = tmp_path / "analyses"
    ledger = tmp_path / "observations.txt"

    assert main(["inspect", str(media)]) == 0
    inspect = json.loads(capsys.readouterr().out)
    assert inspect["media"]["has_audio"] is True

    exit_code = main(
        [
            "analyze",
            str(media),
            "--metrics-dir",
            str(metrics),
            "--analyses-dir",
            str(analyses),
            "--text-report",
            str(ledger),
            "--input-root",
            str(tmp_path),
            "--rights-status",
            "approved_test",
        ]
    )
    assert exit_code in {0, 3}
    result = json.loads(capsys.readouterr().out)
    assert Path(result["metrics_json"]).exists()
    assert Path(result["metrics_text"]).exists()
    assert ledger.exists()
