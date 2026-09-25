# Implementation Plan — Local Visualizer for Per-Audio Metrics

**Project:** `senat-audio-audit`
**Branch:** `feat/visualizer`
**Plan:** `implementation-plan-visualizer.md`
**Date:** 2026-09-25
**Status:** Planned — not started
**Base plan:** `implementation-plan_v0.1.md`

---

## 1. Executive summary

The MVP produces a rich, fully machine-readable snapshot per recording
(`<stem>.metrics.json`, ~53 KB for the reference corpus) and a human-readable text
report, but there is no way to *look* at a recording and see where its 41 candidate
events fall in time. Reviewing findings today means reading timestamped text lines and
then manually seeking an external audio player.

This plan adds a **local, read-only web visualizer**: a new `visualize` subcommand that
starts a loopback HTTP server and opens a browser page showing a multi-lane timeline
(speech activity, loudness envelope, candidate events) for one analyzed recording, with
click-to-seek playback so a reviewer can jump to any event and hear the audio that
produced it.

The visualizer is strictly a **view over already-committed artifacts**. It computes
nothing that changes an analysis result, writes no review decisions, and adds no
dependency to the analysis path. All computation it needs beyond the existing snapshot
is one precomputed, cached, display-only *timeline sidecar*.

Design constraints (all confirmed with the requester):

| Decision | Choice |
|---|---|
| Delivery | New CLI subcommand + local HTTP server on `127.0.0.1` |
| Timeline lanes | Candidate events + speech activity + dBFS loudness envelope |
| Human review | **Read-only.** No write path to `reviews.jsonl` |
| Frontend | Vanilla HTML/CSS/JS, zero dependencies, no build step, fully offline |

---

## 2. Goal and non-goals

### 2.1 Goal

Given one analyzed recording, produce a page that lets a reviewer:

1. see the overall metrics picture (speech ratio, measured levels, rate, F0, vividness);
2. see *where* in the recording activity, loudness, and candidate events occur;
3. click any candidate event and have the audio seek to that moment;
4. play just that event's span, and replay it, to hear the evidence; and
5. read the same per-event evidence, decision state, and review status that the text
   report shows, without leaving the page.

### 2.2 Explicit non-goals

These are deliberately excluded from v1. Each would require a design decision this plan
does not make.

- **No review write-back.** No confirm/reject buttons, no POST endpoints, no writes to
  `reports/reviews.jsonl`. The page renders `review_status` as read-only. The existing
  `senate-audio-audit review` command remains the only way to record a decision.
- **No new inference.** The visualizer never re-runs VAD, never scores events, and
  never alters a snapshot. It may compute a *display* envelope, but that envelope is a
  rendering aid, not a metric, and is never written into `*.metrics.json`.
- **No cross-recording comparison view.** The global ledger stays text-only. A
  multi-recording comparison page is a separate feature (see §14).
- **No transcript lane in v1.** The SRT/VTT cue layer was considered and deferred; see
  §6.2 and §14. Only 1 of 41 events currently carries a `transcript` string anyway.
- **No speaker lanes.** Diarization is disabled in the MVP, so `speaker_summaries` is
  empty and every event has `speaker_id: null`. The UI must state this rather than
  imply speaker separation.
- **No editing, annotation, or export of findings.**
- **No packaging as a web service.** The server is a local, single-user, ephemeral
  developer tool. It is not hardened for exposure.

---

## 3. Current-state assessment (evidence)

Measured against the reference recording
`video_1879774_5fd00c098be35_…---audition-de.mp3`:

| Property | Value | Consequence for design |
|---|---|---|
| Duration | `5,343,033` ms (01:29:03.033) | Timeline needs zoom; 1 px ≈ 1 s at full width |
| File size | `85,489,101` bytes (mp3, stereo 44.1 kHz) | Must stream with HTTP Range, never inline |
| `events` | 41, spanning 5,000 – 5,175,000 ms | Event bars are sub-pixel at full zoom-out |
| Claim types | `high_arousal_forceful_delivery`, `relative_loud_delivery` | Only 2 lanes of event type; a legend suffices |
| Decision states | `supported_candidate`, `uncertain_candidate` | Must be visually distinct, not merged |
| `review_status` | `unreviewed` for all 41 | Page shows an explicit "0/41 reviewed" counter |
| `transcript` present | 1 of 41 events | No transcript lane in v1 |
| `speaker_summaries` | `[]`, `speaker_id: null` everywhere | UI must disclose no diarization |
| `speech_ratio` | `0.936133` (quality `poor`) | Activity lane is nearly solid; envelope carries the detail |
| `vividness` | `10/10`, `provisional_rubric`, band `medium` | Must render with an uncalibrated/provisional badge |
| Snapshot size | 53,197 bytes JSON | Safe to ship in one payload; no lazy loading needed |
| `source.path` | absolute path through the `audio` symlink | Server resolves audio server-side (§5.3) |

