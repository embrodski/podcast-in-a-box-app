---
name: lighthaven-podcast-in-a-box
description: >-
  Walk-in podcast pipeline: scan MultiCorder dumps in E:\PodcastRoom, label
  Host/Guest/Wide video and Host/Guest audio, then run conversation-sync,
  Combined-as-Clean (DeRoom placeholder), video-sync, ElevenLabs transcribe,
  podcast autocut 1-min approval, and full interview render. If Host/Guest audio
  sounds swapped in the edit, run piab_fix_audio_speaker_swap.py (speaker-ID remap
  + 1-min re-render only; never swap Raw audio unless Raw was mislabeled). Use when the user
  says "Lighthaven Podcast In A Box", "podcast in a box", "PIAB", or wants a
  stripped-down interview-only edit from fresh MultiCorder files.
---

# Lighthaven Podcast In A Box

Cursor-guided skill (no GUI). Reuses harness Interview tools; skips reading, intro, stitch, hand-edit, and real DeRoom.

**State file:** `<working_folder>/podcast-in-a-box.json`  
**Default scan root (MultiCorder dumps):** `E:\PodcastRoom`  
**Default work root (sessions, logs, JSON):** `E:\PodcastRoom\PodcastInABox\Sessions`

## Recording flow vs autocut flow

| Phase | Steps | Purpose |
|-------|-------|---------|
| **Recording flow** | −1, 0–0e | Already recorded?, vMix, preset, camera, MultiCorder, continue or stop |
| **Autocut flow** | 1–6 + resume | Scan, label, prep, 1-min approval, full render, delivery |

**New session:** `python scripts/piab_start_session.py` → option **1** asks whether the podcast is **already recorded** or will be **recorded now**. Already recorded → autocut flow (default vs special folder). Record now → recording flow, then **Continue to Autocut or Stop**.

**Resume autocut only:** option **2** skips recording flow entirely.

**Non-interactive:** `--already-recorded` / `--skip-recording-flow` for autocut-only; `--record-now` + `--continue-to-autocut` or `--stop-after-recording` after recording.

## Hard rules

- **No overwrite** without listing paths and getting explicit approval; then pass `--allow-overwrite`.
- **No long renders** (>~30s) without user confirmation (prep chain and full render both count).
- After rename/move: give **Estimate A** (Fast Preview), wait for OK before preview prep.
- **All sessions use Fast Preview.** Do not build full-length `Input/` prepped media before 1-min approval. If max video is &lt; 5 minutes, use the last 60 seconds for the preview (same as missing Start Phrase) and reuse preview prepped files after approval.
- After 1-min approval: enqueue full prep+render (no Estimate B / F3). Do not start a second Full job while one is running.
- **Use 5-minute completion checks for long jobs.** After launching prep or full render in the background: confirm it started once and report the estimate. Do **not** busy-wait with sub-minute polling. While the job is running, **check status about every 5 minutes** until completion or failure, then notify the user immediately (short progress notes on intermediate checks are fine). This is agent monitoring, not part of the render pipeline or future standalone app. See project **`AGENTS.md`**.

**Immediate failure alerts:** `piab_run_prep.py` and `piab_run_full_render.py` write **`Temp/harness-FAILURE.json`** and show a **Windows toast** (with sound) when a step fails (e.g. ElevenLabs billing). If you launched prep in the background, also check for that marker file or a non-zero exit — do not wait for the next 5-minute poll to report failures.

### Revealing folders / files to the user

Whenever you ask the user to look at a folder (previews, Raw, Output, etc.) or open a deliverable folder:

1. **Open it in File Explorer** with:
   ```powershell
   explorer.exe "<absolute Windows path>"
   ```
2. In the chat message, always give **both**:
   - Plain full path for copy-paste: `E:\PodcastRoom\PodcastInABox\Sessions\<name>\Temp\piab-previews`
   - Optional markdown link: `[Open folder](file:///E:/PodcastRoom/PodcastInABox/Sessions/<name>/Temp/piab-previews)`  
     (use forward slashes in the `file:///` URL)
3. Do **not** rely on clickable `file://` links alone — Cursor’s chat webview often fails them silently.

## Standard Raw names (after labeling)

| Role | Filename |
|------|----------|
| Host video | `Host Raw Video.mp4` |
| Guest video | `Guest Raw Video.mp4` |
| Wide video | `Wide Raw Video.mp4` |
| Host audio | `Host Raw Audio.wav` |
| Guest audio | `Guest Raw Audio.wav` |

