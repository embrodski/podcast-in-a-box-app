# Fast Preview — design and implementation plan

Speed up the path to the 1-minute approval gate for long interviews by running prep on
300-second head clips in an isolated sandbox, recording user choices, then running full
prep + full render on `Raw/` without a second 1-minute review.

**Applies when:** every labeled session. Full-length `Input/` files are not created until after 1-minute approval.

**Short sources:** if max labeled video is **&lt; 5 minutes**, the 1-minute review uses the **last 60 seconds** (same as missing Start Phrase). After approval, preview prepped files are **promoted** to canonical `Input/` (no second sync/transcribe).

---

## Goals

| Goal | Detail |
|------|--------|
| Faster approval | User sees sync / Host-Guest / camera check in minutes, not proportional to interview length |
| Same decisions | Sync offset A/B, speaker-ID swap, and approval recorded and applied on full-length run |
| Isolated artifacts | Preview outputs never overwrite canonical `Input/`, `Temp/`, `Output/` |
| No second 1-min review | After Fast Preview approval, full prep skips step 10 and chains into full render |

---

## Directory layout (option B — full sandbox)

All preview artifacts live under `<session>/Preview Files/`:

```
Preview Files/
  Preview Host Raw Video.mp4          # 300s stream-copy from Raw/
  Preview Guest Raw Video.mp4
  Preview Wide Raw Video.mp4
  Preview Host Raw Audio.wav
  Preview Guest Raw Audio.wav
  Input/
    Preview Host Video-prepped.mp4    # after video-sync + multicam
    Preview Guest Video-prepped.mp4
    Preview Wide Video-prepped.mp4
    Preview Host Clean Audio-prepped.wav
    …
  Temp/
    interview.dsl
    interview_transcript_simplified.json
    segments.json
    …
  Output/
    Preview 1 Min Test.mp4            # or A/B pair (see below)
    Preview 1 Min Test no offset.mp4
    Preview 1 Min Test forced audio offset.mp4
```

Session-level folders (`Raw/`, `Input/`, `Temp/`, `Output/`) are **untouched** until full prep after approval.

**Naming rule:** Every file created in the preview pass is prefixed with `Preview` (including combined/clean audio aliases under the sandbox).

---

## Phase 1 — Create preview sources

**Trigger:** After labeling completes (D4), for every session.

**Script:** `scripts/piab_create_preview_clips.py` (new)

For each of the five labeled files in `Raw/`:

```bash
ffmpeg -y -t 300 -i "<Raw/Host Raw Video.mp4>" -c copy "Preview Files/Preview Host Raw Video.mp4"
```

- Fixed **300s** for all sources (video + audio).
- Stream copy (`-c copy`); map default video/audio streams.
- Idempotent; honor overwrite guard unless `--allow-overwrite`.
- Record paths + clip duration in session state under `fast_preview`.

**Threshold:** Use **max** of the three **video** file durations (not median of all five).

---

## Phase 2 — Preview prep (steps 06–10 in sandbox)

**Script:** `scripts/piab_run_fast_preview.py` (new) or `piab_run_prep.py --fast-preview`

Parameterize prep to read/write via preview paths:

| Normal | Fast Preview |
|--------|----------------|
| `paths.raw` → `Raw/` | `paths.preview_root` → `Preview Files/` (raw clips at root) |
| `Input/` | `Preview Files/Input/` |
| `Temp/` | `Preview Files/Temp/` |
| `Output/` | `Preview Files/Output/` |

Reuse existing steps:

1. **06** conversation-sync on preview WAVs → `Preview … Combined Audio.wav` in sandbox
2. **07** Combined → Clean (DeRoom placeholder)
3. **08** video-sync + multicam → `Preview Files/Input/*-prepped.mp4`
4. **09** ElevenLabs transcribe on preview prepped WAV
5. **10** DSL + 1-min render (see below)

**Sync confidence / F2a (required):** Use the same low-confidence rules as full prep
(`maybe_write_sync_confidence_flag`, `run_sync_ab_one_min_tests`). If triggered:

- Render **both** under `Preview Files/Output/`:
  - `Preview 1 Min Test no offset.mp4`
  - `Preview 1 Min Test forced audio offset.mp4`