**Key gap:** the snapshot persists only *aggregates* and *events*. It stores no
time series. The 30 ms RMS frame levels that a loudness envelope needs are already
computed in memory during analysis by `_LevelCollector` in `activity.py`, but they are
discarded after metrics are reduced to percentiles. The activity intervals
(`ActivityResult.intervals`) are likewise computed and then dropped.

So the envelope and activity lanes must either be persisted at analysis time or
recomputed on demand. §5.5 chooses recompute-and-cache, because the reference snapshot
already exists and re-running `analyze` to obtain pixels would be wasteful.

---

## 4. Architecture

```
senate-audio-audit visualize <media-path> [options]
        │
        ├─ 1. resolve committed snapshot   artifacts.read_latest(metrics_paths(...))
        │     └─ same recovery order as `metrics`: pointer → latest JSON → history
        │
        ├─ 2. resolve timeline sidecar     timeline.load_or_build(...)
        │     ├─ cache hit  (sha256 + run_id + bucket_ms match) → reuse
        │     └─ cache miss → timeline.build_envelope(...) via ffmpeg, then atomic write
        │
        ├─ 3. bind 127.0.0.1:<port>        visualizer/server.py
        │     └─ fixed route table only; no client-supplied filesystem paths
        │
        └─ 4. open http://127.0.0.1:<port>/ browser
              │
              ├─ GET /              → ui/index.html
              ├─ GET /app.css       → ui/app.css
              ├─ GET /app.js        → ui/app.js
              ├─ GET /api/payload   → {"snapshot": …, "timeline": …}
              └─ GET /audio         → mp3 byte stream WITH Range support
```

### 4.1 Module layout

New modules, mirroring the existing flat package layout (`senate_audio_audit/*.py`):

| File | Responsibility |
|---|---|
| `visualizer/__init__.py` | Package marker; public `run_visualizer()` entry |
| `visualizer/timeline.py` | Envelope + activity bucket builder, cache keying, sidecar I/O |
| `visualizer/server.py` | Loopback HTTP server, Range handler, route table, payload builder |
| `visualizer/ui/index.html` | Page structure |
| `visualizer/ui/app.css` | Layout, theme, lane styling |
| `visualizer/ui/app.js` | Fetch payload, render lanes, wire interaction |

Modified modules:

| File | Change |
|---|---|
| `cli.py` | Register `visualize` subparser + `command_visualize` |
| `__init__.py` | Re-export `run_visualizer` if the package exposes a top-level API |
| `pyproject.toml` | Add `visualizer/ui/**` to sdist `include` |
| `README.md` | Document `visualize` in "Basic use" |
| `schemas/` | New `timeline-v0.1.schema.json` |

`visualizer/` is a subpackage of the existing distribution. It adds **no new runtime
dependency** — stdlib `http.server`, `functools`, `webbrowser`, plus the `numpy` and
FFmpeg already required.

> Note: the package is currently flat (`senate_audio_audit/*.py`). Introducing the
> `visualizer/` subpackage is a deliberate choice to keep ~600 lines of server and
> vendored asset code out of the top-level namespace. An alternative is
> `visualizer.py` + `visualizer_assets/`; the subpackage is preferred for testability.

### 4.2 Read-only guarantee

The visualizer must not be able to mutate analysis state. Enforced by construction:

- The server exposes **no** mutating HTTP method. `do_POST`/`do_PUT`/`do_DELETE` are
  not implemented, so any such request gets a `501` from the base handler.
- The only file writes in the entire feature are the timeline sidecar (§5.5), which is a
  derived, regenerable display artifact. It is written to `reports/metrics/` and is
  covered by the existing `/reports/` ignore rule.
- The page renders `review_status` but offers no control that changes it.

---

## 5. Server design

### 5.1 Loopback binding

