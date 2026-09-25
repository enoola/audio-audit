# Implementation Plan v0 — Audio Vividness and Interaction Analysis for French Sénat Recordings

**Status:** Proposed implementation plan  
**Version:** 0.1  
**Date:** 2026-09-24  
**Primary input:** One local audio/video path per analysis, with optional batch mode  
**Proposed implementation name:** `senat-audio-audit` (working name)

> This document is a plan, not an implementation and not legal advice. It deliberately replaces claims about a person's inner emotion with claims about observable delivery and perceived behavior. Any public-facing use requires source permission, a privacy review, model licensing review, and human editorial review.

---

## 1. Executive summary

Build a local-first Python command-line application that:

1. accepts a specific audio or video file as a required parameter;
2. determines whether speech is present;
3. reuses an existing French subtitle/transcript when available;
4. separates anonymous speakers locally, when reliable enough;
5. measures objective signal properties such as digital level, clipping, speech rate, pitch variation, overlap, and pauses;
6. produces **timestamped candidate events** for:
   - relatively loud or high-effort delivery;
   - high-arousal/forceful delivery and perceived anger expression;
   - potentially disrespectful acts;
   - sarcasm/irony candidates;
7. produces a whole-file **audio vividness score from 0 to 10**;
8. writes a stable, human-readable text ledger and a richer JSON/JSONL evidence trail; and
9. allows a human reviewer to confirm, reject, or mark each event as uncertain.

The 0–10 score will measure **observable expressiveness and interactional salience**, not goodness, badness, intelligence, ideology, credibility, or actual internal emotion. A score is not a probability. Confidence and score must be reported separately.

Before human calibration exists, outputs must be labelled `provisional` or `uncalibrated`. The system must not present an arbitrary weighted sum as a scientifically validated emotion score.

---

## 2. Required epistemic distinction

The requested concepts mix signal measurements, social interpretations, and internal mental states. They must not be implemented as one generic “emotion” class.

| User concept | Planned report label | What it means | What it does not mean |
|---|---|---|---|
| Video contains speech | `speech_present` | Speech activity was detected by VAD, with transcript/audio agreement where possible | Every detected vocalization is intelligible human speech |
| Speaking loudly | `relative_loud_delivery` | Recorded vocal level/effort is elevated relative to the same anonymous speaker or recording | Calibrated sound-pressure level in dB SPL; a permanent trait of the speaker |
| Angry | `perceived_anger_expression` / `high_arousal_forceful_delivery` | The delivery may sound angry, forceful, tense, or confrontational | The speaker is actually angry |
| Disrespectful | `potentially_disrespectful_act` | An utterance or interaction may be insulting, contemptuous, demeaning, hostile, or dismissive | The person is disrespectful overall |
| Sarcastic | `sarcasm_or_irony_candidate` | Context and delivery may make an utterance sound sarcastic or ironic | Sarcasm was deliberately intended |
| Vivid emotions | `audio_vividness_score` | Degree of salient, dynamic, expressive, forceful, relational, or pragmatically incongruent behavior over the selected audio | Internal emotion, moral character, or sentiment polarity |

Additional rules:

- Claims are multi-label; an event may be loud, forceful, and potentially disrespectful at once.
- `no qualifying evidence` means the model did not clear its review threshold. It does **not** prove the opposite.
- Poor audio, overlap, missing transcript, clipping, or poor alignment produce `not_assessable`, not a negative label.
- Internal states, personality, sincerity, credibility, health, gender, age, accent, party, and identity must not be inferred.
- Formal criticism, policy disagreement, quoting an insult, or naming misconduct are important hard negatives and are not automatically disrespectful.

---

## 3. Current workspace assessment

The current workspace is a useful corpus for a pilot, but it is not yet an application or a labeled evaluation dataset.

### 3.1 Assets found

- 21 MP3 recordings, all stereo, 44.1 or 48 kHz.
- Approximately 46.82 hours and 2.51 GiB of logical audio data.
- One matching base SRT, VTT, TXT, and `*.sentiment.srt` sidecar for every MP3.
- 26,769 base SRT cues.
- A diarization example in `audio/diarization/audio-cut-2min.json` with 11 segments from 40.232 s to 118.823 s and two anonymous speaker labels.
- FFmpeg/FFprobe 9.0.1 is installed.
- The machine is an Apple M3 Max with 128 GiB RAM; approximately 116 GiB was free at audit time.
- The system Python is 3.14, while much of the current ML audio stack is not yet reliably compatible with it.
- `audio/` is a symbolic link to media outside this workspace.
- There is no application code, dependency lockfile, test suite, or `.gitignore` in this workspace.
- A root-level `hf_key` file exists. It must not be read, copied, printed, or used as an implicit default credential.

### 3.2 Data-quality findings

- Every existing `*.sentiment.srt` cue is labelled `NEUTRAL` (26,769/26,769). This is a saturated/default-label artifact, not usable evidence that the corpus is neutral.
- Nine SRT files contain end timestamps beyond the corresponding MP3 duration; the largest observed drift is roughly 25 seconds.
- The diarization JSON is useful only as a schema/smoke-test fixture. It is not ground truth and has incomplete provenance.
- No video is currently present. Video containers may be accepted, but v0 should analyze their audio stream only.
- The filenames mix committee auditions, press conferences, and full public sessions; the source manifest must classify format/session type rather than treating all 21 files as one genre.
- Because recordings vary from about 31 to 385 minutes, raw event counts cannot be compared fairly across files.

### 3.3 Immediate consequences

1. Exclude the existing sentiment sidecars from training, calibration, and fusion unless their provenance and behavior can be independently explained.
2. Validate and clamp subtitle timestamps while preserving a warning; do not silently rewrite source files.
3. Use the base SRT as the primary transcript and run ASR only when it is absent or shown to be unreliable.
4. Use a Python 3.11/3.12 environment for the first implementation rather than the system Python 3.14.
5. Treat source media as read-only and write all artifacts into this workspace.
6. Do not process the full corpus until rights and privacy gates in Section 5 are approved.

---

## 4. Scope and non-goals

### 4.1 In scope for v1

- Local MP3, WAV, M4A, AAC, OGG, MP4, MOV, WebM, and other FFmpeg-readable inputs.
- Full-file or explicit time-range analysis.
- French-language default, with an explicit language override.
- Speech presence and speech/non-speech intervals.
- Existing SRT/VTT ingestion and optional ASR fallback.
- Anonymous, per-recording speaker diarization.
- Relative loud delivery and vocal-effort candidates.
- High-arousal/forceful-delivery candidates.
- Perceived anger-expression candidates.
- Potentially disrespectful-act candidates with subtype and quoted context.
- Sarcasm/irony candidates requiring conversational context.
- Event-level confidence, abstention, and evidence.
- Whole-file 0–10 vividness score, explicitly provisional until calibrated.
- UTF-8 text report plus JSON/JSONL artifacts.
- Batch processing with resume, cache, and deterministic manifests.
- Human review records that never overwrite raw model results.

### 4.2 Explicit non-goals

- Inferring a person's actual emotional state or intent.
- Inferring or publishing real-world identity from voice.
- Cross-file speaker recognition, a permanent speaker identity database, or a persistent cross-session person profile.
- Personality, mental-health, personality-trait, ideology, credibility, dishonesty, criminality, or misconduct-risk scoring.
- Lip-reading or detecting speech from silent video.
- Facial emotion recognition or facial blendshape “emotion” labels.
- Employment, education, disciplinary, moderation, law-enforcement, immigration, or election-influence use.
- Real-time intervention or automated action against a person.
- Publishing a raw model accusation about a named politician or candidate.
- Treating commercial cloud APIs as the default processor.

---

## 5. Legal, ethical, and source-rights gate

This section is a risk-control plan. Obtain advice from a French data-protection lawyer and the relevant organization's DPO/data steward before deployment or publication.

