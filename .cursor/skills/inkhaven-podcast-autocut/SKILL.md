---
name: inkhaven-podcast-autocut
description: Automates the Inkhaven multi-cam podcast workflow: convert a detail transcript JSON into per-sentence simplified JSON (in Temp), register the interview segment in Temp/segments.json (key main; do not edit config.py), generate interview.dsl in Temp with speaker-based camera cuts plus the dense-cuts→wide rule (default --camera-switch-offset-ms -250 on generate_full_dsl.py; user may say "adjust off" or "Adjust X ms"), write MP4 renders to Output, then render ONLY the 1-minute test MP4 and pause to ask whether to continue (5-minute + full render are opt-in). If the user's initial request includes "massive" or `--massive`, after the agreed full-episode render add `--massive` to `python -m podcast_dsl` so the same folder also gets Ben Render, Guest Render, and Wide Render (single-camera variants, encoded sequentially: Ben, then Guest, then Wide). Default to no color correction; enable it only if the user's initial request explicitly says "Use Color Correct", "Run Color Correct", or similar. Use when the user says “Inkhaven”, “podcast autocut”, “generate DSL”, “render interview”, or provides input/output folders with Ben/Guest/Wide videos + WAV + transcript JSON.
---

# Inkhaven Podcast Autocut

## Working folder resolution

Resolve episode folders **before** listing files or building command paths. Let **`P`** be the absolute path the user gave. Let **`WF_INPUT`** be true iff **`P`**'s last path segment equals **`Input`** (case-insensitive).

- **If `WF_INPUT` is true**: let **`R = parent(P)`**. If **`R / Output`** exists as a directory **and** **`R`** has a sibling **`temp`** directory (case-insensitive; e.g. `Temp` or `temp` on Windows), treat **`R`** as the **working folder** (same as if the user had passed the parent):
  - **Input folder** = **`P`** (the path they passed; normally **`R / Input`**)
  - **Output folder** = **`R / Output`**
  - **Temp folder** = that existing **`R / temp`** sibling (do not create a second temp folder under **`P`**)
- **Else**: **working folder** = **`P`**, with the usual layout:
  - **Input folder** = **`P / Input`**
  - **Output folder** = **`P / Output`**
  - **Temp folder** = existing **`P / temp`** sibling if present, else **`P / Temp`** (case-insensitive; create if missing when needed)

If **`parent(P)`** is not meaningful (e.g. drive root), do not promote; use **`P`** as the working folder.

When promotion applies, say so once (e.g. “Using `E:\Inkhaven Viv` as the working folder because you passed `...\Input`”). Use the resolved **Input**, **Output**, and **Temp** paths in every step below—not **`P / Input`** when **`P`** was already the Input folder.

In all commands below, substitute **`<input folder>`**, **`<output folder>`**, and **`<temp folder>`** with those resolved absolute paths.

## Inputs to collect