MultiCorder sources (top-level only under scan root):

- Video: `MultiCorder[n] - DeckLink Quad HDMI Recorder ... .MP4`
- Audio: `MultiCorder[n] - Output [m] ... .WAV`

---

## Recording flow

### Step −1 — Already recorded or record now?

First question for a **new session** (option 1):

> Have you already recorded this podcast, or will you be recording now?

| Answer | Next |
|--------|------|
| **Already recorded** | Skip to **autocut flow** — Step 1 asks default folder vs special folder |
| **Record now** | Continue with Steps 0–0d, then Step 0e |

### Step 0 — vMix must be running

Before recording, ensure **vMix** is open on the podcast machine:

```powershell
python scripts/piab_ensure_vmix.py
```

Or start the interactive launcher (runs this check first):

```powershell
python scripts/piab_start_session.py
```

Behavior:

1. If **vMix is already running**, continue immediately.
2. If not, print **`Opening vMix`** and launch from `C:\Program Files (x86)\vMix\` (`vMix64.exe`, then `vMix.exe`).
3. If vMix still is not running after ~30 seconds, print **`Please open vMix`** and open the help image: `assets/piab-vmix-icon-help.png` (shows the vMix taskbar icon).

Use `--skip-vmix` only for automation/CI.

### Step 0b — Open the PIAB vMix preset

After vMix is running, load **`4 People - 5 Cameras - Default.vmix`** from `E:\PodcastRoom\vMix Configs\`:

```powershell
python scripts/piab_open_vmix_preset.py
```

`piab_start_session.py` runs Steps 0–0d for **new sessions** (option 1). Resume (option 2) skips recording flow.

Behavior:

1. Find `4 People - 5 Cameras - Default.vmix` under `E:\PodcastRoom\vMix Configs\` (also accepts the spaced filename `Default .vmix`).
2. If that preset is already loaded, continue immediately.
3. Otherwise print **`Opening vMix preset: …`** and call the vMix HTTP API (`OpenPreset`) on `http://127.0.0.1:8088/api/`.
4. Wait until vMix reports the preset path in its API XML.

Use `--skip-vmix-preset` only for automation/CI, or `--preset-path` to override the file location.

### Step 0c — Camera and microphone setup

After the preset is loaded, confirm camera framing and mic levels:

```powershell
python scripts/piab_confirm_camera_setup.py
```

`piab_start_session.py` runs this automatically after Step 0b.

Behavior:

1. Print the setup instructions (cameras off-center toward center, eyes at top viewfinder line, mic ~80%).
2. Open all three reference images at once:
   - `assets/piab-camera-left.jpg`
   - `assets/piab-camera-right.jpg`
   - `assets/piab-camera-wide.jpg`
3. Wait for the user to confirm ready (today: type **`ready`** in the Cursor terminal; future standalone app: **`PIAB_USE_CONTINUE_BUTTON=1`** + Continue button — see `PIAB_USE_CONTINUE_BUTTON` in `scripts/piab_confirm_camera_setup.py`).

Use `--skip-camera-setup` or `--confirm-camera-ready` for automation/CI.

### Step 0d — MultiCorder recording session

After camera setup, start **MultiCorder** and wait while the hosts record:

```powershell
python scripts/piab_multicorder_record.py
```

`piab_start_session.py` runs this automatically after Step 0c.

Behavior:

1. If MultiCorder is **not** recording, call vMix API `StartMultiCorder`.
2. If MultiCorder is **already** recording, ask: continue current recording, or stop and restart (`StopMultiCorder`, wait 2s, `StartMultiCorder`).
3. Print the running-program instructions with **Start Phrase** and **Ending Phrase** from `podcast-phrase-gates.json`.
4. Wait for the user to type **`continue`** (future standalone app: **`PIAB_USE_CONTINUE_BUTTON=1`** + Continue button).
5. Call vMix API `StopMultiCorder` when the user continues.

Use `--skip-multicorder`, `--auto-continue-recording`, or `--already-recording continue|restart` for automation/CI.

### Step 0e — Continue to autocut or stop

After MultiCorder stops, the **newest** files in `E:\PodcastRoom` are the session sources. The launcher asks:

> Continue to Autocut, or Stop? (you can use the files yourself now, or resume autocut later)