### 5.1 Source permission and copyright

The Sénat's current legal notice says that audio files on `audio.senat.fr` are the exclusive property of the Sénat, that reuse is subject to the Sénat's agreement, that commercial exploitation is prohibited, and that audio modification/montage is restricted. The exception for uncopyrighted parliamentary works must not be assumed to cover audiovisual recordings, subtitles, databases, performer rights, or third-party music. The licence for `data.senat.fr` also must not be assumed to license media hosted on the audio or video portals; verify every asset separately.

Before batch processing or publication:

1. Record the source URL, title, date, retrieval date, rights holder/producer, performers, transcript source, and rights status for every file.
2. Inventory senators, witnesses, staff, audience members, bystanders, third-party footage, music, and subtitles; a public official role gives a different privacy analysis from an incidental audience member.
3. Obtain written permission that expressly covers downloading/copying, temporary storage, segmentation/feature extraction, internal testing, aggregate publication, retention, and any model training or fine-tuning.
4. Do not assume that a text-and-data-mining exception is a blanket research or model-training licence; rights reservation, lawful access, performer rights, and database rights must be checked.
5. Do not upload the recordings to an external API merely because a service offers an endpoint.
6. Do not publish audio/video excerpts, edited clips, or identifiable event rankings without a separate content and rights review.
7. Keep quotations minimal and source-attributed in internal evidence files.

A `data/sources.csv` or equivalent source manifest should contain:

```text
source_id, local_filename, source_url, title, session_date, session_type,
retrieved_at, rights_holder, producer, participants,
third_party_content, rights_status, permission_reference,
permitted_uses, retention_rule, transcript_source, notes
```

Processing should be blocked for batch mode when `rights_status` or `permitted_uses` is unknown.

### 5.2 GDPR/RGPD

Voice, transcript, metadata, speaker turns, features, scores, and rankings can relate to identifiable people. Public availability does not remove GDPR obligations, and removing a name does not necessarily make a report anonymous. Parliamentary statements can reveal political opinions, an Article 9 special category. Emotion, loudness, or disrespect are not automatically Article 9 categories by themselves, but their combination with health, political, demographic, or conduct inferences may be.

Biometrics must be classified rather than assumed either way. Under GDPR Article 4(14), biometric data generally concerns processing that allows or confirms unique identification. Under the AI Act, the biometric-data definition is broader for emotion-recognition purposes. Diarization speaker embeddings and affect inference therefore need a documented technical/legal classification even though this plan forbids identity matching.

The project must first identify whether it acts as controller, joint controller, processor, or a separate provider/deployer. A processor or cloud vendor contract should cover documented instructions, approved subprocessors, security, deletion/return, no secondary use or model training, audit rights, incident notification, and transfers.

The implementation must support a documented:

- Article 6 legal basis;
- Article 9 condition where relevant, without assuming that “manifestly made public” covers a new emotional or sensitive inference;
- defined purpose and prohibited purposes;
- data minimization and feature selection;
- retention and deletion schedule;
- layered Article 14 notice or a documented alternative where individual notice is impossible/disproportionate;
- rights process for access, objection, correction, restriction, deletion, and complaint where applicable;
- processor and international-transfer controls;
- security measures; and
- DPIA/AIPD decision.

Consent should not be assumed merely because a person participated in a public hearing. “Scientific research” is not itself an Article 6 legal basis. GDPR Article 89 and CNIL's research guidance may provide safeguards and, where applicable, an Article 9 route, but the controller must document the complete analysis and may still need a separate Article 6 basis.

A DPIA is likely, because this processing can combine systematic observation, sensitive political content, a large audio collection, innovative technology, and evaluation/profiling of people. CNIL states that an AIPD is required for high-risk processing and lists evaluation, systematic monitoring, sensitive/highly personal data, large-scale collection, and innovative technology among its criteria. If residual risk remains high after mitigation, assess GDPR Article 36 prior consultation.

### 5.3 EU AI Act

The legal classification depends on both the technical signal and its purpose:

- measuring digital/relative loudness alone is normally an acoustic measurement;
- mapping a vocal signal to anger is likely emotion inference;
- an audio emotion classifier may be an AI Act emotion-recognition system when it uses biometric data;
- transcript-only sentiment is not the same technical processing, although GDPR, copyright, accuracy, and transparency still apply; and
- sarcasm may be treated as inferred intention or affect depending on the model and purpose.

A friendly UI name such as “vividness” does not change the classification. Plan for two modes:

- **R&D/pilot mode:** pre-market research, testing, or development may fall within AI Act scope exclusions, but “independent research” is not a universal exemption. GDPR, copyright, contract, and other law still apply. Document the exact intended purpose and do not market a finished emotion-recognition product during research.
- **Operational/deployment mode:** before putting the system into service, obtain a formal AI Act classification. Voice-based emotion recognition is listed in Annex III and is likely high-risk unless a documented exception applies; profiling can prevent use of the narrow Article 6(3) derogation. Article 50 transparency for emotion-recognition deployers applies from 2 August 2026 in the current timeline. Annex III high-risk obligations are currently scheduled from 2 December 2027 after the July 2026 AI Omnibus amendments.

Article 50 notice should identify the controller, retrospective AI-assisted analysis, signals/data used, outputs and limitations, model/version, human review, and correction contact. Where individual notice is impractical, counsel must document the Article 14 approach rather than treating public availability as an automatic exemption.

The system must explicitly prohibit workplace and educational emotion-recognition use. Article 5(1)(f) prohibits emotion inference in those settings except for medical or safety purposes. A public Senate hearing is not expressly listed, but a system used by the Senate on senators or staff in official work may still be connected to a workplace; obtain a written classification rather than relying on the research label. Do not use “disrespect,” “anger,” or “aggression” as a proxy for criminality, dishonesty, disloyalty, or a persistent social score.

### 5.4 Harm and editorial safeguards

- Default output uses anonymous speaker IDs and behavior-oriented wording.
- A high-severity event cannot be published without meaningful human confirmation by a reviewer trained in French, parliamentary context, uncertainty, and bias.
- Reports display the original audio timestamp, quoted context, evidence, confidence, model versions, and review status.
- Provide a correction, objection, right-of-reply, and takedown workflow; preserve both machine and human decisions.
- Distinguish official public conduct from witnesses, staff, audience members, bystanders, and incidental private conduct; public-office status is not a blanket privacy exemption.
- Prefer aggregate results and minimum cell sizes. Do not publish a rare score linked to a person, exact date, or commission when the output can single someone out.
- Treat French defamation, insult, personality/image, dignity, press, parliamentary, and presumption-of-innocence issues as publication-review gates; a disclaimer alone does not cure a defamatory presentation.
- Do not equate loudness with aggression, disagreement with disrespect, or a sarcastic-sounding phrase with malicious intent.
- Include a plain-language limitation statement in every report.
- Never use the score for discipline, employment, education, moderation, law enforcement, election/campaign strategy, or any finding of misconduct.

### 5.5 Security and confidentiality

- Use `HF_TOKEN` or another secret manager/environment variable. Never read `hf_key` automatically.
- Before any Git initialization, add `hf_key`, `.env*`, model caches, reports, annotations, and extracted audio to `.gitignore`.
- If the credential has ever been committed or shared, rotate/revoke it.
- Default to fully local inference and offline mode after model download; prefer EU/EEA hosting if a processor is unavoidable.
- Disable pyannote telemetry with `PYANNOTE_METRICS_ENABLED=0` where required.
- Contractually prohibit vendor training/secondary use, define retention/deletion, and assess subprocessors and international transfers.
- Use encryption in transit/at rest, MFA, least privilege, restrictive file permissions, segregated identity mappings, and access logging for a shared deployment.
- Do not place raw transcript fragments or credentials in application logs or general-purpose AI prompts.
- Keep anonymous speaker IDs recording-local; they are pseudonymization, not anonymization.
- Use a category-specific retention schedule: short pilot retention for raw/temporary media, deletion of extracted PCM after feature validation, restricted identity keys only during controlled review, and longer aggregate retention only when re-identification risk is negligible.
- Treat a model trained to infer facts about people as potentially personal data; deleting the raw dataset does not automatically make the model anonymous.
- Test for training-data memorization, extraction, inversion, membership inference, poisoning, and rare-person leakage where the threat model warrants it.
- Maintain an SBOM and model/data license/provenance manifest.

