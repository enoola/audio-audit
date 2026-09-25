/* Local metrics visualizer.
 *
 * INVARIANT: this file must never call decodeAudioData() on the source audio.
 * An 89-minute stereo 44.1 kHz recording decodes to roughly 2 GB of Float32 and
 * will crash the tab. All drawing comes from the precomputed timeline sidecar;
 * playback goes through the <audio> element fed by the Range-capable /audio
 * endpoint. If you want a waveform, add a downsampled peaks file to the
 * sidecar rather than decoding client-side.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const TOKEN_Q = TOKEN ? `?t=${encodeURIComponent(TOKEN)}` : "";

const LIMITATION =
  "Local speaker IDs are not identities. Outputs are estimates of observable or " +
  "perceived behavior, not internal emotion, intent, diagnosis, or moral character.";

const TYPE_COLORS = {
  high_arousal_forceful_delivery: "#f85149",
  relative_loud_delivery: "#d29922",
  potentially_disrespectful_act: "#a371f7",
  sarcasm_or_irony_candidate: "#db61a2",
  perceived_anger_expression: "#ff7b72",
};
const FALLBACK_COLOR = "#58a6ff";
const COLOR = (type) => TYPE_COLORS[type] || FALLBACK_COLOR;

const state = {
  snapshot: null,
  timeline: null,
  events: [],
  view: { start: 0, end: 0 },
  selected: null,
  stopAt: null,
  tolerance: 250,
  envelope: [],
  activity: [],
  bucketMs: 1000,
  scale: 10,
  floorDbfs: -80,
  reference: null,
  lanesAvailable: true,
};

/* ---------- formatting ---------- */
function ts(ms) {
  const v = Math.max(0, Math.round(ms || 0));
  const h = Math.floor(v / 3600000);
  const m = Math.floor((v % 3600000) / 60000);
  const s = Math.floor((v % 60000) / 1000);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(v % 1000).padStart(3, "0")}`;
}
function shortTs(ms) {
  const v = Math.max(0, Math.round(ms || 0));
  const h = Math.floor(v / 3600000);
  const m = Math.floor((v % 3600000) / 60000);
  const s = Math.floor((v % 60000) / 1000);
  return (h ? `${h}:` : "") + `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