- Bind `127.0.0.1` explicitly. **Never** `0.0.0.0` — the payload contains absolute
  filesystem paths and copyrighted-audio-derived measurements.
- `--port` defaults to `0` (OS-assigned ephemeral port) to avoid collisions with a
  developer already running something on 8080. Print the resolved URL.
- `--no-browser` suppresses `webbrowser.open`, for headless/remote use and for tests.
- `--once`/`--timeout` serve a single page load then exit, so a forgotten server does
  not linger. Consider a default idle timeout (e.g. 30 min) with an opt-out.

### 5.2 HTTP Range support — the critical detail

Python's `http.server.SimpleHTTPRequestHandler` **does not implement `Range`
requests**. Serving an 85 MB mp3 without Range support means the browser cannot seek
efficiently: Chrome may work by re-requesting, but Safari will not seek at all, and any
browser will buffer the whole file before allowing reliable scrubbing.

`visualizer/server.py` must therefore implement a Range-capable handler:

- Parse `Range: bytes=<start>-<end>`, including open-ended and suffix (`bytes=-N`) forms.
- Respond `206 Partial Content` with `Content-Range`, `Content-Length`,
  `Accept-Ranges: bytes`.
- Reject unsatisfiable ranges with `416` and a `Content-Range: bytes */<size>` header.
- Always advertise `Accept-Ranges: bytes` on the `200` response so the browser knows
  seeking is possible.
- Stream in bounded blocks (e.g. 256 KB) from a seekable file handle — never
  `read()` the whole file, and never load it into memory.
- Honor `If-Range` if trivially supportable; otherwise document that the sidecar is
  immutable per `run_id` so mid-stream changes are not expected.

Also send `Cache-Control: no-store` on the payload and a long `Cache-Control` on
`/audio`, and set `Content-Type: audio/mpeg` from the probed codec rather than by
extension.

### 5.3 Route table and path resolution

The audio path is resolved **server-side at startup** from
`snapshot["source"]["path"]`, falling back to matching `source.filename` inside a
user-supplied `--media-root`. It is never taken from a request query parameter.

This matters for security: a localhost server that maps client-supplied paths to
filesystem reads is a local file-disclosure primitive, because **any website open in
the user's browser can issue requests to `127.0.0.1`** (the browser does not apply
same-origin policy to the response, only to reading it — but side effects and timing
still apply, and simple `GET` requests are sent regardless of page origin).

Therefore:

- Serve only the four fixed routes above. No static file browsing, no directory
  listing, no path parameters.
- Resolve and `realpath()` the media path once, at startup; refuse to start if it does
  not exist or is not a regular file.
- Verify the media file's `sha256` matches `source.sha256` before serving, and warn
  loudly on mismatch (stale snapshot pointing at changed audio). Hashing 85 MB is ~0.2 s,
  so this is cheap; make it skippable with `--skip-verify` for large corpora.

### 5.4 Host header / DNS-rebinding defense

A second, subtler localhost risk is DNS rebinding: an attacker-controlled domain can
resolve to `127.0.0.1` and then issue same-origin requests to the server once the
victim's browser is pointed at it. Mitigation, cheap to implement:

- Validate the `Host` header against the expected `127.0.0.1[:port]` /
  `localhost[:port]` values; respond `403` otherwise.
- Do not send `Access-Control-Allow-Origin`. The page is same-origin only.
- Optionally generate a random per-session token, embed it in the URL, and require it
  on `/api/payload` and `/audio`. Cheap defense in depth; decide at implementation time.

### 5.5 Timeline sidecar

New display artifact, written next to the metrics pair:

```
reports/metrics/<stem>.timeline.json
```

**Contents:** envelope buckets, activity buckets, and the metadata needed to interpret
them. It deliberately does **not** duplicate `events` — those remain solely in
`*.metrics.json` so there is exactly one source of truth and no drift between two
copies. The server merges snapshot + timeline into one `/api/payload` response, and the
page derives event geometry itself.

**Cache key** — rebuild when any component differs:

```
stable_hash({ source_sha256, run_id, bucket_ms, timeline_version })
```

**Build algorithm** (reusing existing code, no new inference):

1. `media.iter_pcm_chunks(path, channels, start_ms, end_ms, chunk_seconds)` streams
   16 kHz mono float32 without loading the file.
