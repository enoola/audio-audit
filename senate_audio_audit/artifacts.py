from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ArtifactError
from .models import AnalysisResult
from .reporting import render_metrics_text
from .util import (
    append_jsonl,
    append_text,
    atomic_write_text,
    canonical_json,
    file_lock,
    pretty_json,
    sanitize_component,
    source_relative_path,
    stable_hash,
)


@dataclass(frozen=True, slots=True)
class MetricsPaths:
    latest_json: Path
    latest_text: Path
    history_jsonl: Path
    history_text: Path
    current_pointer: Path
    lock_file: Path


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    state: str
    latest_run_id: str
    paths: MetricsPaths


def metrics_paths(
    media_path: Path,
    metrics_dir: Path,
    input_root: Path | None,
    source_sha256: str,
) -> MetricsPaths:
    relative = source_relative_path(media_path, input_root)
    parts = [sanitize_component(part) for part in Path(relative).parts if part not in {".", ""}]
    if input_root is not None and len(parts) == 1:
        try:
            media_path.resolve().relative_to(input_root.resolve())
        except ValueError:
            stem = parts[0]
            parts = [f"{stem}__{source_sha256[:10]}"]
    if not parts:
        parts = [f"audio__{source_sha256[:10]}"]
    filename_stem = Path(parts[-1]).stem
    output_dir = metrics_dir.joinpath(*parts[:-1])
    base = output_dir / filename_stem
    return MetricsPaths(
        latest_json=base.with_name(base.name + ".metrics.json"),
        latest_text=base.with_name(base.name + ".metrics.txt"),
        history_jsonl=base.with_name(base.name + ".metrics.history.jsonl"),
        history_text=base.with_name(base.name + ".metrics.history.txt"),
        current_pointer=base.with_name("." + base.name + ".current.json"),
        lock_file=base.with_name("." + base.name + ".lock"),
    )


def compute_run_key(
    *,
    source_sha256: str,
    transcript_sha256: str | None,
    start_ms: int,
    end_ms: int,
    pipeline_version: str,
    config_hash: str,
    model_manifest_hash: str,
) -> str:
    return stable_hash(
        {
            "source_sha256": source_sha256,
            "transcript_sha256": transcript_sha256,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "pipeline_version": pipeline_version,
            "config_hash": config_hash,
            "model_manifest_hash": model_manifest_hash,
        },
        length=64,
    )


def read_latest(paths: MetricsPaths) -> dict[str, Any] | None:
    """Read the last committed snapshot, recovering from immutable run data.

    The current pointer is written after both latest views. If publication was
    interrupted, the pointer or per-run report is preferred over a mixed latest pair.
    """
    pointer_snapshot = _snapshot_from_pointer(paths)
    if pointer_snapshot is not None:
        return pointer_snapshot
    latest_snapshot = _load_snapshot(paths.latest_json)
    if latest_snapshot is not None:
        return latest_snapshot
    history_snapshot = _newest_history_snapshot(paths.history_jsonl)
    if history_snapshot is not None:
        return history_snapshot
    if paths.latest_json.exists():
        raise ArtifactError(f"Latest metrics JSON is unreadable: {paths.latest_json}")
    return None


def latest_matches_run_key(paths: MetricsPaths, run_key: str) -> bool:
    latest = read_latest(paths)
    return bool(latest and latest.get("run_key") == run_key)


def publish_result(
    result: AnalysisResult,
    paths: MetricsPaths,
    analyses_dir: Path,
    *,
    force: bool = False,
) -> PublishOutcome:
    snapshot = result.to_dict()
    text = render_metrics_text(snapshot)
    # Validate finite JSON before touching any current/history file.
    canonical_json(snapshot)

    run_dir = analyses_dir / result.run_id
    run_report = run_dir / "report.json"
    run_text = run_dir / "report.txt"
    atomic_write_text(run_report, pretty_json(snapshot))
    atomic_write_text(run_text, text)

    with file_lock(paths.lock_file):
        latest = read_latest(paths)
        if latest and latest.get("run_id") == result.run_id:
            _ensure_history(paths, snapshot, text)
            _publish_latest(paths, snapshot, text, run_report=run_report)
            return PublishOutcome(
                state="already_published", latest_run_id=result.run_id, paths=paths
            )
        if latest and latest.get("run_key") == result.run_key and not force:
            return PublishOutcome(
                state="idempotent_noop", latest_run_id=str(latest["run_id"]), paths=paths
            )

        _ensure_history(paths, snapshot, text)
        _publish_latest(paths, snapshot, text, run_report=run_report)
        return PublishOutcome(state="published", latest_run_id=result.run_id, paths=paths)


def _ensure_history(paths: MetricsPaths, snapshot: dict[str, Any], text: str) -> None:
    run_id = str(snapshot["run_id"])
    if not _jsonl_contains(paths.history_jsonl, "run_id", run_id):
        append_jsonl(paths.history_jsonl, snapshot)
    marker = f"METRICS-HISTORY-BLOCK run_id={run_id}"
    history_text = _read_text_or_empty(paths.history_text)
    if marker not in history_text:
        delimiter = "=" * 80
        block = f"{delimiter}\n{marker}\n{text}"
        if history_text and not history_text.endswith("\n"):
            history_text += "\n"
        append_text(paths.history_text, block)


def _publish_latest(
    paths: MetricsPaths,
    snapshot: dict[str, Any],
    text: str,
    *,
    run_report: Path,
) -> None:
    # A lock-aware reader always sees a coherent pair. The current pointer is
    # replaced only after both latest views have been written.
    atomic_write_text(paths.latest_json, pretty_json(snapshot))
    atomic_write_text(paths.latest_text, text)
    pointer = {
        "run_id": snapshot["run_id"],
        "run_key": snapshot["run_key"],
        "latest_json": paths.latest_json.name,
        "latest_text": paths.latest_text.name,
        "run_report": str(run_report.resolve()),
    }
    atomic_write_text(paths.current_pointer, canonical_json(pointer) + "\n")


def _load_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(value, dict) and "run_id" in value:
        return value
    return None


def _snapshot_from_pointer(paths: MetricsPaths) -> dict[str, Any] | None:
    pointer = _load_snapshot(paths.current_pointer)
    if pointer is None:
        return None
    run_report_value = pointer.get("run_report")
    if not run_report_value:
        return None
    run_report = Path(str(run_report_value)).expanduser()
    if not run_report.is_absolute():
        run_report = (paths.latest_json.parent / run_report).resolve()
    return _load_snapshot(run_report)


def _newest_history_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    newest: dict[str, Any] | None = None
    for line in _read_text_or_empty(path).splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "run_id" in value:
            newest = value
    return newest


def _jsonl_contains(path: Path, key: str, expected: str) -> bool:
    if not path.exists():
        return False
    for line in _read_text_or_empty(path).splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            # A truncated/corrupt line is not considered a committed entry.
            continue
        if value.get(key) == expected:
            return True
    return False


def _read_text_or_empty(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactError(f"Cannot read artifact {path}: {exc}") from exc


def rebuild_global_ledger(metrics_dir: Path, output_path: Path) -> int:
    from .reporting import render_global_ledger

    snapshots: list[dict[str, Any]] = []
    for path in sorted(metrics_dir.rglob("*.metrics.json")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"Cannot rebuild ledger from {path}: {exc}") from exc
        if isinstance(value, dict) and value.get("artifact_role") == "per_audio_latest_metrics":
            value.setdefault("source", {})["metrics_json"] = str(path)
            snapshots.append(value)
    atomic_write_text(output_path, render_global_ledger(snapshots))
    return len(snapshots)