function num(value, digits = 2, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
  return Number(value).toFixed(digits) + suffix;
}
function pct(value) {
  if (value === null || value === undefined) return "N/A";
  return (Number(value) * 100).toFixed(1) + "%";
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ---------- boot ---------- */
async function boot() {
  const audio = $("audio");
  audio.src = `/audio${TOKEN_Q}`;

  let payload;
  try {
    const response = await fetch(`/api/payload${TOKEN_Q}`);
    if (!response.ok) throw new Error(`payload ${response.status}`);
    payload = await response.json();
  } catch (error) {
    $("boot").textContent = `Could not load metrics: ${error.message}`;
    return;
  }

  const snapshot = payload.snapshot || {};
  state.snapshot = snapshot;
  state.timeline = payload.timeline;
  state.events = (snapshot.events || []).slice().sort((a, b) => a.start_ms - b.start_ms);

  const timeline = state.timeline || {};
  state.envelope = (timeline.envelope && timeline.envelope.values) || [];
  state.activity = (timeline.activity && timeline.activity.values) || [];
  state.bucketMs = timeline.bucket_ms || 1000;
  state.scale = (timeline.envelope && timeline.envelope.scale) || 10;
  state.floorDbfs = (timeline.envelope && timeline.envelope.floor_dbfs) ?? -80;
  state.reference = timeline.envelope ? timeline.envelope.reference_dbfs : null;
  state.lanesAvailable = Boolean(timeline.envelope && timeline.envelope.available);

  render();
  wire();
  $("boot").hidden = true;
  $("page").hidden = false;
  resize();
  window.addEventListener("resize", resize);
}

/* ---------- header, alerts, summary ---------- */
function render() {
  const s = state.snapshot;
  const source = s.source || {};
  const quality = s.quality || {};
  const measurements = s.measurements || {};
  const summary = s.summary || {};

  $("filename").textContent = source.filename || "unknown recording";
  $("runid").textContent = (s.run_id || "—").slice(0, 8);
  $("total").textContent = ts(source.duration_ms);
  $("pos").textContent = ts(0);

  const status = s.analysis_status || "unknown";
  const statusBadge = $("status");
  statusBadge.textContent = status;
  statusBadge.className = "badge " + (status === "completed" ? "ok" : "warn");

  renderAlerts(s, status);

  const tiles = $("tiles");
  tiles.replaceChildren();
  const speechOk = summary.speech_present === "yes";
  const qualityOverall = quality.overall || "unknown";
  addTile(tiles, "Active speech", pct(summary.speech_ratio ?? measurements.speech_ratio),
    `${measurements.active_speech_duration_ms ? Math.round(measurements.active_speech_duration_ms / 1000) + " s" : "N/A"} · ${speechOk ? "speech present" : "speech absent"}`,
    qualityOverall !== "good");
  addTile(tiles, "Median level", num(measurements.active_speech_median_dbfs, 2, " dBFS"), "active-speech 30 ms frames");
  addTile(tiles, "P95 level", num(measurements.active_speech_p95_dbfs, 2, " dBFS"), "active-speech 30 ms frames");
  addTile(tiles, "Speech rate", num(measurements.median_speech_rate_wpm, 1, " wpm"), "median, transcript-derived");
  addTile(tiles, "Pause ratio", num(measurements.median_pause_ratio, 4), "median");
  addTile(tiles, "F0 variation", num(measurements.f0_variation_semitones, 2, " st"), `median F0 ${num(measurements.f0_median_hz, 1, " Hz")}`);
  addTile(tiles, "Clipping", num(measurements.clipping_fraction, 6), "fraction of samples");
  addTile(tiles, "Measurement quality", qualityOverall,
    `activity ${quality.activity_method || "unknown"}`, qualityOverall !== "good");
  addTile(tiles, "Candidate events", String(state.events.length),
    `${state.events.filter((e) => e.review_status !== "unreviewed").length} reviewed`);
  addTile(tiles, "Speakers", (s.speaker_summaries || []).length ? String(s.speaker_summaries.length) : "none",
    "diarization not enabled", true);

  renderVividness(s.vividness || {});
  renderLegend();
  renderActivityLabel();
  renderTable();
  renderLimitations();
}

function addTile(parent, key, value, note, warn) {
  const tile = el("div", "tile" + (warn ? " warn" : ""));
  tile.append(el("div", "k", key), el("div", "v", value), el("div", "n", note || ""));
  parent.append(tile);
}

function renderAlerts(s, status) {
  const box = $("alerts");
  box.replaceChildren();
  const add = (text, kind) => {
    if (text) box.append(el("div", "alert " + kind, text));
  };

  if (status === "not_assessable") {
    add("This recording is marked not_assessable: " + ((s.summary || {}).status_note || "no detail recorded") +
      ". No timeline lanes are shown because there is no speech to display.", "bad");
  }
  if (status === "failed") {
    const errors = (s.errors || []).map((e) => `${e.stage || "unknown"}: ${e.message || ""}`).join("; ");
    add("The recorded analysis failed, so nothing here is a finding. " + (errors || ""), "bad");
  }

  for (const warning of (s.quality || {}).warnings || []) add(warning, "warn");
  for (const warning of (state.timeline || {}).warnings || []) add(warning, "warn");

  const activity = (state.timeline || {}).activity || {};
  if (activity.available && activity.method && /transcript/.test(activity.method)) {
    add("The activity lane was derived from transcript cue intervals, not from VAD-detected speech. " +
      "It shows where the transcript covers the recording.", "warn");
  }
  if (activity.agrees_with_snapshot === false) {
    add("This lane was recomputed independently and does not match the recorded speech ratio. " +
      "Treat it as indicative only.", "warn");
  }
  if (!state.lanesAvailable) {
    add("Timeline lanes are unavailable (FFmpeg or the VAD backend could not run). " +
      "Events and metrics below are still exact.", "warn");
  }
  const env = (state.timeline || {}).envelope || {};
  if (env.available && env.reference_dbfs !== null && env.reference_dbfs !== undefined) {
    add(`${env.reference_label}: ${num(env.reference_dbfs, 2, " dBFS")}. ${env.reference_note}`, "");
  }
  if (!(s.speaker_summaries || []).length) {
    add("Diarization is not enabled: events are not attributed to named speakers.", "");
  }
  if ((s.source || {}).rights_status && /unverified/i.test(s.source.rights_status)) {
    add("Source rights status has not been verified. Do not redistribute this audio or its metrics.", "warn");
  }
}

function renderVividness(v) {
  const score = $("vscore");
  const status = $("vstatus");
  const meter = $("vmeter");
  if (v.score === null || v.score === undefined) {
    score.textContent = "N/A";
    status.textContent = v.status || "not_assessable";
    status.className = "badge warn";
    meter.style.width = "0%";
    $("vexplain").textContent = v.explanation || "";
    return;
  }
  score.textContent = `${v.score}/10`;
  const provisional = v.status === "provisional_rubric" || v.status === "not_assessable";
  status.textContent = `${v.status} · uncalibrated · ${v.confidence_band || "unknown"} confidence`;
  status.className = "badge " + (provisional ? "warn" : "ok");
  meter.style.width = `${Math.max(0, Math.min(100, v.score * 10))}%`;
  $("vexplain").textContent =
    (v.explanation || "") +
    (v.expected_rating !== null && v.expected_rating !== undefined
      ? ` Expected rating ${num(v.expected_rating, 2)}; the rubric is not calibrated to human ratings.`
      : "");
}

function renderActivityLabel() {
  const activity = (state.timeline || {}).activity || {};
  const label = $("actlabel");
  if (!activity.available) {
    label.textContent = "Speech activity · unavailable";
    return;
  }
  const method = activity.method || "unknown";
  const backend = activity.backend || "unknown";
  label.innerHTML = "";
  label.append(document.createTextNode("Speech activity "));
  const em = el("em", null, `${method} · ${pct(activity.speech_ratio)}`);
  label.append(em);
}

function renderLegend() {
  const legend = $("legend");
  legend.replaceChildren();
  const types = [...new Set(state.events.map((e) => e.claim_type))].sort();
  for (const type of types) {
    const item = el("span");
    const swatch = el("i");
    swatch.style.background = COLOR(type);
    item.append(swatch, document.createTextNode(type));
    legend.append(item);
  }
  const uncertain = el("span");
  const uswatch = el("i");
  uswatch.style.background = "transparent";
  uswatch.style.border = "1px dashed var(--dim)";
  uncertain.append(uswatch, document.createTextNode("dashed outline = uncertain_candidate"));
  legend.append(uncertain);
  const speech = el("span");
  const ss = el("i");
  ss.style.background = "var(--speech)";
  speech.append(ss, document.createTextNode("activity lane"));
  legend.append(speech);
}

function renderLimitations() {
  const box = $("limitations");
  box.replaceChildren();
  box.append(el("div", null, "Limitations"));
  const list = el("ul");
  for (const text of [
    LIMITATION,
    "Decimal values are uncalibrated signal strengths until probability calibration exists.",
    "Review the original audio before quoting or publishing any event.",
    "The loudness envelope is a display aid computed independently of the analysis pass; it is not a metric.",
    "Event boundaries come from 30 ms VAD frames and are not sample-accurate.",
  ]) list.append(el("li", null, text));
  box.append(list);
}

/* ---------- view state ---------- */
function scope() {
  const t = state.timeline || {};
  const start = t.scope_start_ms || 0;
  const duration = t.duration_ms || (state.snapshot.source || {}).duration_ms || 1;
  return { start, end: start + duration };
}
function setView(start, end) {
  const { start: lo, end: hi } = scope();
  const minSpan = 2000;
  let span = Math.max(minSpan, Math.min(end - start, hi - lo));
  let from = Math.max(lo, Math.min(start, hi - span));
  state.view = { start: from, end: from + span };
  draw();
  renderTable();
}
function resetView() {
  const { start, end } = scope();
  state.view = { start, end };
  draw();
  renderTable();
}
function xToMs(x, width) {
  const ratio = width > 0 ? x / width : 0;
  return state.view.start + ratio * (state.view.end - state.view.start);
}
function msToX(ms, width) {
  const span = state.view.end - state.view.start;
  if (span <= 0) return 0;
  return ((ms - state.view.start) / span) * width;
}

/* ---------- drawing ---------- */
function prep(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 800;
  const height = canvas.clientHeight || parseInt(canvas.getAttribute("height"), 10) || 60;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function draw() {
  drawEnvelope();
  drawActivity();
  drawEvents();
  drawRuler();
  drawPlayhead();
}

function drawEnvelope() {
  const { ctx, width, height } = prep($("cEnv"));
  if (!state.envelope.length) {
    ctx.fillStyle = "#6b7785";
    ctx.font = "12px sans-serif";
    ctx.fillText("loudness envelope unavailable", 8, height / 2);
    return;
  }
  const lo = state.floorDbfs;
  const top = 0;
  const yOf = (db) => {
    const clamped = Math.max(lo, Math.min(0, db));
    return height - ((clamped - lo) / (0 - lo)) * height;
  };

  ctx.strokeStyle = "#2a3240";
  ctx.lineWidth = 1;
  for (let db = 0; db >= lo; db -= 20) {
    const y = Math.round(yOf(db)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
    ctx.fillStyle = "#4a5563";
    ctx.font = "9px monospace";
    ctx.fillText(`${db}`, 2, y - 1);
  }

  if (state.reference !== null && state.reference !== undefined) {
    const y = Math.round(yOf(state.reference)) + 0.5;
    ctx.strokeStyle = "#d2992288";
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  const { start: viewStart } = state.view;
  const firstIndex = Math.max(0, Math.floor((viewStart - (state.timeline.scope_start_ms || 0)) / state.bucketMs));
  const step = Math.max(1, Math.ceil(state.envelope.length / Math.max(1, width)));

  ctx.beginPath();
  ctx.moveTo(0, height);
  let started = false;
  for (let i = firstIndex; i < state.envelope.length; i += step) {
    const ms = (state.timeline.scope_start_ms || 0) + i * state.bucketMs;
    if (ms > state.view.end) break;
    const x = msToX(ms, width);
    const y = yOf(state.envelope[i] / state.scale);
    if (!started) { ctx.lineTo(x, y); started = true; }
    else ctx.lineTo(x, y);
  }
  ctx.lineTo(width, height);
  ctx.closePath();
  const gradient = ctx.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, "#58a6ff88");
  gradient.addColorStop(1, "#58a6ff18");
  ctx.fillStyle = gradient;
  ctx.fill();
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 1;
  ctx.stroke();
}

function drawActivity() {
  const { ctx, width, height } = prep($("cAct"));
  const activity = (state.timeline || {}).activity || {};
  if (!activity.available || !state.activity.length) {
    ctx.fillStyle = "#6b7785";
    ctx.font = "12px sans-serif";
    ctx.fillText("speech activity unavailable", 8, height / 2);
    return;
  }
  const scopeStart = state.timeline.scope_start_ms || 0;
  for (let i = 0; i < state.activity.length; i += 1) {
    const ms = scopeStart + i * state.bucketMs;
    if (ms < state.view.start || ms > state.view.end) continue;
    const x = msToX(ms, width);
    const value = state.activity[i] / 255;
    if (value <= 0) continue;
    const barWidth = Math.max(1, msToX(ms + state.bucketMs, width) - x);
    ctx.fillStyle = `rgba(46,160,67,${0.25 + 0.75 * value})`;
    ctx.fillRect(x, 0, barWidth, height);
  }
}

function drawEvents() {
  const { ctx, width, height } = prep($("cEvents"));
  const filters = activeFilters();
  for (const event of state.events) {
    if (!passes(event, filters)) continue;
    const x1 = msToX(event.start_ms, width);
    const x2 = msToX(event.end_ms, width);
    if (x2 < 0 || x1 > width) continue;
    const left = Math.max(0, x1);
    const right = Math.min(width, x2);
    // The shortest events in the reference corpus are 5 s, which is ~1.35 px at 1440 px
    // across an 89-minute timeline, so bars get a 2 px floor to stay visible and
    // clickable at full zoom.
    const barWidth = Math.max(2, right - left);
    const uncertain = event.decision_state === "uncertain_candidate";
    const selected = state.selected && state.selected.event_id === event.event_id;
    const top = 6;
    const barHeight = height - 14;

    ctx.fillStyle = COLOR(event.claim_type);
    ctx.globalAlpha = selected ? 1 : 0.75;
    ctx.fillRect(left, top, barWidth, barHeight);
    if (uncertain) {
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#e6edf3";
      ctx.setLineDash([3, 2]);
      ctx.lineWidth = 1;
      ctx.strokeRect(left + 0.5, top + 0.5, Math.max(1, barWidth - 1), barHeight - 1);
      ctx.setLineDash([]);
    }
    if (selected) {
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.strokeRect(left - 1, top - 1, barWidth + 2, barHeight + 2);
    }
    ctx.globalAlpha = 1;
  }
  if (state.events.length) {
    ctx.fillStyle = "#6b7785";
    ctx.font = "9px monospace";
    ctx.fillText(`${state.events.length} events`, 4, height - 3);
  }
}

function drawRuler() {
  const { ctx, width, height } = prep($("cRuler"));
  const span = state.view.end - state.view.start;
  const targets = [100, 250, 500, 1000, 5000, 15000, 30000, 60000, 300000, 900000, 1800000, 3600000];
  const step = targets.find((t) => span / t <= 12) || 3600000;
  const first = Math.ceil(state.view.start / step) * step;
  ctx.strokeStyle = "#2a3240";
  ctx.fillStyle = "#6b7785";
  ctx.font = "9px monospace";
  ctx.lineWidth = 1;
  for (let ms = first; ms <= state.view.end; ms += step) {
    const x = Math.round(msToX(ms, width)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
    ctx.fillText(shortTs(ms), x + 2, height - 5);
  }
}

function drawPlayhead() {
  const audio = $("audio");
  const canvas = $("cPlay");
  const lanes = document.querySelector(".lanes");
  const first = $("cEnv");
  const last = $("cRuler");
  if (!lanes || !first || !last) return;
  const stackHeight = Math.round(
    last.getBoundingClientRect().bottom - first.getBoundingClientRect().top
  );
  if (stackHeight > 0) canvas.style.height = `${stackHeight}px`;

  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 800;
  const height = canvas.clientHeight || stackHeight || 208;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!audio.duration || !Number.isFinite(audio.duration)) return;
  const x = msToX(audio.currentTime * 1000, width);
  if (x < 0 || x > width) return;
  ctx.strokeStyle = "#ffffffcc";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(Math.round(x) + 0.5, 0);
  ctx.lineTo(Math.round(x) + 0.5, height);
  ctx.stroke();
}

function resize() {
  draw();
}

/* ---------- events table ---------- */
function activeFilters() {
  return { type: $("fType").value, st: $("fState").value, rev: $("fReview").value };
}
function passes(event, f) {
  if (f.type && event.claim_type !== f.type) return false;
  if (f.st && event.decision_state !== f.st) return false;
  if (f.rev && (event.review_status || "unreviewed") !== f.rev) return false;
  return true;
}

function renderTable() {
  const filters = activeFilters();
  const body = $("events").querySelector("tbody");
  body.replaceChildren();
  const visible = state.events.filter((e) => passes(e, filters));
  $("shown").textContent = `${visible.length} of ${state.events.length} shown`;
  $("evcount").textContent = `· ${visible.length}`;

  for (const event of visible) {
    const row = el("tr");
    if (state.selected && state.selected.event_id === event.event_id) row.className = "sel";
    row.append(el("td", "num", ts(event.start_ms)));
    row.append(el("td", "num", ts(event.end_ms)));

    const typeCell = el("td");
    const swatch = el("i");
    swatch.style.cssText = `display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;background:${COLOR(event.claim_type)}`;
    typeCell.append(swatch, document.createTextNode(event.claim_type));
    row.append(typeCell);

    const stateCell = el("td", null, event.decision_state);
    if (event.decision_state === "uncertain_candidate") stateCell.style.color = "var(--warn)";
    row.append(stateCell);

    const signals = event.signal_strengths || {};
    const parts = Object.entries(signals)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => `${k.split("_")[0]} ${Number(v).toFixed(3)}`);
    row.append(el("td", "num", parts.length ? parts.join("  ") : "—"));

    const review = event.review_status || "unreviewed";
    const reviewCell = el("td");
    reviewCell.append(el("span", "badge " + (review === "unreviewed" ? "" : "ok"), review));
    row.append(reviewCell);

    const actionCell = el("td");
    const playButton = el("button", null, "▶");
    playButton.title = "Play this event";
    actionCell.append(playButton);
    row.append(actionCell);

    row.addEventListener("click", () => select(event, true));
    playButton.addEventListener("click", (clickEvent) => {
      clickEvent.stopPropagation();
      playEvent(event);
    });
    body.append(row);
  }
}

function populateFilters() {
  const fill = (id, values) => {
    const select = $(id);
    for (const value of values) {
      const option = el("option", null, value);
      option.value = value;
      select.append(option);
    }
  };
  fill("fType", [...new Set(state.events.map((e) => e.claim_type))].sort());
  fill("fState", [...new Set(state.events.map((e) => e.decision_state))].sort());
  fill("fReview", [...new Set(state.events.map((e) => e.review_status || "unreviewed"))].sort());
}

/* ---------- selection and playback ---------- */
function select(event, seek) {
  state.selected = event;
  renderDetail(event);
  renderTable();
  drawEvents();
  if (seek) seekTo(event.start_ms / 1000);
}

function renderDetail(event) {
  const panel = $("detail");
  panel.hidden = false;
  $("dTitle").textContent = `${event.claim_type} · ${ts(event.start_ms)}–${ts(event.end_ms)}`;
  const grid = $("dGrid");
  grid.replaceChildren();

  const field = (key, value) => {
    const box = el("div", "dfield");
    box.append(el("div", "k", key), el("div", "v", value));
    grid.append(box);
  };

  field("event id", event.event_id || "—");
  field("claim type", event.claim_type);
  field("epistemic status", event.epistemic_status || "—");
  field("decision state", event.decision_state);
  field("score semantics", event.score_semantics || "—");
  field("review status", event.review_status || "unreviewed");
  field("speaker", event.speaker_id || "unavailable (diarization disabled)");

  for (const [name, value] of Object.entries(event.signal_strengths || {})) {
    field(name, value === null || value === undefined
      ? "not_assessable"
      : `uncalibrated signal ${Number(value).toFixed(3)}`);
  }

  const evidence = event.evidence || {};
  for (const [name, value] of Object.entries(evidence)) {
    if (value === null || value === undefined) continue;
    field(name, typeof value === "number" ? Number(value).toFixed(3) : String(value));
  }
  if (event.transcript) field("transcript", `“${event.transcript}”`);
}

function seekTo(seconds) {
  const audio = $("audio");
  audio.currentTime = Math.max(0, seconds);
  $("pos").textContent = ts(audio.currentTime * 1000);
  drawPlayhead();
}

function playEvent(event) {
  const audio = $("audio");
  select(event, false);
  const start = Math.max(0, event.start_ms / 1000 - state.tolerance / 1000);
  const end = (event.end_ms + state.tolerance) / 1000;
  audio.currentTime = start;
  state.stopAt = end;
  $("playmode").textContent = `playing ${ts(event.start_ms)}–${ts(event.end_ms)}`;
  audio.play().catch(() => { $("playmode").textContent = "press play to start"; });
}

function nearestEvent(direction) {
  const filters = activeFilters();
  const visible = state.events.filter((e) => passes(e, filters));
  if (!visible.length) return null;
  const current = $("audio").currentTime * 1000;
  if (direction > 0) return visible.find((e) => e.start_ms > current + 200) || null;
  const before = visible.filter((e) => e.start_ms < current - 200);
  return before.length ? before[before.length - 1] : null;
}

/* ---------- interaction ---------- */
function wire() {
  const audio = $("audio");
  populateFilters();
  resetView();

  $("play").addEventListener("click", () => {
    if (audio.paused) {
      audio.play().catch(() => {});
      state.stopAt = null;
      $("playmode").textContent = "free playback";
    } else {
      audio.pause();
    }
  });

  audio.addEventListener("play", () => { $("play").textContent = "❚❚"; $("play").setAttribute("aria-pressed", "true"); });
  audio.addEventListener("pause", () => { $("play").textContent = "▶"; $("play").setAttribute("aria-pressed", "false"); });

  audio.addEventListener("timeupdate", () => {
    const seconds = audio.currentTime;
    $("pos").textContent = ts(seconds * 1000);
    drawPlayhead();
    if (state.stopAt !== null && seconds >= state.stopAt) {
      audio.pause();
      state.stopAt = null;
      $("playmode").textContent = "event ended · paused";
    }
  });

  audio.addEventListener("loadedmetadata", () => {
    if (Number.isFinite(audio.duration) && audio.duration > 0) {
      $("total").textContent = ts(audio.duration * 1000);
    }
  });

  $("back5").addEventListener("click", () => seekTo(audio.currentTime - 5));
  $("fwd5").addEventListener("click", () => seekTo(audio.currentTime + 5));
  $("rate").addEventListener("change", (event) => { audio.playbackRate = Number(event.target.value); });
  $("tol").addEventListener("change", (event) => { state.tolerance = Math.max(0, Number(event.target.value) || 0); });

  for (const id of ["fType", "fState", "fReview"]) {
    $(id).addEventListener("change", () => { renderTable(); drawEvents(); });
  }

  $("dPlay").addEventListener("click", () => { if (state.selected) playEvent(state.selected); });
  $("dReplay").addEventListener("click", () => { if (state.selected) playEvent(state.selected); });

  const seekFrom = (clickEvent) => {
    const canvas = clickEvent.currentTarget;
    const rect = canvas.getBoundingClientRect();
    seekTo(xToMs(clickEvent.clientX - rect.left, rect.width) / 1000);
  };
  for (const id of ["cEnv", "cAct", "cRuler"]) {
    $(id).addEventListener("click", seekFrom);
  }
  $("cEvents").addEventListener("click", (clickEvent) => {
    const canvas = clickEvent.currentTarget;
    const rect = canvas.getBoundingClientRect();
    const ms = xToMs(clickEvent.clientX - rect.left, rect.width);
    const filters = activeFilters();
    let best = null;
    let bestDistance = Infinity;
    for (const event of state.events) {
      if (!passes(event, filters)) continue;
      const distance = ms < event.start_ms ? event.start_ms - ms : ms > event.end_ms ? ms - event.end_ms : 0;
      if (distance < bestDistance) { bestDistance = distance; best = event; }
    }
    const tolerancePx = (state.tolerance / (state.view.end - state.view.start)) * rect.width;
    if (best && bestDistance <= Math.max(tolerancePx, 6)) select(best, true);
    else seekTo(ms / 1000);
  });

  // Zoom with ctrl/cmd + wheel around the cursor; plain wheel pans.
  const lanes = document.querySelector(".lanes");
  lanes.addEventListener("wheel", (wheelEvent) => {
    wheelEvent.preventDefault();
    // Measure the lane content box rather than assuming the container's padding,
    // so the zoom anchor matches the canvas the user is pointing at.
    const rect = $("cEnv").getBoundingClientRect();
    const ratio = rect.width > 0 ? (wheelEvent.clientX - rect.left) / rect.width : 0.5;
    const anchor = state.view.start + ratio * (state.view.end - state.view.start);
    if (wheelEvent.ctrlKey || wheelEvent.metaKey) {
      const factor = wheelEvent.deltaY > 0 ? 1.25 : 0.8;
      const span = (state.view.end - state.view.start) * factor;
      setView(anchor - ratio * span, anchor + (1 - ratio) * span);
    } else {
      const shift = ((state.view.end - state.view.start) * wheelEvent.deltaY) / 400;
      setView(state.view.start + shift, state.view.end + shift);
    }
  }, { passive: false });

  lanes.addEventListener("mousemove", (mouseEvent) => {
    const canvas = $("cEvents");
    const rect = canvas.getBoundingClientRect();
    if (mouseEvent.clientY < rect.top || mouseEvent.clientY > rect.bottom) {
      $("tooltip").hidden = true;
      return;
    }
    const ms = xToMs(mouseEvent.clientX - rect.left, rect.width);
    const filters = activeFilters();
    let best = null;
    let bestDistance = Infinity;
    for (const event of state.events) {
      if (!passes(event, filters)) continue;
      const distance = ms < event.start_ms ? event.start_ms - ms : ms > event.end_ms ? ms - event.end_ms : 0;
      if (distance < bestDistance) { bestDistance = distance; best = event; }
    }
    const tolerancePx = (state.tolerance / (state.view.end - state.view.start)) * rect.width;
    const tip = $("tooltip");
    if (best && bestDistance <= Math.max(tolerancePx, 6)) {
      tip.hidden = false;
      tip.innerHTML = "";
      tip.append(el("div", null, `${ts(best.start_ms)} – ${ts(best.end_ms)}`));
      const line = el("div");
      line.append(el("b", null, best.claim_type));
      tip.append(line);
      tip.append(el("div", null, `${best.decision_state} · ${best.review_status || "unreviewed"}`));
      const signals = Object.entries(best.signal_strengths || {})
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => `${k}: uncalibrated ${Number(v).toFixed(3)}`);
      if (signals.length) tip.append(el("div", null, signals.join(" · ")));
      const left = Math.min(mouseEvent.clientX - rect.left + 12, rect.width - 340);
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${mouseEvent.clientY - rect.top + 12}px`;
    } else {
      tip.hidden = true;
    }
  });
  lanes.addEventListener("mouseleave", () => { $("tooltip").hidden = true; });

  document.addEventListener("keydown", (keyEvent) => {
    if (keyEvent.target instanceof HTMLInputElement || keyEvent.target instanceof HTMLSelectElement) return;
    const audioEl = $("audio");
    switch (keyEvent.key) {
      case " ":
        keyEvent.preventDefault();
        $("play").click();
        break;
      case "ArrowLeft":
        keyEvent.preventDefault();
        seekTo(audioEl.currentTime - (keyEvent.shiftKey ? 30 : 5));
        break;
      case "ArrowRight":
        keyEvent.preventDefault();
        seekTo(audioEl.currentTime + (keyEvent.shiftKey ? 30 : 5));
        break;
      case "j": {
        const next = nearestEvent(1);
        if (next) { select(next, true); ensureVisible(next); }
        break;
      }
      case "k": {
        const previous = nearestEvent(-1);
        if (previous) { select(previous, true); ensureVisible(previous); }
        break;
      }
      case "Enter":
        if (state.selected) playEvent(state.selected);
        break;
      case "Escape":
        state.selected = null;
        state.stopAt = null;
        audioEl.pause();
        $("detail").hidden = true;
        renderTable();
        drawEvents();
        break;
      case "0":
        resetView();
        break;
      default:
        break;
    }
  });
}

function ensureVisible(event) {
  const span = state.view.end - state.view.start;
  if (event.start_ms < state.view.start || event.end_ms > state.view.end) {
    setView(event.start_ms - span * 0.1, event.start_ms + span * 0.9);
  }
}

boot();