2. Reuse `activity._LevelCollector(frame_ms=30)` to get 30 ms RMS frames in dBFS — the
   identical frame size and math used during analysis, so the envelope is consistent
   with `active_speech_median_dbfs` and `active_speech_p95_dbfs`.
3. Reduce frames into `bucket_ms` buckets (default 1000 ms) via max-RMS (dB domain
   average of power, not arithmetic mean of dB, to avoid understating peaks).
4. Clamp to `[-120, 0]` dBFS; replace non-finite with a floor sentinel.
5. Activity lane: run `create_activity_detector(...)` over the same stream to obtain
   `SpeechInterval`s, then mark each bucket with the fraction of its span covered by
   speech (0–255). This makes the activity lane honest at bucket resolution.

**Cost, stated honestly:** this is a **second decode pass** over the recording. For the
89-minute reference file, expect roughly 30–90 s of wall time for the pass (FFmpeg
mp3 decode is fast; the `onnxruntime` VAD inference over ~178k frames dominates if the
Silero backend is selected). Mitigations:

- The result is cached and reused; subsequent `visualize` calls are instant.
- Default to the **energy** detector for the timeline lane unless the Silero model is
  already present *and* the snapshot's `quality.activity_backend` matches, so the lane
  does not silently disagree with the metrics it sits under. The lane's `method` is
  recorded in the sidecar and rendered in the UI, and any disagreement with
  `quality.activity_method` is shown as a warning.
- `--timeline-bucket-ms` trades resolution for size.
- Longer term (§14), have `analyze` persist the envelope it already computes, removing
  the second pass entirely.

**Size budget** for the reference file at `bucket_ms = 1000`: 5,344 buckets.
Envelope as integers (dBFS × 10) ≈ 30 KB; activity as 5,344 small ints ≈ 15 KB;
total well under 60 KB uncompressed.

---

## 6. Frontend design

### 6.1 Layout