- Set `resume_at` → `10a_sync_offset_approval` (F2a)
- Do **not** skip A/B because the source is only 5 minutes.

**Forced-offset preview prep:** When A/B runs, build forced-offset preview prepped media in the sandbox (same as today’s `ensure_forced_offset_prep`, scoped to preview paths). Do not create full-length forced prep yet.

**Estimate:** Show **Estimate A-fast** on D4 (wall-clock for ~5 min sources, not full interview length).

---

## Phase 3 — 1-minute preview render logic

### Normal (head) mode

1. Generate `interview.dsl` from preview transcript with phrase gates.
2. Render with `max_seconds=60` (first ~60s of **output timeline** after start phrase handling).

### When to fall back to tail mode

Switch to **tail autocut** if **any** of:

- Start phrase **not found** in preview transcript
- Start phrase timestamp **> 4:00** (240s) — leaving &lt; 60s after start cut in a 300s source
- Head autocut output would be **&lt; 60s**

### Tail mode behavior

- Window = last **60 seconds** of prepped preview timeline (wall-clock on prepped MP4/WAV)
- Slice transcript to that window and **rebase times to 0**; set segment audio/video offsets to the window start so the tail is a normal 60s source
- Run **`generate_full_dsl.py`** with normal Host/Guest/Wide cutting rules
- **Skip** start/end/pause phrase gates (conversation sample only)
- Render up to 60s of that DSL

Record in state:

```json
"fast_preview_approval": {
  "preview_render_mode": "head_autocut" | "tail_autocut",
  …
}
```

---

## Phase 4 — User review (F2a / F2)

### F2a — sync offset (unchanged logic, preview paths)

Side-by-side players load from `Preview Files/Output/`. Choice saved into **`fast_preview_approval.sync_offset_choice`** (`start_aligned` | `forced_offset`), not canonical `sync_offset_choice` until full prep applies it.

### F2 — simplified Host/Guest fix

| Action | Behavior |
|--------|----------|
| **Looks good** | Persist approval bundle → brief confirmation → auto-start full prep + render |
| **Host/Guest swapped** | Toggle `swap_speaker_ids`, regenerate preview DSL, re-render preview 1-min only (no video-sync, no Raw changes). Update `fast_preview_approval.swap_speaker_ids`. |
| **Re-label cameras/mics** | Link to D1 with clear copy: *this takes a long time; use Host/Guest swap if only speaker mapping is wrong.* Clears preview sandbox; after re-label, regenerate 300s clips and re-run preview prep. |

**Remove** F2 flow that swaps Raw files and skips speaker-ID remap.

### Auto-continue after approval (answer 2-B)

On **Looks good**:

1. Show brief confirmation: *“Starting full processing…”*
2. Navigate to **E1** (full prep + render job)
3. **Skip F3** Estimate B screen and extra click
4. Chain **full prep → full render** in one job (answer 6)

---

## Phase 5 — `fast_preview_approval` state (isolated from preview artifacts)

Stored on `podcast-in-a-box.json`; applied once at start of full prep:

```json
{
  "fast_preview": {
    "enabled": true,
    "threshold_sec": 600,
    "max_video_duration_sec": 3723.5,
    "preview_root": "…/Preview Files",
    "clips_created_at": "…"
  },
  "fast_preview_approval": {
    "approved_at": "…",
    "sync_offset_choice": "start_aligned" | "forced_offset" | null,
    "swap_speaker_ids": false,
    "preview_render_mode": "head_autocut" | "tail_autocut",
    "sync_ab_required": true,
    "preview_one_min_path": "…/Preview Files/Output/Preview 1 Min Test.mp4"
  }
}
```

Preview prep may populate transient keys (`main_prepped`, etc.) under preview scope; **canonical** session `Input/` and `main_prepped` are written only during full prep. Full prep **reads** `fast_preview_approval` and **writes** canonical fields.

---

## Phase 6 — Full prep + render (after approval)

**Script:** extend `piab_run_prep.py` with `--apply-fast-preview-approval` (or dedicated `piab_run_full_after_preview.py`)