| Answer | Next |
|--------|------|
| **Continue to autocut** | Autocut flow with the newest default-folder cluster (no default vs special prompt) |
| **Stop** | Exit; files remain in `E:\PodcastRoom`. Start autocut later via option **1** → already recorded, option **2** resume, or Step 1 scripts |

Use `--continue-to-autocut` or `--stop-after-recording` for non-interactive runs.

---

## Autocut flow

### Step 1 — Where are the source files?

**Email delivery (optional):** When starting a **new** session (not resume), ask whether to email `Full Interview.mp4` when the full render completes. If yes, collect the recipient email, read it back, and confirm with **“Is this correct? [Y/N or A to abort]”**. Abort = continue without delivery. On **resume**, do not re-prompt if `podcast-in-a-box.json` already has a confirmed delivery email for this session.

Interactive launcher (`piab_start_session.py`) handles this after init. Agent-driven starts should call the same flow or use:

```powershell
python scripts/piab_init_session.py --working-folder "<folder>" --delivery-email "user@example.com" --confirm-delivery-email
```

Secrets: copy `delivery-config.example.env` and set Frame.io + Gmail SMTP env vars before full render delivery runs. For Gmail, run:

```powershell
python scripts/harness_setup_smtp.py
python scripts/harness_smtp_test.py --to "you@gmail.com"
```

This writes a gitignored repo-root `.env` loaded automatically by `piab_run_full_render.py`.

**Frame.io (Native App OAuth):** on Windows, register the Adobe `adobe+://` redirect handler once, then browser login and ID discovery:

```powershell
python scripts/harness_frameio_oauth.py register-protocol
python scripts/harness_frameio_oauth.py login
python scripts/harness_frameio_discover.py --write-env
```

After Adobe sign-in, allow the browser prompt to **Open** the PIAB OAuth handler. App Builder projects often do not expose Redirect URI editing; this path uses the existing Native App credential without changing Adobe Console.

Tokens live in `.frameio-oauth.json` (gitignored) and refresh automatically.

**After recording flow (Step 0e → continue):** scan the default folder and confirm the newest cluster.

**Already recorded (Step −1) or autocut-only:** ask the user:

> Are the MultiCorder files in the **default folder** (`E:\PodcastRoom`), or in a **special folder** you already created (e.g. `E:\Bayeswatch\Jessiah`)?

### Default folder

Sources sit at the top level of `E:\PodcastRoom`. PIAB creates a **new working subfolder** under `E:\PodcastRoom\PodcastInABox\Sessions`.

```powershell
Set-Location "<repo>"
python scripts/piab_scan_session.py --root "E:\PodcastRoom"
```

Show the user: file list, typical mtime, typical duration, counts, and any **`requirements.missing`** lines. Ask: **Are these the files from this session?** and **What should the working folder be named?**

On confirm:

```powershell
python scripts/piab_init_session.py --name "<UserChosenName>" --root "E:\PodcastRoom\PodcastInABox\Sessions" --scan-root "E:\PodcastRoom"
```

Creates `E:\PodcastRoom\PodcastInABox\Sessions\<UserChosenName>\` with `Raw`, `Input`, `Output`, `Temp`, and `podcast-in-a-box.json`. Init scans **`E:\PodcastRoom`** (not the empty new subfolder).

### Special folder (sources already in place)

The user gives a **single folder path** that **is** the working folder and already contains the MultiCorder files (often alongside future `Raw` / `Output` subfolders).

```powershell
python scripts/piab_scan_session.py --root "<SpecialFolderPath>" --strict
```

If **`requirements.ok`** is false, **stop and tell the user** what is missing (need ≥3 camera MP4s and ≥2 Output WAVs). Do not init until the folder is complete or the user explicitly accepts incomplete files.

On confirm:

```powershell
python scripts/piab_init_session.py --working-folder "<SpecialFolderPath>"
```

Init scans **that folder only**. Do **not** pass `--root` + `--name` for special folders.

### Interactive launcher (optional)

For a terminal-only flow without the agent:

```powershell
python scripts/piab_start_session.py
```

Option **1**: Step −1 (already recorded vs record now) → recording and/or autocut init. Option **2**: resume autocut only. Flags: `--already-recorded`, `--continue-to-autocut`, `--stop-after-recording`.

---

### Step 2 — Label videos

```powershell
python scripts/piab_extract_video_previews.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>"
```

The script names the previews `Camera 1.jpg`, `Camera 2.jpg`, etc. Before asking
for labels, **open the preview folder** (`explorer.exe`) and give the plain path
plus optional `file:///` link (see **Revealing folders** above).