```
┌──────────────────────────────────────────────────────────────────┐
│ HEADER  filename · 01:29:03 · run_id · status badge             │
│         [provisional / uncalibrated banner — always visible]    │
├──────────────────────────────────────────────────────────────────┤
│ TRANSPORT  ◀◀  ▶/❚❚  ▶▶   00:12:34 / 01:29:03   [loop event]   │
│           volume ──────●────────                                │
├──────────────────────────────────────────────────────────────────┤
│ SUMMARY   vividness 10/10 (provisional)  speech 93.6%  quality   │
│           poor · level −37.7 dBFS med · p95 −24.9 · rate 149.7   │
│           wpm · F0 var 4.96 st · clipping 0.0 · events 41        │
├──────────────────────────────────────────────────────────────────┤
│ OVERVIEW  full-duration minimap: envelope + event density        │
├──────────────────────────────────────────────────────────────────┤
│ TIMELINE  lane 1  loudness envelope (dBFS)                       │
│           lane 2  speech activity (0–100%)                       │
│           lane 3  candidate events (color = claim_type,          │
│                     hatch = uncertain_candidate)                │
│           ruler   time ticks                                     │
│           playhead + zoom/pan + hover tooltip                    │
├──────────────────────────────────────────────────────────────────┤
│ EVENTS    filter: [type ▾] [state ▾] [review ▾]   41 shown      │
│           ┌ time range │ type │ state │ signals │ review │ ▶ ┐   │
│           │ 00:00:05 – 00:00:25  …                             │   │
├──────────────────────────────────────────────────────────────────┤
│ DETAIL    selected event: evidence, signal strengths,          │
│           transcript if present, review status, [▶ play] [⟲]   │
├──────────────────────────────────────────────────────────────────┤
│ FOOTER    limitations text (verbatim from reporting._LIMITATION)│
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 Lanes

1. **Loudness envelope** — filled area path, dBFS on a fixed domain (e.g. −80…0) so
   recordings are visually comparable. A horizontal reference line at the snapshot's
   `active_speech_median_dbfs` makes "this moment is louder than typical" readable at a
   glance, which is exactly the evidence the loud-delivery rule uses.
2. **Speech activity** — one column per bucket, opacity by speech fraction. For the
   reference file (93.6% speech) this lane is nearly solid; it is still shown because it
   makes the silences legible and would matter for a sparser recording.
3. **Candidate events** — one bar per event, `start_ms → end_ms`, aligned to the same
   time axis.
   - Fill = `claim_type` (2 types in the current corpus → 2 colors).
   - Hatched or outlined = `uncertain_candidate` vs solid = `supported_candidate`.
   - Opacity/outline = `review_status` (all `unreviewed` today).
   - Because at full zoom a 20 s event in an 89-minute file is ~0.4 px wide, events
     have a **minimum rendered width of 2 px** and the bar is drawn from a per-bucket
     max-intensity map so overlapping events remain visible.

**No transcript lane in v1.** With 1 of 41 events carrying a transcript, a dedicated
lane would be nearly empty. The one available transcript is rendered in the detail
panel instead. Deferred to §14.

### 6.3 Interaction

- **Seek:** click anywhere on any lane or the ruler → `audio.currentTime = ms/1000`.
- **Select event:** click an event bar → seeks to `start_ms`, loads the detail panel.
- **Play event:** `▶` on an event seeks to `start_ms`, plays, and auto-pauses at
  `end_ms` via a `timeupdate` guard. This is the core "run and hear the metrics" loop.
  Events have a `±` tolerance control (default ±250 ms) because boundaries come from
  30 ms VAD frames merged with padding and are not sample-accurate.
- **Replay:** `⟲` restarts the event span.
- **Zoom:** `Ctrl`/`Cmd` + wheel zooms around the cursor; plain wheel pans. Zoom range
  from full duration down to ~2 s. Double-click resets.
- **Keyboard:** `Space` play/pause, `←`/`→` ±5 s, `Shift`+arrows ±30 s, `J`/`K`
  previous/next event, `Enter` play selected event, `Esc` clear selection. Full keyboard
  operation is a requirement, not a nicety — reviewing 41 events with a mouse is slow.
- **Filters:** by `claim_type`, `decision_state`, `review_status`. Filtering hides
  non-matching bars *and* table rows, and shows "N of 41 shown".
- **Overview minimap:** a full-duration strip showing envelope plus event density, with
  a draggable viewport rectangle indicating the zoom window. Essential for orientation
  in an 89-minute file.

### 6.4 Rendering approach

- **Canvas** for the three lanes and the ruler: 5,344 buckets and up to a few hundred
  events are far beyond what DOM nodes handle smoothly, and canvas redraws cheaply on
  pan/zoom.
- **DOM** for the transport, summary, filters, event table, and detail panel — these
  benefit from text selection, accessibility, and native scrolling.
- Device-pixel-ratio aware backing store so the envelope is crisp on Retina displays.
- Redraw lanes on `requestAnimationFrame` during pan/zoom, coalescing events; do not
  redraw on every `timeupdate` (4 Hz is enough for the playhead — move the playhead in
  its own lightweight pass).

### 6.5 Do not decode the audio in the browser

The page must **not** call `decodeAudioData` on the full file. An 89-minute stereo
44.1 kHz recording is ~5.34 M frames × 2 ch × 4 bytes ≈ **2.0 GB** of Float32 — enough
to crash the tab on most machines. Audio is played through an `<audio>` element fed by
the Range-capable `/audio` endpoint, and all visualization comes from the precomputed
sidecar arrays. This constraint should be recorded as a comment in `app.js` so a future
maintainer does not "improve" it by decoding client-side.

### 6.6 Epistemic labeling in the UI

The project's core discipline is that these outputs describe observable or perceived
behavior, never internal emotion, intent, identity, or character. The page is a
**publication surface**, so it must carry that discipline visibly:

- Persistent banner: *provisional research output; not a determination of intent or
  character*.
- Vividness rendered as `10/10 · provisional_rubric · uncalibrated` — never a bare
  "10/10".
- Signal-strength decimals always labeled *uncalibrated signal* (mirroring
  `reporting._signal`), never as percentages or probabilities.
- Event types named as **candidates**; `decision_state` shown verbatim
  (`supported_candidate` / `uncertain_candidate`), never smoothed into a verdict.
- An explicit "no diarization — events are not attributed to named speakers" note,
  since the current data has `speaker_id: null` everywhere and a colored-by-speaker UI
  would imply a separation that does not exist.
- `quality.overall` is `poor` for the reference recording; the summary must show that
  prominently rather than letting a confident-looking visualization imply reliable
  measurements.
- Footer repeats `reporting._LIMITATION` verbatim, so the two renderers cannot drift.

---

## 7. CLI specification

```
senate-audio-audit visualize PATH
    [--metrics-dir DIR]        default: reports/metrics
    [--input-root DIR]
    [--port N]                 default: 0 (ephemeral)
    [--host ADDR]              default: 127.0.0.1; refuse non-loopback
    [--no-browser]
    [--timeline-bucket-ms N]   default: 1000
    [--rebuild-timeline]       ignore cache, recompute
    [--skip-verify]            skip media sha256 verification
    [--idle-timeout SECONDS]   default: 1800; 0 disables