---

## 6. Functional requirements

| ID | Requirement | Acceptance condition |
|---|---|---|
| FR-01 | Required input path | `analyze` accepts one explicit audio/video path; it never guesses a single file from an ambiguous directory |
| FR-02 | Time range | Optional validated `--start` and `--end`; timestamps remain relative to the original media |
| FR-03 | Video support | FFmpeg extracts the selected audio stream; no visual emotion inference in v0 |
| FR-04 | Sidecar discovery | Find SRT first, then VTT; TXT is untimed fallback; provenance is recorded |
| FR-05 | Speech presence | Return yes/no/uncertain plus speech duration/ratio and quality evidence |
| FR-06 | Speaker turns | Optional anonymous diarization with overlap and confidence flags |
| FR-07 | Loud delivery | Return absolute digital level plus speaker/recording-relative level and percentile |
| FR-08 | Forceful/anger expression | Return candidate event scores, not an internal-state verdict |
| FR-09 | Disrespect | Return multi-label contextual candidates with target clause and context |
| FR-10 | Sarcasm | Require context; report low/medium confidence or `not_assessable` when context is inadequate |
| FR-11 | Vividness | Return integer 0–10, underlying estimate, status, confidence band, and later a prediction interval |
| FR-12 | Explainability | Every event stores evidence, model revisions, thresholds, and review state |
| FR-13 | Text ledger | Atomically regenerate a stable UTF-8 `reports/observations.txt` from canonical records |
| FR-14 | Machine output | Versioned JSON schema, JSONL evidence, quality report, and run manifest |
| FR-15 | Batch/resume | Deterministic ordering, cache, bounded memory, and restart without duplicate ledger entries |
| FR-16 | Human review | Confirm/reject/uncertain/annotate without overwriting machine output |
| FR-17 | Reproducibility | Same source hash, model revisions, config, and seed produce the same result |
| FR-18 | Abstention | Poor quality, overlap, clipping, and alignment failures suppress unsupported claims |

### 6.1 Non-functional requirements

- Local-only default; no telemetry and no runtime network access.
- Stream or chunk long recordings rather than loading a six-hour waveform.
- Preserve original amplitude for level analysis; use a separate normalized 16 kHz branch for ML models.
- Pin Python packages and model revisions in lockfiles/manifests.
- Bound disk use with caches under a configurable directory and an LRU/cleanup command.
- Never modify source media or sidecars.
- Schema changes require an explicit schema version.
- Logs must contain IDs and metrics, not transcript content.

---

## 7. Proposed command-line interface

```bash
# Analyze one explicit recording and update the text ledger
python -m senate_audio_audit analyze \
  "audio/video_...mp3" \
  --text-report reports/observations.txt

# Analyze a time range from a video container
python -m senate_audio_audit analyze \
  "video.mp4" \
  --start 00:12:00 --end 00:20:00 \
  --transcript auto

# Use an explicit transcript
python -m senate_audio_audit analyze \
  "audio/file.mp3" --transcript "captions/file.srt"

# Batch mode after rights/privacy gate approval
python -m senate_audio_audit batch "audio/*.mp3" --resume

# Inspect environment, models, media, and sidecars without inference
python -m senate_audio_audit doctor
python -m senate_audio_audit inspect "audio/file.mp3"

# Validate output/schema and rebuild the human-readable ledger
python -m senate_audio_audit validate reports/analyses/<run-id>
python -m senate_audio_audit rebuild-report

# Record a human decision without changing raw model output
python -m senate_audio_audit review \
  --run-id <run-id> --event-id <event-id> --decision uncertain \
  --note "Context is a quotation; sarcasm not confirmed"
```

### 7.1 Important options

- `--language fr`
- `--device auto|cpu|mps|cuda`
- `--offline`
- `--speakers auto|N`
- `--min-confidence`
- `--config configs/default.yaml`
- `--cache-dir`
- `--json-dir`
- `--events-dir`
- `--force` / `--resume`
- `--include-transcript-excerpt`
- `--no-diarization`

### 7.2 Process behavior

- Standard output: concise status and final score.
- Standard error: progress, warnings, and recoverable errors.
- Exit code `0`: completed.
- Exit code `2`: invalid input/config/schema.
- Exit code `3`: no assessable speech.
- Exit code `4`: low quality/partial result; artifacts still written.
- Exit code `5`: internal/model failure with resumable cache retained.

---

## 8. System architecture

```text
Source manifest + media discovery
              |
        FFprobe/media QC
              |
   checksum, duration, channel and
   transcript/sidecar validation
              |
     chunked FFmpeg decoding
        /             \
16 kHz model branch    original-level branch
   |                       |
Silero VAD              dBFS/LUFS/clipping
ASR fallback only       dynamics/channel quality
pyannote diarization         |
   \                         /
    timed SRT/VTT alignment
              |
  speaker turns + context windows
              |
 target-specific feature/model heads
 - speech/loud effort
 - arousal/force
 - anger expression
 - disrespect acts
 - sarcasm/irony
              |
 quality gates + late fusion + calibration
              |
 timestamped evidence events
              |
 duration-aware whole-audio aggregation
              |
 0–10 vividness estimate + uncertainty
              |
 JSON/JSONL + evidence SRT + text ledger
              |
       human review records
```

### 8.1 Two audio branches

Do not use one normalized waveform for all tasks.

**Level branch**

- Preserve source amplitude and channels.
- Do not apply global AGC, peak normalization, compressor, or aggressive denoising.
- Measure digital dBFS, short-term loudness, clipping, crest factor, dynamic range, and channel quality.
- Report that dBFS is not calibrated physical SPL.

**Model branch**

- Produce mono 16 kHz audio for Silero VAD, pyannote, wav2vec-style models, and most acoustic models.
- Normalize only where a model requires it.
- Retain the mapping back to original media timestamps.

### 8.2 Stereo policy

The current files are stereo, and channels should not be assumed to be exact duplicates.

1. Measure per-channel correlation, level, clipping, and VAD quality on a sample.
2. If channels are near-duplicates, downmix safely or select the better channel.
3. If channels are materially different, preserve both for QC.
4. For mono-required models, phase-align before mixing or select the channel with the best voiced quality.
5. If channel content differs, do not attribute all speech to a single anonymous speaker without an overlap/uncertainty flag.
6. Save the selected strategy in `run_manifest.json`.

### 8.3 Long-file processing

- Decode in overlapping chunks, for example 10 minutes with 2–5 seconds of overlap.
- De-duplicate samples at chunk boundaries.
- Cache by source SHA-256, model revision, and configuration hash.
- For diarization, use streaming where supported; if chunking is required, merge turns with global speaker clustering rather than concatenating local labels.
- Bound derived PCM/cache size; provide `cache clean` and `--keep-wav` controls.

---

## 9. Processing pipeline

### 9.1 Media intake and quality control

For each source:

- verify the path and extension;
- run `ffprobe` for duration, streams, codecs, sample rate, and channels;
- calculate a source checksum;
- reject empty, unreadable, or zero-duration media;
- detect clipping, long silence, severe noise, and gross level imbalance;
- report whether a video has a usable audio stream;
- preserve original files unchanged.

### 9.2 Transcript selection and validation

Priority order:

1. matching base SRT;
2. matching VTT;
3. explicit transcript supplied by the user;
4. ASR fallback;
5. untimed TXT as a last-resort reference only.

Validation must check:

- parseable timestamps;
- monotonically ordered cues;
- cue duration and overlap;
- cue end versus media duration;
- empty/malformed text;
- language and character encoding;
- SRT/VTT/TXT consistency when all are present.

For the nine known out-of-range SRTs, retain original timestamps in diagnostics, clamp only the analysis copy, and emit `transcript_drift_warning`.

The existing `*.sentiment.srt` files are loaded only as a diagnostic artifact and excluded unless provenance is approved.

### 9.3 Speech presence and VAD

Primary candidate: Silero VAD.

- Small, fast, CPU-friendly, and suitable for 8/16 kHz input.
- Store probabilities as well as hard speech intervals.
- Initial values may be threshold 0.5, 200–250 ms minimum speech, 100–250 ms minimum silence, and approximately 30 ms padding; tune on labeled French material.
- Compare against a second VAD during development to identify disagreement.

`SPEECH_PRESENT` may be `yes`, `no`, or `uncertain`. It should combine VAD duration, ASR/transcript agreement, and audio quality. Music, applause, and laughter can trigger VAD and require an audio-event quality gate.

### 9.4 Optional ASR fallback

Existing SRTs make ASR unnecessary for the first corpus pass. If a later test shows poor alignment or a transcript is missing:

- benchmark a local French-capable ASR with word timestamps;
- consider `faster-whisper` on CPU/CUDA and Whisper.cpp with Metal on Apple Silicon;
- use French as the default language;
- cache transcript and model revision;
- never silently replace a supplied official transcript.

### 9.5 Speaker diarization

Primary candidate: `pyannote/speaker-diarization-community-1`.

- Current, mature, and supports anonymous speaker turns and exclusive diarization for caption alignment.
- The code is MIT; model weights are CC-BY-4.0 and require acceptance of model conditions.
- Consume mono 16 kHz audio.
- Keep `SPEAKER_00`, `SPEAKER_01`, etc. local to one recording.
- Do not infer names or cross-file identities.
- Expose overlap and uncertain attribution.

Diarization is optional for the whole-file score. If its held-out diarization error rate is too high for reliable event attribution, disable speaker-specific claims by default while retaining file-level analysis.

### 9.6 Alignment and context

- Parse SRT/VTT into integer millisecond cues.
- Split long cues at punctuation/clause boundaries.
- Align clause spans to exclusive speaker turns by temporal overlap.
- Retain regular diarization output separately to mark overlap/interruption.
- For text semantics, include at least one preceding and one following turn, with an initial 15–30 second context target.
- If one cue spans several speakers, do not attribute the full text to every overlapping speaker.

### 9.7 Acoustic and interaction features

Initial interpretable feature set:

- active-speech RMS dBFS, short-term loudness, p50/p90/p95;
- peak, clipping fraction, crest factor, dynamic range;
- fundamental-frequency median/range/variability in semitones;
- energy variability, spectral tilt/centroid, high-frequency ratio;
- harmonic-to-noise proxy;
- speech rate, pause ratio, and phrase duration;
- turn-change rate, overlap fraction, and interruption candidates;
- music/applause/masking suspicion;
- speaker-relative z-score/percentile and recording-relative percentile.

For loud delivery, the primary normalization is:

```text
relative_level_db = window_median_dbfs - same_speaker_active_median_dbfs
```

A valid loudness event must be sustained for approximately 0.5–2 seconds and survive quality checks. The final threshold is learned from human labels, not fixed globally. If an anonymous speaker has too little active speech for a stable baseline, fall back to recording-relative features, lower confidence, and do not claim speaker-relative loudness.

### 9.8 Text and pragmatic features

Construct separate tasks rather than one generic sentiment score:

- direct insult or degrading label;
- ridicule/contempt;
- dismissal or personal invalidation;
- hostile command/threat;
- hostile interruption;
- identity-based attack, reported separately at higher severity;
- sarcasm/irony;
- quoted/hypothetical/formal criticism context;
- target span and directedness.

Use a French/multilingual encoder such as MDeBERTa or CamemBERT only after model/data licensing review. A small, heavily downloaded “French sarcasm” checkpoint with unknown training data is not acceptable production evidence, regardless of its claimed accuracy.

### 9.9 Optional video processing

Not in v0. If original video is later recovered:

- assess shot size, face visibility, and frame quality first;
- use visual cues only as supplementary evidence;
- do not treat face landmarks, head pose, or blendshapes as proof of internal emotion;
- require matched French annotations before adding a visual sarcasm head.

---

## 10. Model strategy

### 10.1 Target-specific heads

Build and evaluate these independently:

1. speech presence;
2. relative loud delivery;
3. high-arousal/forceful delivery;
4. perceived anger expression;
5. disrespectful-act subtypes;
6. sarcasm/irony;
7. interruption/overlap behavior; and
8. whole-recording audio vividness.

### 10.2 Gating rules

- Loudness/arousal require VAD-detected speech and enough voiced frames.
- Loudness uses the amplitude-preserving branch.
- `perceived_anger_expression` requires acoustic expressivity plus contextual support; volume alone is insufficient.
- Disrespect requires transcript evidence and context.
- Sarcasm requires text context and at least one additional acoustic or interaction cue for a high-confidence event.
- Long applause, masking, clipping, or poor transcript alignment produce `not_assessable`.
- Any inferred event must overlap speech/VAD activity unless the event is explicitly about a non-speech interaction.

### 10.3 Modeling progression

**Phase A — transparent baseline**

- robust acoustic features;
- lexicons and auditable rules;
- small logistic/ridge models;
- no claim of calibrated emotion recognition.

**Phase B — domain fine-tuning**

- fine-tune a French acoustic backbone only after enough balanced, speaker/session-diverse labels exist;
- fine-tune a French text encoder for the contextual disrespect taxonomy;
- train sarcasm only with contextual and preferably multimodal annotations;
- use class weighting and carefully selected hard negatives.

**Phase C — late fusion**

- combine target-specific predictions only after each branch is evaluated;
- use out-of-fold predictions for fusion to prevent leakage;
- prefer a monotonic ordinal/ridge model for vividness;
- reserve multimodal transformers for a much larger labeled corpus.

### 10.4 Candidate components

| Function | Preferred starting point | Notes |
|---|---|---|
| Media decode/loudness | FFmpeg/FFprobe, NumPy/SciPy/Librosa | Preserve source gain for level branch |
| VAD | Silero VAD | Fast local baseline; tune on French labels |
| Diarization | pyannote Community-1 | Gated model, local labels, disable telemetry |
| ASR fallback | faster-whisper or Whisper.cpp | Benchmark French alignment and Apple hardware |
| Acoustic features | librosa/SciPy + custom eGeMAPS-like subset | Avoid openSMILE in a commercial path without a separate license |
| Acoustic backbone | French-capable XLS-R/wav2vec family | Benchmark, then fine-tune only on domain labels |
| Text backbone | MDeBERTa or CamemBERT | Fine-tune on parliamentary context |
| Vividness | monotonic ordinal logit/probit or ridge | Interpretable; 21 recordings are too few for a black-box model |
| Storage | JSON/JSONL + optional Parquet | Canonical results independent of display format |

### 10.5 LLM policy

No cloud LLM and no LLM decision-maker in the first implementation. A local, constrained model may later help rank review items or draft neutral summaries, but it must never be the sole source of a claim, send data externally, or alter evidence timestamps without validation.

---

## 11. Event inference and fusion

### 11.1 Windows and event construction