For each preview JPG: **Read the image**, identify it by the matching `Camera X`
filename, and ask the user to label **Host**, **Guest**, **Wide**, or
**do not use**.

Rules: exactly one Host, one Guest, one Wide; all others do not use.

Show a confirmation table. Offer **Accept**, **Re-label**, or **Swap Host ↔ Guest** (before move).

---

### Step 3 — Label audio

```powershell
python scripts/piab_extract_audio_previews.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>"
```

The script names the previews `Mic 1 A.wav`, `Mic 1 B.wav`, etc. (two **5 s**
clips from different parts of each track). Before asking for labels, **open the
preview folder** (`explorer.exe`) and give the plain path plus optional
`file:///` link (see **Revealing folders** above).

Silent or empty Output WAVs are skipped automatically (not shown for labeling).

Play each preview clip and label **Host**, **Guest**, or **do not use** per
**mic** (both A and B clips for a mic share the same source file).

Rules: exactly one Host, one Guest; rest do not use. Confirm / re-label / swap Host ↔ Guest before applying.

---

### Step 4 — Apply labels + Estimate A

Build JSON maps of absolute source path → role, then:

```powershell
python scripts/piab_apply_labels.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --video-labels-json "<json>" --audio-labels-json "<json>"
```

Script copies files into `Raw` (sources stay in place) and prints **Estimate A** (prep through 1-min test).

**Open `Raw`** (`explorer.exe`), give plain path + optional link, tell the user
the estimate, then **wait for confirmation** before prep.

### Swap / re-label after move

```powershell
# Swap Host/Guest files in Raw (clears downstream prep state)
python scripts/piab_swap.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --files video   # or audio | both

# If wrong files were labeled and sources still exist: re-run extract + apply
# (may need --allow-overwrite after user approval)
```

---

### Step 5 — Prep through 1-min test

**Fast Preview** (max labeled **video** duration **> 10 min**): 300s head clips under `Preview Files/` → fast prep → F2/F2a → full prep + render (no second 1-min review). CLI: `piab_create_preview_clips.py`, `piab_run_fast_preview.py`, `piab_approve_fast_preview.py`, `piab_run_full_after_preview.py`. See **`docs/piab-fast-preview.md`**.

Long job — only after Estimate A approval:

```powershell
python scripts/piab_run_prep.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>"
```

To **resume** after a failure (e.g. ElevenLabs billing) without redoing video-sync:

```powershell
python scripts/piab_resume_prep.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>"
python scripts/piab_run_prep.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --resume
```

`--resume` skips steps whose outputs already exist on disk (conversation-sync → clean audio → prepped Input files → transcript → 1 Min Test). Optional `--from-step transcribe` (aliases: `video_sync`, `one_min`, `06`–`10`) forces a start point. On failure, `Temp/harness-FAILURE.json` records the step; the next `--resume` starts there.

Interactive launcher option **2 = resume existing session** runs the same path.

Does: conversation-sync → copy Combined→Clean (**DeRoom placeholder**) → video-sync → `elevenlabs_transcribe_wav.py` → podcast autocut **1 Min Test.mp4**.

Optional phrase gates live in **`podcast-phrase-gates.json`** at the repo root (created automatically with walk-in defaults). Start/end/pause are **always attempted**; if a phrase is missing from the transcript, that gate is skipped. Override the file with `piab_set_phrase_gates.py` or per-episode fields on `podcast-in-a-box.json`.

Default gates (editable in `podcast-phrase-gates.json`):
- **Start:** `I solemnly swear I'm up to no good, in five four three two` / preroll 1.0s — `in` before the countdown is optional; countdown numbers may be skipped; optional trailing `one` / `zero` are removed when spoken; skipped if not in transcript
- **End:** `Be excellent to each other and party on dudes` (alternate: `Hut of brown, now sit down`) / postroll 1.0s — latest match among end phrases wins; skipped if none match
- **Pause:** `Computer Freeze Program.`
- **Unpause:** `Computer Resume Program` / `Computer Unfreeze Program`
- **Abort:** `Emergency override - Eject the warp core`
- Start speaker → Host camera (`speaker_0`)

Then **open `Output`** (`explorer.exe`), give plain path + optional link, and tell
the user:

> 1 Min Test is ready for review: `E:\PodcastRoom\PodcastInABox\Sessions\<name>\Output\1 Min Test.mp4`

