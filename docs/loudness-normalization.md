# Loudness normalization for prep deliverables

Prep outputs (`Input/*-prepped.mp4`, `Input/*-prepped.wav`) are normalized to **−14 LUFS** integrated loudness using FFmpeg’s **`loudnorm`** filter (EBU R128 / ITU-R BS.1770 measurement).

## Why

Listener feedback indicated interview exports were too quiet. Prep is the right hook: every downstream step (transcription, DSL render, 1-min test, full render) reads from prepped media.

## Target levels

| Parameter | Value | Meaning |
|-----------|-------|---------|
| **I** | **−14 LUFS** | Integrated loudness (streaming/podcast class) |
| **TP** | **−1.5 dBTP** | True-peak ceiling |
| **LRA** | **11 LU** | Loudness range target |

Adjust constants in `scripts/harness_loudnorm.py` (`PREPPED_TARGET_*`).

## Two-pass workflow

FFmpeg `loudnorm` supports single-pass (estimate while encoding) and **two-pass** (measure entire file, then apply linear gain). Prep uses **two-pass** for consistent level hit.

### Pass 1 — measure (no output file, audio-only)

```bash
ffmpeg -i input.mp4 -vn -map 0:a:0? \
  -af "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json" \
  -f null -
```

`-vn` skips video decode (large speed win on 1080p MP4s). Parse the JSON block from stderr:

- `input_i`, `input_tp`, `input_lra`, `input_thresh`, `target_offset`

When loudnorm runs **during multicam**, pass 1 uses the same head trim as prep:

```bash
-af "atrim=start=<trim_sec>,asetpts=PTS-STARTPTS,loudnorm=...:print_format=json"
```

### Pass 2 — apply (linear normalization)

**Integrated into multicam re-encode (default 2+ camera path):**

```bash
-filter_complex "
  [0:v]trim=start=<trim_sec>,setpts=PTS-STARTPTS[,scale=...][v];
  [0:a]atrim=start=<trim_sec>,asetpts=PTS-STARTPTS,loudnorm=...:measured_I=...:linear=true[a]
"
```

One encode pass: trim + downscale + H.264 + loudnorm AAC. No second full-file read.

**Post-prep fallback** (single camera, stream-copy trim, or manual CLI):

```bash
ffmpeg -i input.mp4 \
  -af "loudnorm=I=-14:...:measured_I=<input_i>:...:linear=true" \
  -map 0:v:0? -map 0:a:0? -c:v copy -c:a aac -b:a 192k output.mp4
```

For WAV: same filter chain, output `-c:a pcm_s16le -ac 2 -ar 48000`.

## Code layout (this repo)

| File | Role |
|------|------|
| `scripts/harness_loudnorm.py` | Shared two-pass implementation, trim-aware helpers, CLI |
| `scripts/multicam_align_trim.py` | `--loudnorm` (default on with `--prepped-names`): pass 1 on trimmed synced audio, pass 2 in re-encode filter chain |
| `scripts/harness_video_sync.py` | Multicam with loudnorm; single-camera post-copy loudnorm |
| `scripts/test_harness_loudnorm.py` | Unit + ffmpeg integration tests |

### Integration points

**Multicam (2+ cameras, re-encode — PIAB default):**

1. `multicam_align_trim.py --prepped-names --loudnorm` (loudnorm default **on** with prepped names)
2. Per camera: pass 1 `measure_loudnorm_trimmed(synced, trim_sec)` with `-vn`
3. Same ffmpeg encode applies trim + loudnorm pass 2 on audio
4. Extract anchor WAV from normalized prepped MP4

**Single camera:**

1. Copy synced → Input
2. `normalize_prepped_outputs` (post-hoc two-pass, pass 1 uses `-vn`)
3. Extract anchor WAV

**Stream-copy / no-trim copy paths:** multicam writes the file, then `normalize_prepped_media_inplace` (post-hoc).

### CLI (manual / debugging)

```powershell
python scripts/harness_loudnorm.py "E:\PodcastRoom\<session>\Input\Host Video-prepped.mp4"
```

## Performance notes

| Approach | Cost |
|----------|------|
| Pass 1 with `-vn` | Audio decode only — minutes, not hours, on long interviews |
| Pass 2 merged into multicam | No extra full-file pass; audio encoded once |
| Old post-prep loudnorm on all prepped MP4s | Second full read + second audio encode per camera — removed for multicam re-encode path |

## Porting checklist

When copying to **`automated-video-editing`** (PIAB fork) or **Inkhaven Autocut**:

1. Copy **`scripts/harness_loudnorm.py`** as-is (stdlib + ffmpeg only).
2. Copy **`scripts/test_harness_loudnorm.py`** and run:  
   `python -m unittest scripts.test_harness_loudnorm -v`
3. Merge loudnorm into the fork’s **`multicam_align_trim.py`** (or equivalent):
   - `--loudnorm` default on with `--prepped-names`
   - pass 1: `measure_loudnorm_trimmed` with `-vn`
   - pass 2: append `build_loudnorm_pass2_filter` to the audio filter chain during re-encode
   - post-hoc `normalize_prepped_media_inplace` for stream-copy / copy-only paths
4. In prep orchestrators: **do not** loudnorm again after multicam when `--prepped-names --loudnorm` ran; keep post-hoc for single-camera only.
5. Copy this doc to the target repo’s `docs/loudness-normalization.md`.
6. Re-run video-sync / prep on a test session; verify with analysis:  
   `ffmpeg -i "Input/Host Video-prepped.mp4" -af ebur128=peak=true -f null -`

Expected integrated loudness **≈ −14 LUFS** (within ~±0.5 LU).

## Not in scope (yet)

- Final `Full Interview.mp4` render (`podcast_dsl` / `video_renderer`) — still uses DSL `!volume`, not loudnorm.
- `stitch_episode.py` — uses its own single-pass `loudnorm=I=-16` per clip; unchanged here.

## References

- [FFmpeg loudnorm filter](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)
- [FFmpeg ebur128 filter](https://ffmpeg.org/ffmpeg-filters.html#ebur128) (analysis only)
- EBU R128 / ITU-R BS.1770
