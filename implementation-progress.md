# Implementation Progress

**Project:** `senat-audio-audit`  
**Plan:** `implementation-plan_v0.1.md` (+ `implementation-plan-visualizer.md`)  
**Started:** 2026-09-24  
**Current milestone:** Visualizer complete — MVP handoff plus local review view  
**Status:** Complete

## Scope of this implementation pass

Build a local-first Python 3.12 CLI that can:

- accept one explicit audio/video path;
- probe media safely with FFmpeg/FFprobe;
- discover and validate SRT/VTT transcripts;
- estimate speech presence and active-speech intervals;
- compute transparent digital-level, clipping, speech-rate, pause, and related acoustic metrics;
- create provisional loud-delivery/high-arousal candidates only when evidence is sufficient;
- calculate an explicitly provisional 0–10 audio-vividness score;
- write `<stem>.metrics.json` and `<stem>.metrics.txt` for each treated source;
- retain append-only per-audio history;
- write per-run artifacts and a global text ledger; and
- include tests without processing the full copyrighted corpus.

The full corpus will not be analyzed until source-rights status is documented. The existing all-`NEUTRAL` sentiment SRT files remain excluded from inference and calibration.

## Decisions

- Python 3.12 in a project-local `uv` environment; do not use system Python 3.14 for ML dependencies.
- Local/offline processing by default.
- No cloud APIs and no implicit use of `hf_key`.
- Existing base SRT is preferred; no ASR rerun unless a transcript is absent or a later validation proves necessary.
- Anonymous speaker labels are recording-local; no identity inference.
- Outputs describe observable/perceived behavior, not internal emotion or intent.
- Per-audio output format: latest JSON + text plus append-only JSONL + text history.
- Vividness remains `provisional_rubric` until human calibration exists.

## Scope of the visualizer pass (M10)

Add a local, read-only web view over metrics that have already been committed, so a
reviewer can see *where* in a recording its candidate events fall in time and hear the
audio behind each one, instead of reading timestamped text lines and seeking an
external player.

- `senate-audio-audit visualize PATH` serves a page on `127.0.0.1` and opens a browser;
- three timeline lanes: a dBFS loudness envelope, a speech-activity strip, and the
  candidate events, with click-to-seek and per-event playback that stops at the event end;
- the measured metrics, the vividness score with its provisional status, the per-event
  evidence, and the recorded warnings are all rendered from the existing snapshot;
- a display-only, regenerable `<stem>.timeline.json` sidecar supplies the time series a
  snapshot does not store.

The visualizer deliberately runs **no inference, changes no metric, alters no existing
artifact, and records no review decision**. `review_status` renders read-only;
`senate-audio-audit review` remains the only way to record a decision.

### Visualizer decisions

- Loopback-only bind; a non-loopback host is refused rather than flagged.
- No client-supplied filesystem paths, no mutating HTTP method, `Host` validation, no
  CORS, and a per-session token, because any page open in the browser can request
  `127.0.0.1`.
- `Range` support is mandatory: `SimpleHTTPRequestHandler` cannot seek an 85 MB,
  89-minute recording.
- The browser must never call `decodeAudioData`: that file is ~2 GB of Float32.
- No new runtime dependency, no build step, and no external asset, so the page renders
  fully offline.

### Visualizer verification

- 133 tests pass (40 MVP + 93 visualizer), including 24 static UI guards; ruff and
  format clean; `app.js` syntax checked.
- Reference recording, 89:03.033: cold sidecar build 15.7 s, sidecar 59,265 bytes,
  served payload 102,351 bytes; range responses byte-exact against the source file.
- Security surface confirmed against a live server: `206`/`416` for ranges, `501` for
  `POST`/`PUT`/`DELETE`, `403` for a foreign `Host` and a missing token, `404` for
  traversal, and no absolute local paths in the served payload.
- Verified in-browser: 41 events listed, click-to-seek, per-event playback
  (4.750 → 25.499 s, auto-paused), filters, `J`/`K` stepping, `Ctrl`+wheel zoom, and
  zero console errors.