- VAD/acoustic frames: 20–30 ms, aggregated into longer windows.
- Initial event windows: 5–10 seconds with 1–2 second hop.
- Semantic context: 15–30 seconds around the target clause.
- Merge overlapping detections of the same type when the gap is short.
- Require two consecutive high windows or approximately two seconds of evidence for an acoustic event, unless a clear contextual text act independently qualifies.
- Store integer `start_ms` and `end_ms`.

### 11.2 Fusion

Use late fusion and quality gates:

```text
assessable = speech_quality AND transcript_context_quality AND not_masked
candidate_score = calibrated_target_model(features, context, interaction)
publishable = candidate_score >= selected_threshold AND evidence_complete
```

The selected threshold is chosen on validation data to favor precision for rare, harmful labels such as clear disrespect and sarcasm. Raw softmax scores are not probabilities.

### 11.3 Decision states

Every claim uses one of:

- `supported_candidate` — model evidence clears the current review threshold;
- `uncertain_candidate` — borderline evidence retained for review;
- `no_qualifying_evidence` — no event clears the threshold;
- `not_assessable` — quality/context is inadequate;
- `human_confirmed` — reviewer confirms;
- `human_rejected` — reviewer rejects;
- `human_uncertain` — reviewer cannot decide.

---

## 12. Whole-audio vividness score (0–10)

### 12.1 Definition

**Audio vividness** is the degree to which a recording presents salient, dynamic, forceful, expressive, relational, and figuratively incongruent behavior over the selected file or time range.

It is:

- independent of positive versus negative valence;
- not raw loudness;
- not a count of “bad” moments;
- not an internal emotion or personality measure;
- not a probability.

A recording may be vivid because it is humorous, animated, emphatic, confrontational, sarcastic, or a mixture.

### 12.2 Score anchors

These are initial anchors to validate with raters:

| Score | Anchor |
|---:|---|
| N/A | No assessable speech; this is not numeric score 0 |
| 0 | Assessable speech is almost uniformly flat or non-expressive |
| 1–2 | Very low dynamic/expressive range |
| 3–4 | Ordinary formal speech with occasional emphasis |
| 5 | Clearly noticeable expressive or forceful passages |
| 6–7 | Recurrent salient vocal/interpersonal events |
| 8–9 | Pervasive, frequent, or sustained high-intensity behavior |
| 10 | Exceptional, near-continuous intensity across most of the recording; should be rare |

### 12.3 Provisional aggregation formula

Until whole-file human ratings exist, use a transparent provisional rubric.

For each valid event, let `x_j` be a 0–10 expressive-intensity estimate. Let `H` be the provisional high-event threshold (initially 6, to be validated), `T_speech` be active speech duration, `D_H` be high-event speech duration, and `L_H` be the longest high-event run.

```text
peak_component = (0.65 * max(x_j) + 0.35 * P90(x_j)) / 10
coverage_component = min(D_H / (0.15 * T_speech), 1)
persistence_component = min(L_H / 30_seconds, 1)

provisional_vividness =
  10 * (0.55 * peak_component
      + 0.25 * coverage_component
      + 0.20 * persistence_component)
```

Properties:

- A brief genuine high-intensity moment can produce a mid-range score.
- Sustained or pervasive intensity is required for 8–10.
- A long quiet recording does not score highly merely because it has many total minutes.
- Clipping, applause, and one-frame artifacts cannot create an event without the minimum-duration gate.
- The formula is replaced or recalibrated by a monotonic ordinal model after human whole-recording ratings.

### 12.4 Calibration status

Every score must include one of:

- `not_assessable` — no usable speech or insufficient quality;
- `provisional_rubric` — transparent formula, not human calibrated;
- `pilot_calibrated` — calibrated on a small, session-held-out set;
- `validated` — passed the predeclared held-out release criteria.

A validated result should also return:

- continuous expected rating before integer rounding;
- 80% or 90% prediction interval;
- confidence band;
- annotator agreement and calibration-set size;
- leave-one-component-out explanation.

---

## 13. Calibration and uncertainty

### 13.1 Event probabilities

For each target head:

1. reserve complete recordings for calibration/testing;
2. fit a Platt/sigmoid calibrator for small datasets;
3. use isotonic calibration only when there are enough examples;
4. report Brier score, log loss, reliability curve, PR-AUC, and abstention rate;
5. select the operational threshold from the precision/recall curve.

### 13.2 Confidence

Do not use raw model probability as the only confidence. A quality/uncertainty layer should account for:

- calibrated target score;
- VAD/audio quality;
- transcript and diarization alignment;
- overlap fraction;
- clipping/noise/masking;
- model/branch disagreement; and
- human uncertainty for the construct.

### 13.3 Score interval

After whole-file annotation, fit prediction intervals from out-of-fold residuals and annotator distributions. If a score is 6 but the 80% interval is 3–8, the report must show the uncertainty rather than presenting 6 as precise.

---

## 14. Human annotation and calibration program

The 21 full recordings are the independent units. Randomly splitting windows would leak recording, room, microphone, and speaker characteristics across train/test sets.

### 14.1 Annotation units

**Event windows**

- 6–12 second target plus 10–20 seconds of context on each side.
- Initial target: 300–500 windows, expanded toward 600+ after disagreement analysis.
- Include a random baseline sample to estimate prevalence and false positives.
- Add targeted positives and hard negatives after the first model, but keep the random sample for evaluation.

**Whole recordings**

- All 21 files rated 0–10 for vividness.
- At least three trained French-speaking raters; five is preferable for rare sarcasm/disrespect.
- Raters see the anchored rubric and may use `not_assessable`.
- Report median, spread, and inter-rater agreement.

### 14.2 Annotation labels

- `not_assessable`;
- speech present/absent;
- relative vocal effort: none, mild, clear, strong;
- high-arousal/forceful delivery: none, mild, clear, strong;
- perceived anger expression: none, possible, clear;
- disrespectful act: none, possible, clear, plus subtype;
- sarcasm/irony: none, uncertain, clear;
- literal/contextual interpretation;
- whether the language is quoted, hypothetical, formal criticism, or a direct interpersonal act;
- event vividness 0–10;
- whole-file vividness 0–10.

Raters must be blinded to model predictions and, where practical, to speaker identity. Disagreements are adjudicated without overwriting original ratings.

### 14.3 Hard-negative coverage

The annotation set must include:

- ordinary formal proceedings;
- robust but civil policy disagreement;
- strong non-hostile delivery;
- quoting or reporting insulting language;
- naming misconduct or proposing severe penalties;
- direct insults and hostile interruptions;
- laughter/applause/chants/music;
- remote, clipped, quiet, and overlapping speech;
- gain and channel differences;
- French accents and varied speaking styles;
- positive/animated, humorous, sarcastic, and literal uses of similar words.

### 14.4 Split strategy

- Keep every window from a source recording in one split.
- During development, use leave-one-session-out or grouped cross-validation.
- Reserve a locked final set of complete recordings, for example 4–5 files, touched only for release evaluation.
- Balance development batches by speaker/session, not raw event count.
- Never infer gender, age, or accent as production features. If fairness metadata is lawfully and voluntarily supplied, use it only for held-out evaluation.

### 14.5 Agreement targets

Predeclare:

- Krippendorff's alpha or weighted kappa for event labels;
- ICC or an ordinal agreement measure for whole-file scores;
- an initial target of at least 0.67 alpha, preferably 0.80 for publishable high-severity labels;
- a documented adjudication rule.

If agreement is low, revise the codebook and annotate again before training.

---

## 15. Evaluation strategy

### 15.1 Component metrics