```

Behavior:

- Resolves the committed snapshot through the **existing** recovery chain
  (`artifacts.read_latest` → pointer, latest JSON, newest history line), so it works
  with an interrupted-publication directory exactly like the `metrics` command.
- Fails cleanly with a non-zero exit and an actionable message when:
  - no snapshot exists → suggest `analyze` first;
  - the snapshot's `analysis_status` is `failed` → refuse to render an empty page and
    print recorded `errors`;
  - `analysis_status == "not_assessable"` (e.g. video without audio) → render a
    read-only page stating why, with a null-vividness panel and no lanes;
  - the media file is missing or its `sha256` differs from the snapshot;
  - FFmpeg is unavailable → still serve the events lane, and mark the envelope and
    activity lanes unavailable rather than failing the whole command.
- Prints the resolved URL and a one-line reminder that the server is loopback-only and
  read-only.
- Never mutates the snapshot, history, or review files.

---

## 8. Data contracts

### 8.1 `schemas/timeline-v0.1.schema.json`

```jsonc
{
  "schema_version": "0.1.0",
  "artifact_role": "visualizer_timeline",   // display-only, never a metrics artifact
  "run_id": "…", "run_key": "…",
  "source_sha256": "<64 hex>",              // must equal snapshot.source.sha256
  "duration_ms": 5343033,
  "bucket_ms": 1000,
  "bucket_count": 5344,
  "envelope": {
    "unit": "dbfs",
    "floor_dbfs": -80.0,                    // fixed render domain
    "values": [-381, -372, …]               // int, dBFS × 10
  },
  "activity": {
    "method": "silero-vad+transcript-fallback",
    "backend": "silero",
    "speech_ratio": 0.936133,
    "agrees_with_snapshot": true,           // false → UI shows a warning
    "values": [255, 255, 0, …]              // int 0–255, speech fraction per bucket
  },
  "warnings": ["…"],
  "generator": { "timeline_version": "0.1.0" }
}
```

`additionalProperties: true`; `events` are intentionally **absent** (§5.5).

### 8.2 `/api/payload`

```jsonc
{ "snapshot": { …verbatim *.metrics.json… },
  "timeline": { …verbatim <stem>.timeline.json… } }