- Two layout defects were found only by looking at the rendered page — an opaque
  playhead canvas that hid every lane, and lane heights drifting from the canvas
  aspect ratio. Both are fixed and pinned by mutation-checked guards. See
  `implementation-plan-visualizer.md` §13.2.

## Milestones

| ID | Milestone | Status | Notes |
|---|---|---|---|
| M0 | Corpus/environment audit | Complete | 21 MP3, ~46.82 h; FFmpeg 9.0.1; Python 3.12 and uv available |
| M1 | Plan v0.1 metrics lifecycle | Complete | Latest JSON/text and append-only history specified |
| M2 | Secure repository scaffold | Complete | `.gitignore`, `pyproject.toml`, `uv.lock`, Python 3.12 package layout |
| M3 | Media probe and transcript QC | Complete | FFprobe media probe, SRT/VTT parsing, drift/clamp warnings, sentiment-sidecar exclusion |
| M4 | Speech/acoustic analysis | Complete | Pinned local Silero ONNX VAD, energy fallback, level/clipping/rate/pause/F0 metrics |
| M5 | Candidate events and vividness | Complete | Transparent relative-loudness/high-arousal rules, lexical review prompts, provisional score |
| M6 | Metrics/history output lifecycle | Complete | Latest JSON/text, JSONL/text history, run keys, locks, pointer/run recovery, global ledger |
| M7 | CLI and review command | Complete | `analyze`, rights-gated `batch`, `inspect`, `validate`, `metrics`, `review`, `rebuild-report`, `doctor` |
| M8 | Automated tests | Complete | 40 tests covering parsing, VAD, media, scoring, lifecycle, recovery, CLI, no-audio abstention |
| M9 | Documentation and handoff | Complete | README, schema, model provisioning, progress log, and clean-install handoff verified |
| M10 | Local metrics visualizer | Complete | `visualize` CLI, loopback read-only server, three-lane timeline, display-only sidecar |

## Acceptance checkpoint for this pass

The implementation pass is complete when:

- the package installs and tests pass in Python 3.12;
- a synthetic or otherwise rights-cleared fixture can be analyzed end to end;
- malformed/missing media and transcript cases fail or abstain cleanly;
- every terminal result for an accepted source produces a metrics pair and history entry;
- an unchanged rerun is idempotent unless forced;
- a changed rerun preserves the old run in history and atomically updates the latest pair;
- reports label the score provisional and avoid claims about internal emotion;
- no existing source media/sidecar is modified; and
- `implementation-progress.md` accurately records completion and remaining limitations.

## Progress log

### 2026-09-24 — Initialization

- Inspected the workspace and corpus without modifying media.
- Confirmed existing sentiment SRT is saturated (`NEUTRAL` for every cue) and unsuitable as labels.
- Created `implementation-plan_v0.1.md` with per-audio metrics/history requirements.
- Selected Python 3.12, `uv`, and local FFmpeg processing.
- Started M2.

### 2026-09-25 — Observable MVP implementation

- Added the installable `senat-audio-audit` package and Python 3.12 `uv` environment.
- Implemented FFprobe media inspection, safe chunked FFmpeg decoding, channel-policy checks, and original-channel clipping measurement.
- Implemented SRT/VTT discovery and QC, including BOM/CRLF handling, metadata-block filtering, overlap diagnostics, duration clamping, and refusal of `*.sentiment.srt` primary inputs.
- Implemented local Silero VAD through pinned ONNX Runtime CPU inference, with SHA-256 provenance and an explicit energy-activity fallback when the optional model/runtime is absent.
- Implemented active-speech levels, speech rate, pause ratio, approximate F0 variation, provisional loud/high-arousal candidates, conservative French review rules, and bounded vividness scoring.
- Implemented per-audio latest JSON/text plus append-only JSONL/text history, idempotent run keys, forced-run support, per-source locks, immutable run snapshots, current-pointer recovery, and global ledger rebuild.
- Implemented rights-gated batch mode, inspection, validation, metrics/history, review logging, report rebuild, and runtime doctor commands.
- Added explicit no-audio `not_assessable` snapshots instead of silently treating video-only media as speech data.
- Added pinned model provisioning script and model license/provenance documentation; runtime analysis does not download models or read `hf_key`.
- Test status at this checkpoint: `40 passed`; Ruff and Python compilation pass.