| Area | Metrics |
|---|---|
| VAD | Frame precision/recall/F1, missed speech seconds, false-alarm seconds, collar sensitivity |
| Diarization | DER, JER, speaker-count accuracy, overlap analysis |
| ASR, if used | WER/CER, timestamp drift, proper-noun error rate |
| Loud delivery | Human-rating MAE/Spearman, monotonicity under gain changes |
| Arousal/force | MAE/CCC or macro-F1 depending on label type, PR-AUC |
| Disrespect | Per-type PR-AUC, macro-F1, precision at review threshold |
| Sarcasm | PR-AUC, F1, abstention accuracy, context-only vs multimodal gain |
| Vividness | MAE, within-one-point accuracy, QWK, Spearman, interval coverage |
| Annotation | Krippendorff/alpha, ICC/weighted kappa, rare-label agreement |

Accuracy alone is inappropriate for rare sarcasm, disrespect, and overlap events.

### 15.2 Provisional release gates

These are targets to agree before implementation, not promised results:

- no event outside assessable speech unless explicitly an interaction event;
- high-confidence evidence precision lower confidence bound at least 0.80 on held-out recordings;
- `pilot_calibrated` vividness MAE at or below 1.5 points;
- `validated` vividness MAE at or below 1.0 point with meaningful ordinal agreement;
- prediction intervals achieve approximately their nominal held-out coverage;
- all clear disrespect/sarcasm events are human-reviewed before publication;
- complete model, data, package, and license provenance is present for every run.

If a gate is not met, lower the claim level, expand abstention, or remain `provisional`; do not hide the failure.

### 15.3 Test layers

**Unit tests**

- SRT/VTT parsing and malformed timestamps;
- sentiment-prefix parsing kept separate from base transcript;
- interval union, clamping, and context expansion;
- dBFS, percentiles, and speaker normalization;
- stereo selection/downmix;
- scoring formula and rounding;
- schema and deterministic serialization.

**Synthetic fixtures**

- silence and zero-length audio;
- tone, pink noise, and speech-shaped noise;
- gain changes of ±3/±6 dB;
- clipping;
- two overlapping speakers;
- applause/masking-like noise;
- malformed/missing transcript;
- non-French speech;
- a long synthetic file to exercise chunk boundaries.

**Invariant tests**

- anger/disrespect/sarcasm scores remain substantially invariant to pure recording gain;
- relative loud delivery increases monotonically with gain;
- text-only claims do not change when audio is attenuated;
- diarization labels remain local to one recording;
- identical input/config/model revisions produce identical output.

**Real-corpus tests**

- the existing diarization JSON as a schema smoke test only;
- manually labeled VAD/diarization excerpts;
- one complete short recording end to end;
- all 21 recordings in resumable batch mode;
- bounded memory, restartability, and cache invalidation.

### 15.4 Bias and robustness review

Evaluate without production use of protected attributes:

- speaker/session and recording-condition slices;
- low/high digital level and clipping;
- overlap and remote microphone conditions;
- formal criticism versus direct interpersonal acts;
- positive/negative vividness, so the score is not simply hostility;
- French regional/accent variation where lawfully represented in annotations;
- model performance after gain, codec, and mild noise perturbations.

Document failure modes by group and event type. Do not report a favorable aggregate metric that hides a high false-positive rate for a subgroup or a severe event type.

---

## 16. Output design

### 16.1 Artifact layout

```text
reports/
  analyses/
    <run-id>/
      report.json
      quality.json
      run_manifest.json
      events.jsonl
      evidence.srt
      features.parquet        # optional diagnostic cache
  observations.txt            # generated, stable human-readable ledger
  reviews.jsonl               # append-only human decisions
  errors.jsonl
```

The canonical machine record is JSON/JSONL. `observations.txt` is generated from it so formatting changes do not lose data.

### 16.2 Text tracking semantics

- Group the text report by source file and time range.
- Include the latest analysis plus a compact run history.
- Use a stable `run_id`; do not append duplicate blocks on rerun.
- Preserve historical model runs in JSONL even when the latest text block is replaced.
- Write reports atomically with a lock so interrupted runs cannot corrupt the ledger.
- Keep human decisions in a separate append-only file.

### 16.3 Minimal JSON contract

```json
{
  "schema_version": "0.1.0",
  "run_id": "uuid",
  "created_at": "2026-09-24T12:00:00Z",
  "source": {
    "source_id": "senat-xxx",
    "filename": "recording.mp3",
    "sha256": "...",
    "duration_ms": 5343033,
    "rights_status": "approved_for_internal_pilot"
  },
  "scope": {
    "start_ms": 0,
    "end_ms": 5343033,
    "language": "fr"
  },
  "quality": {
    "overall": "good",
    "speech_ratio": 0.91,
    "transcript": "srt",
    "transcript_alignment": "good",
    "diarization": "medium",
    "warnings": []
  },
  "summary": {
    "speech_present": true,
    "vividness": {
      "score": 6,
      "expected_rating": 5.8,
      "status": "provisional_rubric",
      "confidence_band": "medium",
      "prediction_interval_80": null
    }
  },
  "events": [
    {
      "event_id": "evt-...",
      "claim_type": "high_arousal_forceful_delivery",
      "epistemic_status": "inferred_perception",
      "decision_state": "uncertain_candidate",
      "start_ms": 754000,
      "end_ms": 761500,
      "speaker_id": "SPEAKER_02",
      "score_semantics": "uncalibrated_signal_strength",
      "calibration_id": null,
      "signal_strengths": {
        "relative_loud_delivery": 0.82,
        "high_arousal_forceful": 0.74,
        "perceived_anger_expression": 0.61,
        "disrespectful_act": 0.22,
        "sarcasm_or_irony": null
      },
      "evidence": {
        "relative_level_db": 4.2,
        "speaker_level_percentile": 0.96,
        "overlap_fraction": 0.08,
        "context_before": "...",
        "target": "...",
        "context_after": "..."
      },
      "model_versions": {},
      "review": {
        "status": "unreviewed",
        "reviewer": null,
        "note": null
      }
    }
  ]
}
```

### 16.4 Human-readable report template

Until probability calibration exists, decimal values in a `provisional_rubric` report are labelled **uncalibrated signal strengths**, not probabilities or confidence percentages.

```text
================================================================================
FILE: recording.mp3
RUN: <run-id> | generated: <UTC timestamp>
SCOPE: 00:00:00.000–01:29:03.033
STATUS: provisional research output; not a determination of intent or character

SPEECH
  Present: yes
  Active speech: 81.2% (quality: good)

AUDIO VIVIDNESS
  Score: 6/10
  Status: provisional_rubric
  Confidence: medium
  Note: score is not a probability or moral rating

CANDIDATE EVENTS
  00:12:34.000–00:12:41.500 | SPEAKER_02
    High-arousal/forceful delivery: uncertain candidate (0.74)
    Relative loud delivery: supported candidate (0.82)
    Perceived anger expression: uncertain candidate (0.61)
    Potentially disrespectful act: no qualifying evidence (uncalibrated signal 0.22)
    Sarcasm/irony: not assessable from available context
    Evidence: +4.2 dB versus same-speaker median; overlap 0.08
    Context: “<minimal source excerpt>”
    Human review: pending

LIMITATIONS
  Local speaker IDs are not identities. No internal mental state is inferred.
  Review the original audio before quoting or publishing this event.
================================================================================
```

---

## 17. Proposed repository structure

```text
.
├── pyproject.toml
├── uv.lock
├── README.md
├── .gitignore
├── configs/
│   ├── default.yaml
│   └── pilot.yaml
├── data/
│   ├── sources.csv
│   ├── annotations/
│   └── splits/
├── docs/
│   ├── annotation-codebook.md
│   ├── output-schema.md
│   ├── model-card.md
│   ├── data-sheet.md
│   ├── privacy-impact-assessment.md
│   └── limitations.md
├── models/
│   └── manifest.yaml
├── reports/
│   └── .gitkeep
├── src/
│   └── senate_audio_audit/
│       ├── cli.py
│       ├── config.py
│       ├── schemas.py
│       ├── media.py
│       ├── transcripts.py
│       ├── vad.py
│       ├── diarization.py
│       ├── alignment.py
│       ├── channels.py
│       ├── features/
│       ├── text/
│       ├── fusion.py
│       ├── scoring.py
│       ├── calibration.py
│       ├── reporting.py
│       ├── review.py
│       └── cache.py
└── tests/
    ├── unit/
    ├── fixtures/
    ├── integration/
    └── golden/
```

