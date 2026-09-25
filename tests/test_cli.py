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


def test_visualize_without_a_snapshot_names_analyze(
    tmp_path: Path, synthetic_wav_factory, capsys
) -> None:
    media = synthetic_wav_factory("unanalysed.wav", duration_seconds=4.0)
    exit_code = main(
        [
            "visualize",
            str(media),
            "--metrics-dir",
            str(tmp_path / "metrics"),
            "--input-root",
            str(tmp_path),
            "--no-browser",
        ]
    )
    assert exit_code == 2
    error = capsys.readouterr().err
    assert "analyze" in error


def test_visualize_serves_a_committed_snapshot(
    tmp_path: Path,
    synthetic_wav_factory,
    synthetic_srt_factory,
    capsys,
    monkeypatch,
) -> None:
    media = synthetic_wav_factory("viz.wav", duration_seconds=6.0)
    synthetic_srt_factory(media, start="00:00:01,000", end="00:00:05,000")
    metrics = tmp_path / "metrics"
    main(
        [
            "analyze",
            str(media),
            "--metrics-dir",
            str(metrics),
            "--analyses-dir",
            str(tmp_path / "analyses"),
            "--text-report",
            str(tmp_path / "ledger.txt"),
            "--input-root",
            str(tmp_path),
            "--rights-status",
            "approved_test",
        ]
    )
    capsys.readouterr()

    served: list[str] = []

    class _Session:
        def __init__(self, state, config):
            served.append(state.snapshot["source"]["filename"])
            self.url = "http://127.0.0.1:0/?t=test"
            self.closed = __import__("threading").Event()
            self.closed.set()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def wait_closed(self, poll_seconds=0.5):
            return None

    monkeypatch.setattr("senate_audio_audit.visualizer.server.VisualizerSession", _Session)

    exit_code = main(
        [
            "visualize",
            str(media),
            "--metrics-dir",
            str(metrics),
            "--input-root",
            str(tmp_path),
            "--no-browser",
            "--idle-timeout",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert served == ["viz.wav"]
    assert "read-only" in out
    assert ".timeline.json" in out
    assert list(metrics.glob("*.timeline.json"))


def test_visualize_warns_when_the_media_does_not_match_the_snapshot(
    tmp_path: Path,
    synthetic_wav_factory,
    synthetic_srt_factory,
    capsys,
    monkeypatch,
) -> None:
    """A stale snapshot paired with changed audio must not pass silently."""
    media = synthetic_wav_factory("drift.wav", duration_seconds=6.0)
    synthetic_srt_factory(media, start="00:00:01,000", end="00:00:05,000")
    metrics = tmp_path / "metrics"
    main(
        [
            "analyze",
            str(media),
            "--metrics-dir",
            str(metrics),
            "--analyses-dir",
            str(tmp_path / "analyses"),
            "--text-report",
            str(tmp_path / "ledger.txt"),
            "--input-root",
            str(tmp_path),
            "--rights-status",
            "approved_test",
        ]
    )
    capsys.readouterr()

    # Rewrite the committed snapshot with a different media hash. The current pointer
    # takes precedence during recovery, so it and the run report are updated too.
    latest = next(metrics.glob("*.metrics.json"))
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["source"]["sha256"] = "b" * 64
    latest.write_text(json.dumps(payload), encoding="utf-8")
    for pointer in metrics.glob(".*.current.json"):
        pointer.unlink()
    for run_report in (tmp_path / "analyses").glob("*/report.json"):
        run_report.write_text(json.dumps(payload), encoding="utf-8")

    class _Session:
        def __init__(self, state, config):
            self.url = "http://127.0.0.1:0/"
            self.closed = __import__("threading").Event()
            self.closed.set()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def wait_closed(self, poll_seconds=0.5):
            return None

    monkeypatch.setattr("senate_audio_audit.visualizer.server.VisualizerSession", _Session)
    main(
        [
            "visualize",
            str(media),
            "--metrics-dir",
            str(metrics),
            "--input-root",
            str(tmp_path),
            "--no-browser",
            "--idle-timeout",
            "0",
        ]
    )
    assert "SHA-256 does not match" in capsys.readouterr().err


def test_visualize_refuses_a_failed_snapshot(
    tmp_path: Path, synthetic_wav_factory, capsys, monkeypatch
) -> None:
    media = synthetic_wav_factory("failed.wav", duration_seconds=4.0)
    metrics = tmp_path / "metrics"
    latest = metrics / "failed.metrics.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "artifact_role": "per_audio_latest_metrics",
                "run_id": "r1",
                "run_key": "k" * 64,
                "analysis_status": "failed",
                "source": {"filename": "failed.wav", "sha256": "c" * 64},
                "scope": {"start_ms": 0, "end_ms": 4000},
                "errors": [{"stage": "decode", "message": "corrupt frame"}],
            }
        ),
        encoding="utf-8",
    )
    exit_code = main(
        [
            "visualize",
            str(media),
            "--metrics-dir",
            str(metrics),
            "--input-root",
            str(tmp_path),
            "--no-browser",
        ]
    )
    assert exit_code == 2
    error = capsys.readouterr().err
    assert "failed" in error
    assert "corrupt frame" in error