### 2026-09-25 — Final acceptance audit

- Clean locked environment install with `uv sync --locked --python 3.12 --extra dev --extra vad` passed.
- Clean-environment test run passed: `40 passed`.
- Ruff check and Python compilation passed.
- `senate-audio-audit doctor` confirmed FFmpeg/FFprobe, local ONNX Runtime, pinned Silero model, and model SHA-256 provenance.
- Synthetic WAV analysis completed end to end; latest JSON/text, JSONL/text history, and per-run snapshots were inspected.
- Synthetic video-without-audio analysis produced a `not_assessable` snapshot with null vividness and history artifacts.
- Wheel and source distribution built successfully; the console entry point works from the built wheel.
- `implementation-plan_v0.md` was not edited; its recorded SHA-256 is `fc8feec04f9a9865f77d701b6b930c3adc5198944298ff32239503f46647aead`.

### 2026-09-25 — Local metrics visualizer

- Created `implementation-plan-visualizer.md` and the `feat/visualizer` branch.
- Added `senate_audio_audit/visualizer/` with `timeline.py` (display-only sidecar),
  `server.py` (loopback read-only HTTP server), and `ui/` (vanilla HTML/CSS/JS).
- Added the `visualize` subcommand, `schemas/timeline-v0.1.schema.json`, the
  `ui/` wheel artifacts entry, and README documentation.
- Implemented `Range` support so an 85 MB, 89-minute recording can be scrubbed, plus
  loopback binding, a fixed route table, `Host` validation, a session token, and
  absolute-path scrubbing of the served payload.
- Confirmed the recomputed activity lane matches the committed metrics exactly
  (`speech_ratio` 0.936133) and that the recorded method is
  `silero-vad+transcript-fallback`, so the lane is labelled as transcript-derived
  rather than presented as VAD-detected speech.
- Found and corrected three data-driven issues: the transcript-fallback provenance, a
  median reference line that did not represent the envelope's own median, and a
  `quality.vad_model.model_path` leak of a local home directory.
- Found and corrected two layout defects by inspecting the rendered page: an opaque
  playhead canvas that hid all lanes, and lane heights derived from the canvas aspect
  ratio. Added 24 static UI guards and mutation-checked them.
- Recorded that visual rendering cannot be proven by assertions about bytes and data,
  and that the invariants reachable by static tests are now asserted.

## Remaining limitations

- Diarization is an explicit future adapter: speaker summaries and speaker IDs remain unavailable in this MVP rather than guessed.
- Sarcasm, anger expression, and disrespect outputs are candidate/review states, not probabilities or claims about internal states.
- The vividness rubric is not human-calibrated; the corpus must not be batch-processed until rights and intended-use approval is recorded in `data/sources.csv`.
- The implementation is an MVP, not a validated research or publication system; human review and calibration remain required.
- The visualizer adds no new claim: it displays the same candidates, carries the same
  provisional and uncalibrated labels, and cannot record a review decision.
- The visualizer's loudness envelope is a display aid from an independent decode pass,
  not a measurement, and is never merged into the metrics JSON.
- The activity lane follows the backend the metrics recorded, and for the reference
  recording that lane is transcript-cue coverage rather than VAD-detected speech; the
  page states this.
- Event boundaries derive from 30 ms VAD frames, are not sample-accurate, and the page
  exposes a playback tolerance rather than implying exactness.
- Diarization remains disabled, so the visualizer attributes nothing to named speakers.