### Full prep (steps 06–09 on full `Raw/`)

- Standard paths: `Raw/` → `Input/`, `Temp/`, `Output/`
- Apply `fast_preview_approval.swap_speaker_ids` before transcribe/DSL
- Apply `fast_preview_approval.sync_offset_choice`:
  - **`start_aligned`:** normal video-sync → `Input/*-prepped.mp4`
  - **`forced_offset`:** **only** forced-offset full-length sync + multicam → canonical `Input/*-prepped.mp4` (skip start-aligned full encode entirely)
- **Skip step 10** — no `1 Min Test.mp4` for user review
- Mark `11_one_min_approval` completed from recorded approval (same as today after F2)

### Full render (chained, no pause)

- `piab_run_full_render.py` using full-length transcript + DSL
- Uses `active_main_prepped()` from applied sync choice
- Delivery, flag report, email unchanged

### Resume after failure (answer 10)

- Keep `fast_preview_approval` intact
- Resume **full prep only** (or render-only if prep completed)
- Do **not** require repeating Fast Preview

---

## Phase 7 — GUI / controller / resume

| Screen | Change |
|--------|--------|
| **D4** | If max video &gt; 10 min: show Estimate A-fast + explain Fast Preview; else existing Estimate A |
| **E1** | Phase 1: “Fast preview processing…” → F2a or F2; Phase 2: “Full processing…” after approval |
| **F2a / F2** | Resolve paths under `Preview Files/Output/`; F2 re-label link + copy |
| **F3** | Skipped when Fast Preview approval auto-continues |
| **Resume router** | New states: `fast_preview_in_progress`, `fast_preview_approval`, `full_prep_after_preview` |

**Relabel (D1/D2):** After apply labels, if Fast Preview enabled, run clip builder + preview prep again.

---

## Phase 8 — Script / module changes (summary)

| Area | Work |
|------|------|
| `piab_create_preview_clips.py` | New — 300s extracts |
| `piab_run_fast_preview.py` | New — sandbox prep through step 10 |
| `piab_run_prep.py` | Preview path params; full prep apply approval; skip step 10; forced-only branch |
| `piab_run_full_render.py` | Chained from prep job; no 1-min gate |
| `harness_video_sync.py` / `harness_av_sync_lib.py` | Optional `paths` override; preview-scoped forced prep |
| `app/controller/` | Fast preview job, approval persistence, path resolvers |
| `app/gui/views/` | D4, E1, F2, F2a updates |
| `app/gui/views/review_screens.py` | F2 speaker-swap only + re-label link |
| `piab_resume.py` | Preview vs full resume plans |
| `docs/piab-app-architecture.md` | Screen flow update |
| `.cursor/skills/lighthaven-podcast-in-a-box/SKILL.md` | Agent parity |

---

## Phase 9 — Testing

- Preview clip builder: duration ≈ 300s, naming, overwrite guard
- Threshold: 10 min gate on max video duration
- Tail fallback triggers (phrase at 4:15, missing phrase, short output)
- Tail mode: DSL generated; gates skipped
- Low sync confidence on preview → two preview MP4s + F2a
- `fast_preview_approval` apply: forced-only full prep skips start-aligned encode
- F2: speaker swap only; no Raw swap
- Resume: full prep failure retains approval
- Controller + resume router screen IDs

---

## Implementation order

1. State schema + `piab_create_preview_clips.py` + tests  
2. Path-parameterized prep core (preview roots)  
3. `piab_run_fast_preview.py` + tail fallback + sync A/B on preview  
4. `fast_preview_approval` persist/apply + full prep skip step 10 + forced-only branch  
5. Chain full render; GUI D4/E1/F2/F2a; F2 simplification + re-label link  
6. Resume router + skill/docs updates  
7. End-to-end manual test on a &gt;10 min session  

---

## Explicit non-goals (v1)

- Re-checking sync confidence on full-length and re-prompting (see `docs/piab-future-review.md`)
- Fast Preview for sessions ≤ 10 min
- Second 1-minute review after full prep
- Physical Raw file swap from F2

---

## Open items

None — spec locked pending implementation clearance.
