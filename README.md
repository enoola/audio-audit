# Senate Audio Audit (MVP)

Local, evidence-first metrics for French parliamentary audio/video. The MVP reports
observable or perceived behavior and an explicitly provisional 0–10 audio-vividness
score. It does not infer internal emotion, intent, identity, or character.

## Requirements

- Python 3.12
- FFmpeg and FFprobe on `PATH`
- `uv` (recommended)

## Install

```bash
uv sync --python 3.12 --extra dev
```

For the optional local Silero VAD backend:

```bash
uv run python scripts/provision_models.py
uv sync --python 3.12 --extra vad
```

Model provisioning is explicit and verified by SHA-256. Runtime analysis never
downloads a model or calls a cloud service.

## Basic use

```bash
uv run senate-audio-audit doctor
uv run senate-audio-audit inspect "audio/example.mp3"
uv run senate-audio-audit analyze "audio/example.mp3" \
  --metrics-dir reports/metrics \
  --text-report reports/observations.txt
```

## Visual review

For a recording that has already been analyzed, `visualize` opens a local,
read-only web view of its committed metrics:

```bash
uv run senate-audio-audit visualize "audio/example.mp3"
```

The page shows the measured metrics, the vividness score with its provisional
status, and a three-lane timeline — a loudness envelope, a speech-activity strip,
and the candidate events. Clicking an event seeks the audio to that moment; the
row's `▶` plays only that span and stops at its end. Keyboard: `Space` play/pause,
`←`/`→` ±5 s (`Shift` for ±30 s), `J`/`K` next/previous event, `Enter` play the
selected event, `0` reset zoom, `Esc` clear the selection. `Ctrl`/`Cmd` + wheel
zooms; a plain wheel pans.

The server binds `127.0.0.1` only, serves a fixed route table, and implements no
mutating method. It **records no review decisions**: `review_status` is displayed
read-only, and `senate-audio-audit review` remains the only way to record a
decision.

Loudness and activity lanes come from a cached, display-only sidecar
(`<stem>.timeline.json`) that is regenerated on demand; it is not a metric and is
never merged into the metrics JSON. Use `--rebuild-timeline` to recompute it and
`--timeline-bucket-ms` to trade resolution for size. See
`implementation-plan-visualizer.md` for the design.

`auto` uses the pinned local Silero VAD when available and otherwise records an
explicit energy-activity fallback. Force a backend with `--vad-backend silero` or
`--vad-backend energy`; use `--vad-model PATH` for an explicitly provisioned model.

The `analyze` command creates, under `reports/metrics/`:

- `<stem>.metrics.json` — latest canonical snapshot
- `<stem>.metrics.txt` — latest human-readable snapshot
- `<stem>.metrics.history.jsonl` — append-only complete run snapshots
- `<stem>.metrics.history.txt` — append-only readable history
- a hidden current pointer and per-run immutable snapshots under `reports/analyses/`

The canonical snapshot contract is documented in
`schemas/metrics-v0.1.schema.json`.

A forced run appends a new history entry; an unchanged run is idempotent unless
`--force` is supplied. `batch` requires approved rights rows in `data/sources.csv`;
the current manifest is intentionally empty.

## Current limitations

- Energy/activity fallback is not a neural VAD and reports lower confidence.
- Speaker diarization is not enabled; events are not attributed to named speakers.
- Text rules only create uncertain review prompts and suppress quoted/reported insults.
- Sarcasm and anger-expression outputs remain provisional candidates.
- The vividness formula is not calibrated to human ratings.
- Human review is required before quoting or publishing any candidate event.
- The visualizer is a view over committed metrics: it adds no inference, changes no
  metric, and cannot record a review decision.
- The visualizer's loudness envelope is a display aid from an independent pass, not a
  measurement. Its activity lane follows the backend the metrics recorded, and the
  page labels it when that lane came from a transcript fallback rather than VAD.
- Event boundaries derive from 30 ms VAD frames and are not sample-accurate; the page
  exposes a playback tolerance for this.