```

Serving `snapshot` verbatim keeps the visualizer a pure reader: any field the pipeline
adds later is visible without touching the visualizer.

---

## 9. Performance and memory budget

| Item | Budget | Reference-file actual |
|---|---|---|
| Timeline build (first run) | < 120 s | ~30–90 s (second decode pass) |
| Timeline build (cached) | < 50 ms | file read + hash only |
| `/api/payload` | < 200 KB | ~53 KB snapshot + ~60 KB timeline |
| `/audio` first byte | < 250 ms | Range request, no full buffering |
| Initial page interactive | < 1 s | one fetch, no external assets |
| Client memory | < 100 MB | arrays only; **no** `decodeAudioData` |
| Peak server memory | < 50 MB | 256 KB streaming blocks |

---

## 10. Security and privacy

- Loopback-only binding; refuse a non-loopback `--host` unless a loud `--i-know` flag is
  added later. Never bind `0.0.0.0`.
- No mutating HTTP methods; no client-supplied filesystem paths (§5.3).
- `Host` header validation; no CORS headers (§5.4).
- The page must not display absolute filesystem paths. `source.path` and
  `quality.transcript_path` contain `/Users/enola/…`; the payload builder should
  replace them with basenames before sending, or the page should simply never render
  them. Recommended: strip server-side in the payload, keeping the on-disk snapshot
  unchanged.
- No external network requests, no CDN, no fonts, no telemetry — the page must render
  fully offline, consistent with the project's local-only guarantee.
- The server holds copyrighted-audio-derived measurements. It must not be left running
  unattended; hence the idle timeout and `Ctrl-C` handling.
- `.gitignore` already covers `/reports/`, so the sidecar is never committed. Verify that
  the `visualizer/ui/` assets **are** tracked — they are source, not build output, and
  the wheel/sdist `include` lists must name them explicitly.

---

## 11. Testing strategy

Extend the existing suite (currently 40 tests, `pytest`, `ruff` line-length 100,
`target-version = "py312"`). No test may touch the copyrighted corpus — use
`tests/factories.py` and a generated synthetic WAV.

**Timeline builder** (`tests/test_visualizer_timeline.py`)

- Bucket boundaries are exact at non-multiples of the bucket size (e.g. 5343033 ms at
  1000 ms → 5344 buckets, last bucket partial).
- Envelope is monotonic-consistent with a synthetic tone: a constant-amplitude tone
  yields a flat envelope within one LSB.
- dB-domain averaging does not understate a short loud transient inside a quiet bucket.
- Non-finite and out-of-domain values clamp to the floor sentinel.
- Cache key changes when `source_sha256`, `run_id`, or `bucket_ms` changes; a matching
  key reuses the file and does **not** re-invoke FFmpeg (assert with a monkeypatched
  builder that raises if called).
- Missing FFmpeg degrades to `activity.available = false` rather than raising.

**Range handler** (`tests/test_visualizer_server.py`)

- `Range: bytes=0-` → `206`, correct `Content-Range`, `Accept-Ranges: bytes`.
- `Range: bytes=100-199` → exactly 100 bytes, correct offset.
- Suffix form `bytes=-50` → last 50 bytes.
- Unsatisfiable (`bytes=999999999-`) → `416` with `Content-Range: bytes */<size>`.
- Malformed header → `416`, not `500`.
- `200` response (no Range) includes `Accept-Ranges: bytes`.
- `POST` → `501` (no write path).
- Unknown path → `404`; no directory listing.
- Non-loopback `Host` header → `403`.
- Bind is `127.0.0.1`; test uses port `0`.

**Payload** (`tests/test_visualizer_server.py`)

- Absolute paths are stripped/relativized in the served payload while the on-disk
  snapshot is unchanged.
- A `not_assessable` snapshot produces a valid payload with null vividness.
- Events in the payload match `*.metrics.json` exactly (guards against drift).

**CLI** (extend `tests/test_cli.py`)

- `visualize` with no snapshot → non-zero exit, message names `analyze`.
- `--no-browser --port 0` starts, serves `/api/payload`, and exits on idle timeout.
- `failed` status refuses to render.

**Frontend** — not unit-tested in this repo (no JS toolchain by decision). Instead:

- Hand-check against the reference recording: all 41 events visible, seeking accurate
  to ±250 ms, keyboard shortcuts work, and the page renders with the network disabled
  after first load.
- Assert in `app.js` (via a documented invariant comment) that no `decodeAudioData`
  call exists.

**Regression guard:** `ruff check`, `python -m compileall`, and the full suite must
pass. `uv sync --locked --python 3.12 --extra dev --extra vad` must remain clean, and
the wheel must build with the `visualizer/ui/**` assets included.

---

## 12. Milestones

| ID | Milestone | Status | Notes |
|---|---|---|---|
| V0 | Branch and plan | Complete | `feat/visualizer`; this document |
| V1 | Timeline sidecar | Planned | `timeline.py`, schema, envelope + activity builder, cache |
| V2 | Range-capable server | Planned | loopback bind, route table, payload, path stripping |
| V3 | Page shell and summary | Planned | transport, header, summary, limitations, not-assessable state |
| V4 | Lanes and zoom/pan | Planned | envelope, activity, events, ruler, minimap |
| V5 | Event interaction | Planned | seek, play-span, replay, tolerance, detail panel, filters, keyboard |
| V6 | Tests and packaging | Planned | timeline/server/CLI tests, sdist+wheel assets, README |
| V7 | Acceptance audit | Planned | reference-recording walkthrough, offline check, clean-install check |

---

## 13. Acceptance criteria

The visualizer pass is complete when:

- `senate-audio-audit visualize <path>` starts a loopback-only server and opens a page
  for any recording with a committed snapshot;
- the page shows the snapshot's summary metrics, all candidate events, the activity
  lane, and the loudness envelope, each labeled with its provenance
  (`quality.overall`, `activity_method`, `vividness.status`);
- clicking an event seeks the audio to its `start_ms`, and `▶` plays only that span and
  stops at `end_ms`;
- seeking works by range request — verified by scrubbing an 89-minute file in a browser
  that would otherwise buffer it;
- the page renders with no network access and no external asset;
- absolute filesystem paths never reach the rendered page;
- `review_status` is displayed and **no** UI control mutates any artifact;
- the vividness score always appears with its `provisional_rubric` status, and
  signal-strength values are labeled uncalibrated;
- a `not_assessable` or `failed` snapshot yields a clear explanatory page, not a blank
  or misleading one;
- a stale snapshot (media `sha256` mismatch) warns and refuses to present mismatched
  audio as the analyzed source;
- the timeline sidecar is cached and reused, and rebuilding is explicit;
- `ruff check`, `compileall`, and the full test suite pass on a clean
  `uv sync --locked` environment;
- the wheel contains `visualizer/ui/**` and `senate-audio-audit visualize` works from
  the installed wheel; and
- `README.md` and the progress log document the feature and its limitations.

---

## 14. Risks, limitations, and deferred work

### 14.1 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Second decode pass is slow on long recordings | Medium | Cache by `sha256`+`run_id`; later persist during `analyze` (§14.3) |
| Silent activity-lane/metrics disagreement | Medium | Record `agrees_with_snapshot`; render a warning; prefer the snapshot's backend |
| Localhost server read by a malicious local page | Medium | Fixed routes, no client paths, no CORS, `Host` check (§5.3–5.4) |
| Visual confidence misread as evidence | Medium | Persistent provisional banner, verbatim limitation footer, no verdicts |
| Stale snapshot paired with changed audio | Medium | `sha256` verification with a visible warning |
| Browser OOM if someone decodes audio client-side | Low | Documented invariant; envelope is precomputed |
| 41+ events clutter the timeline at full zoom | Low | Overview minimap, 2 px minimum bar width, filters, zoom |
| Unbounded growth of `events` in future snapshots | Low | Canvas rendering; filters; table virtualization if > 500 |

### 14.2 Known limitations of the visualizer

- It visualizes **candidates**, not findings. A bar is a prompt for human review.
- Event boundaries derive from 30 ms VAD frames with padding; they are not
  sample-accurate, which is why a play tolerance is exposed.
- With diarization disabled, events cannot be grouped by speaker, and overlapping
  speech cannot be attributed.
- The envelope is a display aid computed independently of the analysis pass; it is not
  a metric and must never be copied into `*.metrics.json`.
- The reference recording's `quality.overall` is `poor`, so the visualization is a
  faithful display of low-quality measurements.
- Text search across transcripts is not available (no transcript lane in v1).

### 14.3 Deferred, with intent

1. **Persist the envelope during `analyze`.** `_LevelCollector` already computes the
   exact 30 ms dBFS frames needed, so writing them (downsampled) during analysis would
   remove the second decode pass entirely. Cleanest follow-up, but it changes the
   analysis artifact and therefore belongs in its own change with its own tests.
2. **Transcript lane.** Renders SRT/VTT cues under the activity lane, with the
   sentiment-sidecar refusal from `transcripts.py` preserved. Worth it when coverage is
   real, not for 1 of 41 events.
3. **Review write-back.** Requires a POST endpoint, `reviews.jsonl` locking consistent
   with `util.file_lock`, the same validation as the `review` command, and an audit
   trail. Explicitly out of scope for v1.
4. **Cross-recording comparison.** A ledger view across `*.metrics.json` files with
   sortable vividness/speech-ratio columns. Separate feature.
5. **Static export.** `--export-static` emitting a portable single-file report for
   sharing without a running server. Deferred; the server is the approved path.
6. **Full waveform.** Only if a genuine review need appears; requires the peaks file
   and roughly doubles the sidecar.

---

## 15. Compliance with the base plan

- The visualizer adds **no** inference, changes **no** metric, and alters **no**
  existing artifact. `implementation-plan_v0.1.md` is not modified.
- The `provisional_rubric` vividness status, the no-emotion/no-intent framing, the
  recording-local speaker labels, and the human-review-before-quoting requirement are
  all carried into the UI, not weakened by it.
- Offline operation and the absence of cloud calls are preserved: no CDN, no telemetry,
  no network dependency in the page.
- The new feature is additive and opt-in; every existing CLI command, artifact, and test
  is unaffected.