Do not commit source audio, extracted PCM, credentials, model weights, personal reports, or annotation exports containing identifiable content.

---

## 18. Dependency and environment plan

### 18.1 Runtime

- Python 3.11 or 3.12 in an isolated environment, preferably managed by `uv`.
- FFmpeg/FFprobe pinned or version-checked.
- CPU fallback required even when MPS/CUDA is available.
- Exact package versions and model revisions recorded in the run manifest.

### 18.2 Core dependencies

- CLI/schema: Typer or argparse, Pydantic.
- Numerics/audio: NumPy, SciPy, SoundFile, Librosa.
- Transcript: `srt`, `webvtt-py`.
- ML: PyTorch, scikit-learn; Transformers only where required.
- Diarization: pyannote.audio.
- VAD: Silero VAD or ONNX Runtime.
- Training/calibration: scikit-learn, Statsmodels, optional LightGBM.
- Storage: JSONL and optional Parquet via PyArrow.
- Testing: pytest and Hypothesis.

Use optional dependency groups such as `cpu`, `diarization`, `training`, and `video`. Do not install the entire stack into the system Python.

### 18.3 Model manifest

For every model, record:

- model ID and immutable revision;
- source URL;
- license and acceptable use;
- training-data provenance when known;
- preprocessing and label ontology;
- local/cloud execution location;
- checksum;
- calibration ID and thresholds.

### 18.4 Secret handling

- Use `HF_TOKEN` from the environment or a secret manager.
- Add secret files to `.gitignore` before initializing version control.
- Never print environment values.
- Do not copy `hf_key` into `.env` automatically.
- If it has been exposed elsewhere, rotate it.

---

## 19. Phased implementation plan

### Phase 0 — Scope, rights, labels, and schema

**Estimated effort:** 3–5 working days, plus legal review.

Tasks:

- approve the observable/inferred terminology;
- complete source-rights manifest;
- make the GDPR/AI Act deployment decision;
- create the annotation codebook and vividness anchors;
- define JSON schema, source schema, model manifest, and limitation text;
- decide that identity mapping is out of scope;
- identify reviewers and annotation retention policy.

**Exit gate:** no ambiguous output such as “the person is angry”; source permission and pilot data scope are documented.

### Phase 1 — Secure scaffold and media/transcript foundation

**Estimated effort:** 3–5 days.

Tasks:

- create Python 3.12 project and lockfile;
- add `.gitignore` before any VCS use;
- implement CLI, config, schema, source discovery, and run IDs;
- implement `ffprobe` media QC and checksums;
- implement SRT/VTT/TXT parsing and drift checks;
- implement atomic JSON and run manifest;
- add unit tests and CI skeleton.

**Exit gate:** one file can be inspected and validated without running ML; no source file is modified.

### Phase 2 — Observable MVP

**Estimated effort:** 5–7 days.

Tasks:

- chunked decoding and dual audio branches;
- stereo QC/selection;
- Silero VAD and speech presence;
- amplitude-preserving level, clipping, and relative loudness features;
- provisional event windows and vividness rubric;
- text/JSONL/evidence-SRT reports;
- batch resume and bounded cache.

**Exit gate:** report speech presence and relative loud-delivery evidence only; every claim has evidence and quality status.

### Phase 3 — Diarization and interaction context

**Estimated effort:** 4–7 days plus annotation.

Tasks:

- integrate pyannote Community-1 locally;
- align SRT cues to exclusive speaker turns;
- preserve overlap/interruption regions;
- derive speaker-relative loudness and interaction features;
- add ASR fallback behind an optional profile;
- create manual VAD/diarization validation excerpts.

**Exit gate:** speaker-specific claims are enabled only if held-out diarization quality is acceptable.

### Phase 4 — Annotation pilot

**Estimated effort:** 1–2 weeks of engineering plus 2–4 weeks of rater work.

Tasks:

- build local review clips/waveforms and annotation sheets/UI;
- train raters with formal-criticism and quotation hard negatives;
- annotate 300–500 event windows and all 21 whole files;
- calculate agreement and adjudicate;
- freeze grouped/locked splits;
- publish codebook version and data sheet.

**Exit gate:** minimum agreement targets are met or the affected label remains uncalibrated.

### Phase 5 — Acoustic and contextual behavior models

**Estimated effort:** 2–4 weeks.

Tasks:

- train/compare vocal-effort and arousal models;
- fine-tune contextual text heads for disrespect;
- build sarcasm text baseline and abstention;
- add interaction evidence;
- select thresholds for high precision;
- create model cards and license records.

**Exit gate:** no unsupported “anger” label; clear-event precision and abstention meet the pilot target or are downgraded to candidates.

### Phase 6 — Fusion, calibration, and vividness

**Estimated effort:** 2–3 weeks.

Tasks:

- out-of-fold late fusion;
- Platt/isotonic calibration;
- quality/uncertainty model;
- fit monotonic whole-file vividness model;
- prediction intervals and component explanations;
- replace provisional formula only after validation.

**Exit gate:** `pilot_calibrated` or `validated` status is earned by held-out evidence, not development accuracy.

### Phase 7 — Full-corpus run and hardening

**Estimated effort:** 1–2 weeks.

Tasks:

- run all 21 recordings resumably;
- inspect quality distribution and false positives;
- test long files, cache invalidation, and interrupted jobs;
- complete privacy/security review;
- freeze first reproducible release and reports;
- document known failures and unsupported use cases.

**Exit gate:** all artifacts reproduce from the manifest; no unexplained missing outputs.

### Overall estimate

- Observable MVP: approximately 2–3 engineering weeks.
- Usable, human-reviewed candidate system: approximately 6–9 engineering weeks.
- Annotation and legal review run in parallel and are likely the critical path.
- Twenty-one independent recordings support a pilot, not a strong general claim; expand the corpus before describing the system as validated across French parliamentary settings.

---

## 20. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Internal emotion is unknowable from recordings | False accusations and scientific overclaim | Report observable/perceived behavior only; human review; explicit limitations |
| Current sentiment SRT is all neutral | Model appears to find nothing | Exclude artifact; investigate provenance; use human labels |
| Political-speech content is sensitive | RGPD/Article 9 and reputational harm | DPIA, legal basis, minimization, local processing, restricted reports |
| Hearing may be a workplace context | Prohibited or high-risk AI Act use | Written AI Act classification; prohibit HR, discipline, training, and staff monitoring uses |
| Model or vendor creates a secondary profile | Persistent re-identification or political targeting | No cross-file identity, no secondary training/use, model privacy tests, aggregate-only pilot output |
| Source recording rights are restricted | Copyright/reuse claims | Source manifest, permission gate, no publication without review |
| Microphone gain/distance differs | “Loud” flags are inconsistent | Preserve gain, report digital level, normalize per speaker, use gain-invariance tests |
| Stereo channels differ | Downmix cancellation or lost speech | Channel QC, conditional phase-align/select, record strategy |
| Long files favor raw event counts | Longer recordings score higher | Duration-aware prevalence, persistence, and saturating recurrence |
| Transcript drift/ASR errors | Wrong semantic evidence | Validate SRT, retain context, fallback ASR, abstention |
| Diarization/overlap errors | Wrong speaker attribution | Anonymous labels, exclusive turns, overlap flags, DER gate |
| Disrespect is contextual and normative | Harsh policy language falsely flagged | Parliamentary codebook, multi-label taxonomy, contextual encoder, hard negatives |
| Sarcasm is pragmatic and low-base-rate | High false-positive rate | Context mandatory, auxiliary cue for high confidence, abstention, PR-AUC |
| Generic emotion checkpoints are weak/mislabeled | Misleading scores | Treat as benchmarks only; custom domain calibration |
| Model probabilities are overconfident | Harmful false positives | Held-out calibration, reliability curves, precision-first thresholds |
| Few independent recordings | Overfit vividness model | Grouped/locked splits, simple ordinal model, expand corpus |
| One artifact creates score 10 | Misleading aggregate | Minimum duration, robust peak, quality gates, rare-score policy |
| Secret/model telemetry leakage | Data/security incident | Environment secrets, `.gitignore`, offline mode, disable telemetry |
| Hardware/package incompatibility | Slow or unstable setup | Python 3.12, lockfile, CPU fallback, benchmark on one file first |
| Public-facing misuse | Stigma or political harm | No identity, no adverse decisions, editorial review, correction workflow |

