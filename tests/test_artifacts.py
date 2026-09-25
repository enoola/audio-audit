from __future__ import annotations

import json
from pathlib import Path

from senate_audio_audit.artifacts import metrics_paths, publish_result, read_latest
from senate_audio_audit.reporting import render_metrics_text
from tests.factories import make_result


def test_first_publish_creates_latest_pair_and_history(tmp_path: Path) -> None:
    paths = metrics_paths(
        tmp_path / "source" / "sample.mp3", tmp_path / "metrics", tmp_path, "a" * 64
    )
    result = make_result()
    outcome = publish_result(result, paths, tmp_path / "analyses")
    assert outcome.state == "published"
    assert paths.latest_json.exists()
    assert paths.latest_text.exists()
    assert paths.history_jsonl.exists()
    assert paths.history_text.exists()
    assert paths.current_pointer.exists()
    assert paths.latest_text.read_text(encoding="utf-8") == render_metrics_text(result.to_dict())
    assert len(paths.history_jsonl.read_text(encoding="utf-8").splitlines()) == 1
    assert "METRICS-HISTORY-BLOCK run_id=run-1" in paths.history_text.read_text(encoding="utf-8")


def test_idempotent_rerun_does_not_append_history(tmp_path: Path) -> None:
    paths = metrics_paths(tmp_path / "sample.mp3", tmp_path / "metrics", tmp_path, "a" * 64)
    result = make_result()
    publish_result(result, paths, tmp_path / "analyses")
    before_json = paths.latest_json.read_bytes()
    before_history = paths.history_jsonl.read_bytes()
    outcome = publish_result(result, paths, tmp_path / "analyses")
    assert outcome.state == "already_published"
    assert paths.latest_json.read_bytes() == before_json
    assert paths.history_jsonl.read_bytes() == before_history


def test_changed_and_forced_runs_preserve_history_prefix(tmp_path: Path) -> None:
    paths = metrics_paths(tmp_path / "sample.mp3", tmp_path / "metrics", tmp_path, "a" * 64)
    first = make_result(run_id="run-1", run_key="key-1")
    publish_result(first, paths, tmp_path / "analyses")
    first_history = paths.history_jsonl.read_bytes()

    second = make_result(run_id="run-2", run_key="key-2", supersedes="run-1", vividness_score=6)
    publish_result(second, paths, tmp_path / "analyses")
    assert paths.history_jsonl.read_bytes().startswith(first_history)
    assert read_latest(paths)["run_id"] == "run-2"

    forced = make_result(run_id="run-3", run_key="key-2", supersedes="run-2", vividness_score=6)
    outcome = publish_result(forced, paths, tmp_path / "analyses", force=True)
    assert outcome.state == "published"
    assert len(paths.history_jsonl.read_text(encoding="utf-8").splitlines()) == 3


def test_source_relative_paths_do_not_collide(tmp_path: Path) -> None:
    root = tmp_path / "input"
    first = root / "a" / "recording.mp3"
    second = root / "b" / "recording.mp3"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    first_paths = metrics_paths(first, tmp_path / "metrics", root, "a" * 64)
    second_paths = metrics_paths(second, tmp_path / "metrics", root, "b" * 64)
    assert first_paths.latest_json != second_paths.latest_json
    assert first_paths.latest_json.parent != second_paths.latest_json.parent


def test_missing_latest_text_is_repaired_without_new_history(tmp_path: Path) -> None:
    paths = metrics_paths(tmp_path / "sample.mp3", tmp_path / "metrics", tmp_path, "a" * 64)
    result = make_result()
    publish_result(result, paths, tmp_path / "analyses")
    paths.latest_text.unlink()
    before = paths.history_jsonl.read_bytes()
    outcome = publish_result(result, paths, tmp_path / "analyses")
    assert outcome.state == "already_published"
    assert paths.latest_text.exists()
    assert paths.history_jsonl.read_bytes() == before


def test_corrupt_latest_recovers_from_immutable_run_snapshot(tmp_path: Path) -> None:
    paths = metrics_paths(tmp_path / "sample.mp3", tmp_path / "metrics", tmp_path, "a" * 64)
    publish_result(make_result(), paths, tmp_path / "analyses")
    paths.latest_json.write_text("{broken", encoding="utf-8")
    recovered = read_latest(paths)
    assert recovered is not None
    assert recovered["run_id"] == "run-1"


def test_missing_latest_pair_recovers_from_history(tmp_path: Path) -> None:
    paths = metrics_paths(tmp_path / "sample.mp3", tmp_path / "metrics", tmp_path, "a" * 64)
    publish_result(make_result(), paths, tmp_path / "analyses")
    paths.latest_json.unlink()
    paths.current_pointer.unlink()
    recovered = read_latest(paths)
    assert recovered is not None
    assert recovered["run_id"] == "run-1"


def test_history_json_is_one_complete_snapshot_per_line(tmp_path: Path) -> None:
    paths = metrics_paths(tmp_path / "sample.mp3", tmp_path / "metrics", tmp_path, "a" * 64)
    publish_result(make_result(), paths, tmp_path / "analyses")
    value = json.loads(paths.history_jsonl.read_text(encoding="utf-8"))
    assert value["artifact_role"] == "per_audio_latest_metrics"
    assert value["run_id"] == "run-1"