When **`Temp/failed-sync-confidence.json`** exists, prep renders **two** tests instead:

- **`1 Min Test no offset.mp4`** — start-aligned sync (no detected offset applied)
- **`1 Min Test forced audio offset.mp4`** — detected lags forced on

**Stop at step 10a** (`resume_at`: **`10a_sync_offset_approval`**). The GUI shows **F2a**
(side-by-side players). User picks which sounds better, then confirms the chosen test on **F2**.

```powershell
python scripts/piab_record_sync_offset_choice.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --choice start_aligned
python scripts/piab_record_sync_offset_choice.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --choice forced_offset
```

See **`docs/av-sync-confidence-fallback.md`** in the upstream PIAB repo.

**Stop and wait.**

### 1-min approval loop

**Host/Guest audio swapped in the edit (default interpretation):** When the user says
Host and Guest **audio** are swapped, reversed, or on the wrong mic **in the cut** —
or similar phrasing — assume **Raw and Input files are labeled correctly**. Do **not**
swap Raw audio or re-run prep. Run:

```powershell
python scripts/piab_fix_audio_speaker_swap.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --allow-overwrite
```

In the **GUI app**, F2 **Host/Guest swapped in edit** calls the same backend.

That toggles transcript speaker-id mapping, re-converts the existing detail JSON with
`--swap-speaker-ids`, regenerates `interview.dsl`, and re-renders **`1 Min Test.mp4`**
only (~minutes). Unchanged on disk: `Host Video-prepped.mp4`, `Guest Video-prepped.mp4`,
`Wide Video-prepped.mp4`, `Host Clean Audio-prepped.wav`, and the detail transcript JSON.

| User intent | Action |
|-------------|--------|
| Looks good | Go to Estimate B |
| Host/Guest **audio swapped / reversed / wrong mic in the edit** | **`piab_fix_audio_speaker_swap.py --allow-overwrite`** (after overwrite approval) or GUI F2 |
| Host/Guest **cameras** feel swapped (same speaker-ID fix) | Same as audio swapped |
| Wrong **Raw** Host/Guest files (mislabeled during Step 2–3) | `piab_swap.py --files video` and/or `--files audio`, then re-run **full prep** (`piab_run_prep.py --allow-overwrite` after approval) |
| Other fixes | Adjust and re-run 1-min only when possible |

---

### Step 6 — Estimate B + full render

```powershell
python scripts/piab_estimate.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --which full --mark-awaiting
```

Show the estimate. **Wait for confirmation.** Then:

```powershell
python scripts/piab_run_full_render.py "E:\PodcastRoom\PodcastInABox\Sessions\<name>" --allow-overwrite
```

If delivery was enabled at session start, this also uploads `Full Interview.mp4` to Frame.io, creates a **public** no-expiration share, emails the `short_url` to the confirmed recipient, and writes:

- `Output/Full Interview.delivery.json` — `file_id`, `share_id`, `short_url`, recipient
- `Output/Full Interview Transcript.json` — copy of the main transcript
- `Temp/delivery-summary.json` — machine-readable summary

Delivery failure does **not** fail the render; the user gets a failure email with the **local file path**. Validate config without uploading: `--delivery-dry-run`.

When done, **open `Output`** (`explorer.exe`), give plain path + optional link, and
say exactly:

> Full render is complete: `E:\PodcastRoom\PodcastInABox\Sessions\<name>\Output\Full Interview.mp4`

Filename: **`Full Interview.mp4`** under the session **Output** folder. Stop.

---

## Resume

Read `podcast-in-a-box.json` → `resume_at` and `steps`.

| `resume_at` | Next action |
|-------------|-------------|
| `03_label_videos` | Video previews / labels |
| `04_label_audio` | Audio previews / labels |
| `05_estimate_prep` | Show Estimate A; on OK run prep |
| `06_conversation_sync` … `10_one_min_test` | Run `piab_run_prep.py --resume` (or `piab_start_session.py` → resume) |
| `10a_sync_offset_approval` | A/B sync offset choice (when confidence failed) → GUI **F2a** |
| `11_one_min_approval` | Review 1 Min Test → GUI **F2** |
| `12_estimate_full` | Show Estimate B; on OK full render |
| `14_done` | Finished |

---

## Out of scope (v1)

- GUI
- Real DeRoom (placeholder only)
- Reading / intro / stitch / hand-edit
- 5-minute test gate