---

## 21. Decisions required before implementation

| Decision | Recommended default | Consequence if changed |
|---|---|---|
| Intended use | Private, local research pilot only | Publication/deployment requires fuller legal and editorial review |
| Source permission | Confirm and record before batch processing | Unknown rights should block processing |
| Participant scope | Analyze only rights-cleared recordings; aggregate non-speaking/bystander material | Excluding or separately reviewing audience/staff/witness material changes the corpus and legal basis |
| Model training | Disabled until written source permission covers it | Training/fine-tuning creates additional reproduction, database, and personal-data risk |
| Speaker identity | Never infer or publish | Identity mapping adds biometric and fairness risk |
| Meaning of “disrespect” | Direct interpersonal insult/contempt/dismissal/hostility; ordinary criticism excluded | Different policy changes labels and model training |
| Sarcasm | Context mandatory; high confidence requires an additional cue | Audio-only sarcasm would be too error-prone |
| No speech | Score `N/A`, not 0 | Numeric 0 means assessable but flat speech |
| Report language | English field/report with original French evidence, optional French locale | Translation must not alter quoted evidence |
| Human review | Required for public-facing clear events | Unreviewed output remains internal/pilot only |
| Retention | Defined by controller policy; raw audio remains at source; temporary PCM deleted promptly | Longer retention increases risk |
| Video | Audio-only in initial versions | Visual emotion/sarcasm adds substantial bias and validation work |
| Batch | Enable only after a single-file end-to-end gate | Batch-first work risks systematic QC failures |

---

## 22. Definition of done for v0 implementation

The implementation is complete for its declared pilot scope when:

- a single explicit audio/video path can be analyzed reproducibly;
- speech presence is reported with quality evidence;
- base SRT/VTT is validated and timestamp drift is visible;
- relative loud delivery is separated from absolute digital level;
- candidate events distinguish acoustic force, perceived anger expression, contextual disrespect, and sarcasm;
- no output claims an internal state or real-world identity;
- the vividness score is 0–10 or explicitly `not_assessable`, with status and confidence;
- a text ledger and canonical JSON/JSONL artifacts are written atomically;
- every event includes timestamps, context, evidence, model versions, and review status;
- all current `NEUTRAL` sentiment sidecars are excluded;
- unit, invariant, malformed-input, and one-file integration tests pass;
- source media and sidecars remain unchanged;
- the run is local/offline by default and does not use `hf_key` implicitly;
- source permission, privacy review, model licenses, and human-review workflow are documented;
- held-out results are reported honestly, including abstention and failure cases.

---

## 23. Authoritative and technical references

Accessed 2026-09-24 unless otherwise noted.

### Legal and institutional

- Sénat, legal notice and audio/video reuse terms: https://www.senat.fr/mentions-legales.html
- Sénat Archives, conditions of use: https://archives.senat.fr/debuter-pas-a-pas/conditions-dutilisation.html
- data.senat.fr licence (verify scope asset by asset): https://data.senat.fr/licence/
- CNIL, GDPR text and articles: https://www.cnil.fr/fr/reglement-europeen-protection-donnees
- CNIL, AIPD guidance: https://www.cnil.fr/fr/ce-quil-faut-savoir-sur-lanalyse-dimpact-relative-la-protection-des-donnees-aipd
- CNIL, research guidance outside health: https://www.cnil.fr/fr/recherche-scientifique-hors-sante
- CNIL, AI practical sheets: https://www.cnil.fr/fr/les-fiches-pratiques-ia
- CNIL, legitimate interest for AI development: https://www.cnil.fr/fr/base-legale-interet-legitime-developpement-systeme
- CNIL, *On the Record* white paper on voice and personal data: https://www.cnil.fr/sites/cnil/files/atoms/files/cnil_white-paper-on_the_record.pdf
- EUR-Lex, AI Act consolidated text as of 2026-07-27: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02024R1689-20260727
- EUR-Lex, Regulation (EU) 2026/1744 (Digital Omnibus on AI): https://eur-lex.europa.eu/eli/reg/2026/1744/oj
- EU AI Act Service Desk, implementation timeline: https://ai-act-service-desk.ec.europa.eu/en/ai-act/timeline/timeline-implementation-eu-ai-act
- European Commission, current AI Act overview and timeline: https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai
- EUR-Lex, GDPR: https://eur-lex.europa.eu/eli/reg/2016/679/oj
- EDPB, biometrics and voice guidance: https://www.edpb.europa.eu/topics/ai-and-technology/biometrics_en
- EDPB Opinion 28/2024 on personal-data aspects of AI models: https://www.edpb.europa.eu/our-work-tools/our-documents/opinion-board-art-64/opinion-282024-certain-data-protection-aspects_en
- CNIL, guidance on annotating data for AI: https://www.cnil.fr/fr/ia-annoter-les-donnees
- Légifrance, Code de la propriété intellectuelle: https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000006069414

### Core technical components

- FFmpeg filters, including `ebur128`: https://ffmpeg.org/ffmpeg-filters.html#ebur128
- Silero VAD: https://github.com/snakers4/silero-vad
- pyannote.audio: https://github.com/pyannote/pyannote-audio
- pyannote Community-1 model card and license: https://huggingface.co/pyannote/speaker-diarization-community-1
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Librosa: https://librosa.org/doc/latest/index.html
- openSMILE (license caution: research/education, not open source for commercial products): https://github.com/audeering/opensmile
- scikit-learn probability calibration: https://scikit-learn.org/stable/modules/calibration.html
- Statsmodels ordered model: https://www.statsmodels.org/stable/generated/statsmodels.miscmodels.ordinal_model.OrderedModel.html

### Reference/model candidates requiring separate review

- French CommonVoice ASR: https://huggingface.co/speechbrain/asr-wav2vec2-commonvoice-fr
- French XLS-R backbone: https://huggingface.co/facebook/wav2vec2-large-xlsr-53-french
- MDeBERTa V3: https://huggingface.co/microsoft/mdeberta-v3-base
- TransCasm French sarcasm corpus: https://aclanthology.org/2022.politicalnlp-1.14/
- MUStARD multimodal sarcasm corpus: https://github.com/soujanyaporia/MUStARD

Any model or dataset adopted in implementation must have its exact revision, license, training provenance, intended-use limitations, and compatibility recorded in the model/data manifest.

---

## 24. Recommended immediate next step

Before coding the behavior models:

1. confirm the intended use and source permission;
2. approve the observable-language policy in Section 2;
3. create a 20–30 minute annotation pilot containing formal criticism, a strong exchange, a direct insult, a possible sarcasm case, and ordinary speech;
4. test the existing SRT and pyannote alignment on that pilot;
5. freeze the v0 output schema and codebook; then
6. implement Phases 1–2 and run one complete short recording end to end.

This sequence produces useful, auditable results quickly while avoiding the much more expensive mistake of training or publishing uncalibrated “emotion” labels.
