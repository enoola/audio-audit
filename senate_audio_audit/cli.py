from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .activity import create_activity_detector
from .artifacts import metrics_paths, read_latest, rebuild_global_ledger
from .errors import ArtifactError, AuditError, InputError
from .media import probe_media
from .models import jsonable
from .pipeline import AnalysisConfig, analyze_media
from .transcripts import discover_transcript
from .util import append_jsonl, parse_scope, pretty_json, utc_now

_DEFAULT_METRICS = Path("reports/metrics")
_DEFAULT_ANALYSES = Path("reports/analyses")
_DEFAULT_LEDGER = Path("reports/observations.txt")
_REVIEW_DECISIONS = {"confirmed", "rejected", "uncertain", "not_assessable"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="senate-audio-audit",
        description="Local evidence-first audio metrics for French parliamentary recordings",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze one explicit audio/video path")
    analyze.add_argument("path", type=Path)
    _add_analysis_arguments(analyze)

    batch = subparsers.add_parser("batch", help="Analyze approved paths from a rights manifest")
    _add_analysis_arguments(batch)
    batch.add_argument("paths", nargs="+", type=Path)
    batch.add_argument("--source-manifest", type=Path, default=Path("data/sources.csv"))

    inspect = subparsers.add_parser("inspect", help="Probe media and transcript without inference")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--transcript", type=Path)

    validate = subparsers.add_parser(
        "validate", help="Validate a metrics JSON or per-run directory"
    )
    validate.add_argument("path", type=Path)

    metrics = subparsers.add_parser("metrics", help="Show the latest per-audio metrics record")
    metrics.add_argument("path", type=Path)
    metrics.add_argument("--metrics-dir", type=Path, default=_DEFAULT_METRICS)
    metrics.add_argument("--input-root", type=Path)
    metrics.add_argument(
        "--history", action="store_true", help="Print the append-only JSONL history"
    )

    review = subparsers.add_parser("review", help="Append a human review decision")
    review.add_argument("--run-id", required=True)
    review.add_argument("--event-id", required=True)
    review.add_argument("--decision", required=True, choices=sorted(_REVIEW_DECISIONS))
    review.add_argument("--note", default="")
    review.add_argument("--reviewer", default="")
    review.add_argument("--reviews-file", type=Path, default=Path("reports/reviews.jsonl"))

    rebuild = subparsers.add_parser(
        "rebuild-report", help="Regenerate the global ledger from latest per-audio metrics"
    )
    rebuild.add_argument("--metrics-dir", type=Path, default=_DEFAULT_METRICS)
    rebuild.add_argument("--output", type=Path, default=_DEFAULT_LEDGER)

    visualize = subparsers.add_parser(
        "visualize",
        help="Open a local, read-only web view of one recording's committed metrics",
    )
    visualize.add_argument("path", type=Path)
    visualize.add_argument("--metrics-dir", type=Path, default=_DEFAULT_METRICS)
    visualize.add_argument("--input-root", type=Path)
    visualize.add_argument(
        "--timeline-bucket-ms", type=int, default=1000, help="Timeline bucket width"
    )
    visualize.add_argument(
        "--timeline-activity",
        choices=("match", "auto", "energy", "silero"),
        default="match",
        help="Activity lane backend; match follows what the metrics recorded",
    )
    visualize.add_argument("--vad-model", type=Path, help="Explicit local Silero ONNX model path")
    visualize.add_argument(
        "--rebuild-timeline", action="store_true", help="Recompute the cached timeline"
    )
    visualize.add_argument("--port", type=int, default=0, help="0 selects a free port")
    visualize.add_argument("--no-browser", action="store_true")
    visualize.add_argument(
        "--skip-verify", action="store_true", help="Skip the media SHA-256 check"
    )
    visualize.add_argument("--idle-timeout", type=float, default=1800.0)
    visualize.add_argument("--verbose", action="store_true")

    subparsers.add_parser("doctor", help="Check the local runtime and required executables")
    return parser


def _add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", help="Original-media start timecode, for example 00:12:30.000")
    parser.add_argument("--end", help="Original-media end timecode")
    parser.add_argument("--transcript", type=Path, help="Explicit SRT/VTT; omit for auto-discovery")
    parser.add_argument("--language", default="fr")
    parser.add_argument("--metrics-dir", type=Path, default=_DEFAULT_METRICS)
    parser.add_argument("--analyses-dir", type=Path, default=_DEFAULT_ANALYSES)
    parser.add_argument("--text-report", type=Path, default=_DEFAULT_LEDGER)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--chunk-seconds", type=float, default=60.0)
    parser.add_argument(
        "--vad-backend",
        choices=("auto", "silero", "energy"),
        default="auto",
        help="Local VAD backend; auto uses pinned Silero when available",
    )
    parser.add_argument("--vad-model", type=Path, help="Explicit local Silero ONNX model path")
    parser.add_argument(
        "--force", action="store_true", help="Create a new history run even if unchanged"
    )
    parser.add_argument("--offline", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rights-status", default="unverified_internal_pilot")
    parser.add_argument("--permission-reference")


def _config(args: argparse.Namespace) -> AnalysisConfig:
    start_ms = parse_scope(args.start)
    end_ms = parse_scope(args.end)
    if args.chunk_seconds <= 0:
        raise InputError("--chunk-seconds must be positive")
    if not args.offline:
        raise InputError("This implementation is local-only; --no-offline is not supported")
    return AnalysisConfig(
        metrics_dir=args.metrics_dir,
        analyses_dir=args.analyses_dir,
        text_report=args.text_report,
        input_root=args.input_root,
        transcript_path=args.transcript,
        language=args.language,
        start_ms=start_ms,
        end_ms=end_ms,
        chunk_seconds=args.chunk_seconds,
        vad_backend=args.vad_backend,
        vad_model_path=args.vad_model,
        force=args.force,
        offline=args.offline,
        rights_status=args.rights_status,
        permission_reference=args.permission_reference,
    )


def command_analyze(args: argparse.Namespace) -> int:
    config = _config(args)
    outcome = analyze_media(args.path, config)
    ledger_count = rebuild_global_ledger(config.metrics_dir, config.text_report)
    snapshot = outcome.result.to_dict()
    print(
        pretty_json(
            jsonable(
                {
                    "state": outcome.publish.state,
                    "analysis_status": snapshot["analysis_status"],
                    "speech_present": snapshot["summary"]["speech_present"],
                    "vividness": snapshot["vividness"],
                    "metrics_json": outcome.publish.paths.latest_json,
                    "metrics_text": outcome.publish.paths.latest_text,
                    "history_jsonl": outcome.publish.paths.history_jsonl,
                    "history_text": outcome.publish.paths.history_text,
                    "global_sources": ledger_count,
                }
            )
        ),
        end="",
    )
    if outcome.publish.state == "idempotent_noop":
        return 0
    return _status_exit(snapshot["analysis_status"])


def command_batch(args: argparse.Namespace) -> int:
    manifest = _load_rights_manifest(args.source_manifest)
    config = _config(args)
    failures = 0
    completed = 0
    batch_root = config.input_root
    if batch_root is None:
        parents = [path.expanduser().resolve().parent for path in args.paths]
        batch_root = Path(os.path.commonpath([str(parent) for parent in parents]))
    for path in args.paths:
        key = path.name
        entry = manifest.get(key)
        if not entry or not str(entry.get("rights_status", "")).startswith("approved"):
            print(f"SKIP {path}: no approved rights manifest entry", file=sys.stderr)
            failures += 1
            continue
        permitted = str(entry.get("permitted_uses", ""))
        if "analyze" not in permitted:
            print(f"SKIP {path}: manifest does not permit analysis", file=sys.stderr)
            failures += 1
            continue
        run_config = replace(
            config,
            input_root=batch_root,
            rights_status=str(entry["rights_status"]),
            permission_reference=str(entry.get("permission_reference") or "") or None,
        )
        try:
            outcome = analyze_media(path, run_config)
            completed += 1
            print(f"{outcome.publish.state}\t{path}\t{outcome.publish.paths.latest_text}")
        except AuditError as exc:
            failures += 1
            print(f"ERROR {path}: {exc}", file=sys.stderr)
    rebuild_global_ledger(config.metrics_dir, config.text_report)
    print(f"Batch summary: completed={completed} failed_or_skipped={failures}")
    return 0 if failures == 0 else 5


def command_inspect(args: argparse.Namespace) -> int:
    media = probe_media(args.path)
    transcript = discover_transcript(
        args.path,
        explicit_path=args.transcript,
        duration_ms=media.duration_ms,
    )
    print(
        pretty_json(
            {
                "media": jsonable(media),
                "transcript": jsonable(transcript),
            }
        ),
        end="",
    )
    return 0


def command_validate(args: argparse.Namespace) -> int:
    path = args.path
    json_path = path / "report.json" if path.is_dir() else path
    if not json_path.exists():
        raise InputError(f"Metrics JSON does not exist: {json_path}")
    with json_path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ArtifactError("Metrics JSON root must be an object")
    required = {
        "schema_version",
        "artifact_role",
        "run_id",
        "run_key",
        "analysis_status",
        "source",
        "scope",
        "pipeline",
        "quality",
        "measurements",
        "summary",
        "vividness",
        "events",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise ArtifactError(f"Metrics JSON is missing required fields: {', '.join(missing)}")
    if value["artifact_role"] != "per_audio_latest_metrics":
        raise ArtifactError("Unexpected artifact_role")
    if value["vividness"].get("score") is not None and not (
        0 <= int(value["vividness"]["score"]) <= 10
    ):
        raise ArtifactError("Vividness score is outside 0..10")
    text_path = json_path.with_name("report.txt") if path.is_dir() else None
    if text_path and text_path.exists():
        text = text_path.read_text(encoding="utf-8")
        if str(value["run_id"]) not in text:
            raise ArtifactError("Text report does not contain the JSON run_id")
    print(f"VALID {json_path}")
    return 0


def command_metrics(args: argparse.Namespace) -> int:
    media = probe_media(args.path)
    paths = metrics_paths(
        args.path,
        args.metrics_dir,
        args.input_root or args.path.parent,
        media.sha256,
    )
    if args.history:
        if not paths.history_jsonl.exists():
            raise InputError(f"No metrics history exists: {paths.history_jsonl}")
        sys.stdout.write(paths.history_jsonl.read_text(encoding="utf-8"))
        return 0
    latest = read_latest(paths)
    if latest is None:
        raise InputError(f"No latest metrics found for {args.path}: expected {paths.latest_json}")
    print(pretty_json(latest), end="")
    return 0


def command_review(args: argparse.Namespace) -> int:
    record = {
        "schema_version": "0.1.0",
        "created_at": utc_now(),
        "run_id": args.run_id,
        "event_id": args.event_id,
        "decision": args.decision,
        "reviewer": args.reviewer or None,
        "note": args.note or None,
    }
    append_jsonl(args.reviews_file, record)
    print(f"Review appended to {args.reviews_file}")
    return 0


def command_rebuild(args: argparse.Namespace) -> int:
    count = rebuild_global_ledger(args.metrics_dir, args.output)
    print(f"Rebuilt {args.output} from {count} per-audio metrics files")
    return 0


def command_visualize(args: argparse.Namespace) -> int:
    from .visualizer import TimelineConfig, load_or_build_timeline
    from .visualizer.server import (
        VisualizerConfig,
        VisualizerSession,
        build_state,
    )

    media = probe_media(args.path)
    paths = metrics_paths(
        args.path,
        args.metrics_dir,
        args.input_root or args.path.parent,
        media.sha256,
    )
    snapshot = read_latest(paths)
    if snapshot is None:
        raise InputError(
            f"No committed metrics found for {args.path}: expected {paths.latest_json}. "
            f"Run 'senate-audio-audit analyze {args.path}' first."
        )

    status = str(snapshot.get("analysis_status", "unknown"))
    if status == "failed":
        recorded = "; ".join(
            f"{item.get('stage', 'unknown')}: {item.get('message', '')}"
            for item in snapshot.get("errors", [])
        )
        raise InputError(
            f"The latest analysis for this recording failed and cannot be visualized "
            f"({recorded or 'no recorded detail'})"
        )
    if status == "not_assessable":
        print(
            f"NOTE: this recording is marked not_assessable "
            f"({snapshot.get('summary', {}).get('status_note', 'no detail')}). "
            "The page will explain why and show no timeline lanes.",
            file=sys.stderr,
        )

    config = TimelineConfig(
        bucket_ms=args.timeline_bucket_ms,
        activity_backend=args.timeline_activity,
    ).validated()
    artifact = load_or_build_timeline(
        paths,
        args.path,
        snapshot,
        config,
        vad_model=args.vad_model,
        force=args.rebuild_timeline,
    )

    if not args.skip_verify:
        recorded_sha = str(snapshot.get("source", {}).get("sha256", ""))
        if recorded_sha and recorded_sha != media.sha256:
            print(
                f"WARNING: the media SHA-256 does not match the snapshot.\n"
                f"  snapshot: {recorded_sha}\n"
                f"  on disk : {media.sha256}\n"
                f"The audio you hear is not the analyzed source. Re-run 'analyze'.",
                file=sys.stderr,
            )

    server_config = VisualizerConfig(
        port=args.port,
        open_browser=not args.no_browser,
        idle_timeout=args.idle_timeout,
        verbose=args.verbose,
    ).validated()
    state = build_state(
        snapshot, media_path=args.path, timeline=artifact.payload, config=server_config
    )

    if not artifact.payload["envelope"]["available"]:
        print(
            "NOTE: timeline lanes are unavailable; the page will show events and metrics only.",
            file=sys.stderr,
        )

    print(f"file      : {media.filename}")
    print(f"run       : {snapshot.get('run_id')} ({status})")
    print(f"events    : {len(snapshot.get('events', []))}")
    print(f"timeline  : {artifact.path} ({'rebuilt' if artifact.rebuilt else 'cached'})")
    print("serving   : loopback only, read-only; no review decisions are recorded here")
    print("press Ctrl-C to stop")

    with VisualizerSession(state, server_config) as session:
        print(f"url       : {session.url}")
        try:
            session.wait_closed()
        except KeyboardInterrupt:
            print("\nstopping")
    return 0


def command_doctor(_: argparse.Namespace) -> int:
    try:
        _, vad_metadata = create_activity_detector("auto")
    except Exception as exc:  # pragma: no cover - defensive doctor output
        vad_metadata = {
            "backend": "unavailable",
            "model_id": None,
            "model_path": None,
            "model_sha256": None,
            "selection_reason": str(exc),
        }
    checks = {
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "package_version": __version__,
        "offline_default": True,
        "onnxruntime_available": _module_available("onnxruntime"),
        "vad": vad_metadata,
        "diarization_enabled": False,
    }
    print(pretty_json(jsonable(checks)), end="")
    return 0 if checks["ffmpeg"] and checks["ffprobe"] else 5


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _load_rights_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise InputError(
            f"Batch mode requires a rights manifest: {path} "
            "(no full-corpus processing is permitted without it)"
        )
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"local_filename", "rights_status", "permitted_uses"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise InputError(f"Rights manifest is missing columns: {', '.join(sorted(missing))}")
        result: dict[str, dict[str, str]] = {}
        for row in reader:
            filename = str(row.get("local_filename") or "")
            if not filename:
                continue
            if filename in result:
                raise InputError(f"Rights manifest contains duplicate local_filename: {filename}")
            result[filename] = row
        return result


def _status_exit(status: str) -> int:
    if status in {"completed", "partial"}:
        return 0
    if status in {"no_speech", "not_assessable"}:
        return 3
    if status == "failed":
        return 5
    return 5


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "analyze": command_analyze,
        "batch": command_batch,
        "inspect": command_inspect,
        "validate": command_validate,
        "metrics": command_metrics,
        "review": command_review,
        "rebuild-report": command_rebuild,
        "visualize": command_visualize,
        "doctor": command_doctor,
    }
    try:
        return handlers[args.command](args)
    except (AuditError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, (InputError, json.JSONDecodeError)) else 5


if __name__ == "__main__":
    raise SystemExit(main())