- **Working folder path** (after [Working folder resolution](#working-folder-resolution))
  - Input folder: resolved **Input folder** (source media + detail transcript live here)
  - Output folder: resolved **Output folder** (**MP4 renders only**)
  - Temp folder: resolved **Temp folder** (non-MP4 pipeline artifacts **and** render scratch; on Windows redirect `TEMP`/`TMP` here before rendering)
- **Files**:
  - **Ben close video** (`speaker_0`)
  - **Guest close video** (`speaker_1`)
  - **Wide video** (`wide`)
  - **Master audio** (WAV preferred)
  - **Detail transcript** JSON (must have top-level `segments` array with `start_time`/`end_time` and ideally `words`)
  - Optional: **simplified transcript HTML** (reference only; not required)
- **Filename variations**: any alternate spellings/case/spaces.
- **Offsets** (optional): per-camera offsets in seconds (default 0 for all).
- **Temp folder free space**: if `<temp folder>` is on a cramped drive, choose a different working folder (rendering writes large temp files; keep it off a nearly-full C: drive).
- **Color correction request**: treat color correction as **off by default**. Only enable it if the user's initial run request explicitly says `Use Color Correct`, `Run Color Correct`, or equivalent phrasing.
- **Encoder preference**: by default, let `podcast_dsl` use a working hardware H.264 encoder if available; otherwise it should fall back to `libx264`. Only override this if the user explicitly asks for a specific encoder.
- **Downscale request**: if the user's initial run request says `Downscale 4K to 1080p` or equivalent phrasing, add `--downscale-4k-to-1080p` to the render command.
- **Camera switch timing adjust**: when running **`generate_full_dsl.py`**, apply a camera-switch offset unless the user opts out:
  - **Default:** **-250 ms** earlier switches (`generate_full_dsl.py` default; no flag required).
  - **Opt out:** if the initial request includes **`adjust off`** (case-insensitive; e.g. `Adjust off`, `no adjust`), add **`--no-camera-switch-offset`** to `generate_full_dsl.py` (or `--camera-switch-offset-ms 0`).
  - **Override amount:** if the initial request includes **`Adjust <N> ms`** (or `Adjust <N>ms`, case-insensitive; e.g. `Adjust -200 ms`), use `--camera-switch-offset-ms <N>` instead of the default (parse `<N>` as a signed integer milliseconds value).
  - Current implementation supports **negative values only** (switch earlier). Positive values intentionally error out.
- **Start / End phrases** (always on via `podcast-phrase-gates.json` at repo root): `--start-phrase` drops everything through that phrase; cut begins **1s before** the first word after it (`--start-preroll-sec`, default 1.0). When `start_phrase_countdown_tokens` is configured, only the prefix before the countdown is required exactly (with **I am** / **I'm** equivalence); a trailing **`in`** before the countdown is optional; spoken countdown numbers may be skipped in order, and optional **`one`** / **`zero`** suffix words are included in the cut when present. `--end-phrase` is repeatable; the **latest** match among all configured end phrases wins; cut ends **1s after** the last word before it (`--end-postroll-sec`, default 1.0). If no phrase is **found** in the word-timed transcript, that gate is skipped (full start/end kept). Matching is case/punctuation-insensitive.
  - **Host from start phrase:** when start phrase matches, that speaker is Host for camera mapping (`speaker_0` = Host close cam).
  - Override defaults: edit `podcast-phrase-gates.json` or `python scripts/piab_set_phrase_gates.py --set start_phrase=...`
  - Per-episode override: optional fields on episode/PIAB state still win over the file.
- **Pause / Unpause / Abort** (same file): applied **after** Start/End inside the remaining span. `--abort-phrase` anywhere in the full transcript disables Pause/Unpause.
- **Massive renders**: if the user's initial run request includes `massive`, `--massive`, or equivalent (e.g. “run massive”, “massive test”), then when they agree to the **full episode** render, append **`--massive`** to that `python -m podcast_dsl` command (same flags otherwise). That produces **`Ben Render.mp4`**, **`Guest Render.mp4`**, and **`Wide Render.mp4`** in **`<output folder>`** (same folder as `-o`), plus matching **`.dsl`** siblings in **`<temp folder>`** (same directory as `interview.dsl`), each the same timeline as `interview.dsl` but forced to `speaker_0`, `speaker_1`, or `wide` respectively; `massive_renderer.py` runs those three encodes **one after another** (Ben, then Guest, then Wide) to avoid overloading the machine. Do **not** add `--massive` to 1-minute or 5-minute test commands (`--max-seconds` is incompatible with `--massive`).

### Artifact layout (Output vs Temp)

Keep **`<output folder>`** for deliverable **`.mp4`** files only. Write **all other** pipeline outputs under **`<temp folder>`** (the `temp` / `Temp` sibling one level up from Input/Output):

| Artifact | Folder |
|----------|--------|
| `interview_transcript_simplified.json` | `<temp folder>` |
| `segments.json` | `<temp folder>` |
| `interview.dsl` | `<temp folder>` |
| Massive variant `.dsl` files (`Ben Render.dsl`, etc.) | `<temp folder>` |
| `1 Min Test.mp4`, `5 Min Test.mp4`, `Full Interview.mp4`, massive `.mp4` renders | `<output folder>` |

Ensure **`<temp folder>`** exists before writing JSON/DSL. Do **not** place transcript JSON or DSL under Output.

## Core rules (must apply)

### Speaker mapping

- **Speaker 0 = Ben = `speaker_0`**
- **Speaker 1 = Guest = `speaker_1`**
- Wide camera is `wide`

**ElevenLabs diarization is not Ben/Guest-aware.** Word-level `speaker_0` / `speaker_1` in the detail JSON are arbitrary cluster IDs that can differ per WAV. `convert_transcript_json.py` maps `speaker_N` → integer `N`; `generate_full_dsl.py` maps integer `0` → Ben’s camera and `1` → Guest’s **by default**.

**Exception:** when `--start-phrase` is set, `generate_full_dsl.py` identifies the speaker who said that phrase as Host and maps that transcript `speaker_id` → `speaker_0` (Host/Ben camera), with the other close-mic speaker → `speaker_1`. Manual **`--swap-speaker-ids`** on convert is only needed when there is no start phrase (or the user still wants to force a swap).

### Open and close on Ben (`generate_full_dsl.py`)

These apply when generating **`interview.dsl`** (before rendering):

- **First five seconds:** the timeline interval **[0s, 5s)** stays on **`speaker_0` (Ben)**. Every transcript row whose time range **overlaps** `[0, 5)` is forced to Ben, so there is **no cut off Ben** during that window (sentence-row aligned; a long row that crosses 5s is one clip, so Ben may extend past 5s for that row).
- **Last four seconds:** the timeline from **`T − 4s` through `T`** stays on **`speaker_0`**, where **`T`** is the end of the last transcript row plus the generator’s **final-shot tail** (same `--final-shot-tail-sec` as `generate_full_dsl.py`, default 2s). Every transcript row that **overlaps** that tail window is forced to Ben. If there is no row boundary exactly at `T − 4s`, the first row that intersects the tail window is forced to Ben for its whole clip (Ben may start slightly before `T − 4s`).
- **CLI:** `--open-ben-sec` and `--tail-ben-sec` default to **3** and **4**; set to **0** to disable either lock.

Forced-wide spans from the dense-cut rule are **trimmed** so **`!camera wide`** never covers the open-Ben or tail-Ben windows.

### Dense cuts → force wide

Treat a **cut** as a **camera change** only (`speaker_0 ↔ speaker_1`), not sentence boundaries.

Whenever there would be **more than one cut in any rolling window** (default **3 seconds**, see `generate_full_dsl.py` `--cut-window-sec`), switch to a single **`!camera wide`** for that period:

- **Sentence-aligned** start/end (only at sentence boundaries)
- **Wide lasts at least** `--min-wide-sec` (default **3 seconds**)
- **Return** to the intended camera for the **first sentence after** the wide span
- **Extension exception**: if another cut would happen within the **same window** of the wide span ending, extend the wide span until that cut boundary (repeat until no such cut exists)

## Workflow

### 1) List the input files

Confirm the exact filenames in `<input folder>` and identify which map to:
`speaker_0`, `speaker_1`, `wide`, `audio`, `detail transcript`.

### 2) Convert detail transcript → simplified per-sentence JSON

Run the repo converter to create a simplified transcript JSON in `<temp folder>`.

- **Default**: split into one row per sentence from word timings (critical for sentence-boundary edits).
- Output format must be a JSON dict keyed by row id strings; each row has `start`, `end`, `text`, `speaker_id`, `speaker_name`.

Command template:

```bash
python convert_transcript_json.py "<input folder>/<detail transcript filename>" -o "<temp folder>/interview_transcript_simplified.json"
```

Only if the user explicitly requests a Host/Guest camera swap: re-run step 2 with **`--swap-speaker-ids`**, then repeat steps 4–5 (regenerate DSL and re-render the 1-minute test).

### 3) Register the interview segment in `<temp folder>/segments.json`

Write **one** segment entry with key **`main`** (do **not** edit `src/podcast_dsl/config.py`).

1. Save the segment object to a scratch file, e.g. `<temp folder>/segment_main.entry.json`:

```json
{
  "audio_file": "<absolute path to master audio>",
  "audio_offset": 0,
  "enable_color_match": false,
  "video_files": {
    "speaker_0": { "file": "<Ben/Host prepped MP4>", "offset": 0 },
    "speaker_1": { "file": "<Guest prepped MP4>", "offset": 0 },
    "wide": { "file": "<Wide prepped MP4>", "offset": 0 }
  },
  "transcript_file": "<temp folder>/interview_transcript_simplified.json"
}
```

Set `enable_color_match` to `true` only when the user explicitly requested color correction.

2. Upsert into the episode segment registry:

```bash
python scripts/write_episode_segments.py --temp-dir "<temp folder>" --key main --entry-json "<temp folder>/segment_main.entry.json"
```

Re-run with `--allow-overwrite` only after explicit user approval if `main` already exists.

**Segment key policy:** always use **`main`** for the interview segment. DSL commands use `$segmentmain/<id>`.

### 4) Generate the full DSL (camera + wide rule)

Use `generate_full_dsl.py` (now includes camera switching + wide rule by default) to generate:

- Full DSL: `<temp folder>/interview.dsl`

Command template:

```bash
python generate_full_dsl.py "<temp folder>/interview_transcript_simplified.json" --segment main --output "<temp folder>/interview.dsl" [--camera-switch-offset-ms <NEGATIVE_MS>] [--no-camera-switch-offset]
```

**Default** (`generate_full_dsl.py` applies **-250 ms** automatically; no extra flag needed unless the user said `adjust off` or overrides the amount):

```bash
python generate_full_dsl.py "<temp folder>/interview_transcript_simplified.json" --segment main --output "<temp folder>/interview.dsl"
```

If the user overrides with e.g. `Adjust -200 ms`, add `--camera-switch-offset-ms -200`. If they said `adjust off`, add `--no-camera-switch-offset`.

### 5) Render ONLY the 1-minute test from the full DSL (always redirect TEMP/TMP on Windows)

Rendering writes large temp files. On Windows, **always** set `TEMP` and `TMP` to `<temp folder>` before rendering.

By default, `python -m podcast_dsl` now auto-selects a working hardware H.264 encoder if available and falls back to `libx264` otherwise. If the user explicitly asks for software-only or a specific encoder, add `--video-encoder <encoder>`.
If the user explicitly asks to downscale 4K footage, add `--downscale-4k-to-1080p`.

Render template (PowerShell-friendly; do not use `&&`):

```powershell
Set-Location "<repo>\\src"
$env:TEMP = "<temp folder>"
$env:TMP  = "<temp folder>"
$env:PODCAST_DSL_SEGMENTS_FILE = "<temp folder>\segments.json"

python -m podcast_dsl "<temp folder>\interview.dsl" -o "<output folder>\1 Min Test.mp4" --workers 6 --max-seconds 60 --segments-file "<temp folder>\segments.json"
```

After the 1-minute render completes, **pause** and report completion. **Always** include this note (verbatim intent; wording may be natural):

> **1-minute test render complete.** Review `1 Min Test.mp4` in `<output folder>`. If Host and Guest cameras look swapped, say so and we can re-run transcript conversion with `--swap-speaker-ids`, regenerate the DSL, and render again. Otherwise, continue when ready.

Then ask:

- “Do you want to continue with the 5-minute test render?”
- “Do you want to render the full episode MP4?”

### 6) Optional: render 5-minute test and/or full episode (only if user agrees)

```powershell
Set-Location "<repo>\\src"
$env:TEMP = "<temp folder>"
$env:TMP  = "<temp folder>"
$env:PODCAST_DSL_SEGMENTS_FILE = "<temp folder>\segments.json"

python -m podcast_dsl "<temp folder>\interview.dsl" -o "<output folder>\5 Min Test.mp4" --workers 6 --max-seconds 300 --segments-file "<temp folder>\segments.json"
python -m podcast_dsl "<temp folder>\interview.dsl" -o "<output folder>\Full Interview.mp4" --workers 6 --segments-file "<temp folder>\segments.json"
```

If the user asked for **massive** on the full episode, use the same command with **`--massive`** appended (and keep any `--downscale-4k-to-1080p` / `--video-encoder` flags they requested):

```powershell
Set-Location "<repo>\\src"
$env:TEMP = "<temp folder>"
$env:TMP  = "<temp folder>"

python -m podcast_dsl "<temp folder>\\interview.dsl" -o "<output folder>\\Full Interview.mp4" --workers 6 --massive
```

After it finishes, confirm **`Ben Render.mp4`**, **`Guest Render.mp4`**, and **`Wide Render.mp4`** exist in **`<output folder>`** beside **`Full Interview.mp4`**, and optional **`.dsl`** siblings exist in **`<temp folder>`**.

### 7) Validate outputs

- Confirm MP4 files in **`<output folder>`** exist and are non-trivial size.
- Confirm **`interview_transcript_simplified.json`** and **`interview.dsl`** exist in **`<temp folder>`** (not under Output).
- Optional: run `--dry-run` on `<temp folder>/interview.dsl` to confirm total duration before a long render.
- If **massive** was used: also confirm the three single-camera outputs are non-trivial size.

## Usage example

User: “Load Inkhaven-Podcast-Autocut. Working folder is `D:\\Project`. Ben close is `Ben Close.mp4`, guest is `Guest Close.mp4`, wide is `Interview Wide.mp4`, audio is `interview audio.wav`, transcript is `interview transcript detail.json`.”

Assistant (following this skill):

- Uses `D:\Project\Input` for inputs, writes MP4s to `D:\Project\Output`, pipeline JSON/DSL to `D:\Project\Temp`, and redirects `TEMP/TMP` to `D:\Project\Temp`
- Convert transcript to `D:\Project\Temp\interview_transcript_simplified.json`
- Upsert segment **`main`** into `D:\Project\Temp\segments.json` via `scripts/write_episode_segments.py`
- Generate `D:\Project\Temp\interview.dsl` (with dense-cuts→wide; default **-250 ms** camera-switch offset is built into `generate_full_dsl.py`)
- Render ONLY `1 Min Test.mp4` into the output folder (DSL path `D:\Project\Temp\interview.dsl`) with `TEMP/TMP` redirected to `D:\Project\Temp`
- Pause: confirm 1-minute render complete, **always** mention the optional Host/Guest swap (`--swap-speaker-ids`), then ask whether to continue with the 5-minute test and/or full render

