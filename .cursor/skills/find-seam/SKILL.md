---
name: find-seam
description: Finds edit seam timestamps between transcript-defined video sections by downmixing source audio to mono and selecting the zero-crossing nearest the midpoint between the prior section's last word and the following section's first word. Use when the user says "Find Seam", asks for clean splice points, supplies section-start words or phrases, or needs word-safe audio cut locations before inserting clips.
---

# Find Seam

Find audio-safe section boundaries without rendering or modifying source media.
The implementation is self-contained in `scripts/find_seams.py`.

## Inputs

Collect:

- Base media file with an audio stream.
- Word-timed JSON transcript for that same media.
- Ordered first word or phrase for every section after the first.
- Optional section names and marker occurrence numbers.

The transcript must contain `words[]` entries with `text` (or `word`) and
`start_time`/`end_time` (or `start`/`end`). Plain text transcripts are
insufficient.

## Seam definition

For each section boundary:

1. Find the following section's ordered marker in the transcript.
2. Let **Last Word** be the transcript word immediately before that marker.
3. Let **First Word** be the marker's first word.
4. Use `[Last Word.end, First Word.start]` as the valid seam window.
5. Decode the media's first audio stream at its native sample rate and downmix
   it to mono.
6. Find every exact-zero sample and every sign crossing in the window. Linearly
   interpolate sign crossings between adjacent samples.
7. Choose the crossing nearest the window midpoint.
8. If no crossing exists, choose the minimum-absolute-amplitude sample nearest
   the midpoint.

Do not substitute transcript padding, a fixed offset, RMS minimum, or a video
frame boundary for this calculation.

## Safety

- This skill only analyzes and writes optional JSON/Markdown reports. It does
  not edit or render video.
- Before overwriting a report, list it and get explicit user approval; then use
  `--allow-overwrite`.
- Audio decoding is temporary but processes the complete audio stream. If it is
  likely to exceed 30 seconds, follow the repository's long-job approval rule.
- Keep the base media and transcript unchanged.

## Run

From the repository root:

```bash
python .cursor/skills/find-seam/scripts/find_seams.py \
  --media "<base.mp4>" \
  --transcript "<base.mp4.json>" \
  --markers One Two Three Four Five Six Seven \
  --section-names Intro "Reason 1" "Reason 2" "Reason 3" "Reason 4" "Reason 5" "Reason 6" "Reason 7" \
  --output-json "<report.json>" \
  --output-markdown "<report.md>"
```

Use `--marker-occurrences 1 1 1 1 2 1 1` when, for example, the desired
Reason 5 starts at the second eligible `Five`. Matching is ordered and
case-insensitive by default; add `--case-sensitive` when transcript
capitalization is meaningful.

## Verify and report

Before presenting results:

1. Confirm ffprobe reports the expected audio stream and start time.
2. Review marker ambiguity fields, especially repeated section numerals.
3. Confirm every window has `Last Word.end <= First Word.start`.
4. Report each seam as `HH:MM:SS.mmm`.
5. Include the configured number of words before and after each seam (default
   10), with Last Word and First Word identified.
6. State any `minimum-absolute-sample` fallback explicitly.
7. Note that the timestamp is audio-sample accurate, while an eventual video
   edit must be rendered at the selected timestamp rather than stream-copied to
   a keyframe.

## Porting

For a diverged Podcast In A Box repository, copy or cherry-pick only this
`find-seam/` directory. Read [INTEGRATION.md](INTEGRATION.md) before wiring its
JSON output into an editor pipeline.
