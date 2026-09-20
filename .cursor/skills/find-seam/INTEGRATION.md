# Find Seam integration guide

`find-seam` is intentionally isolated from Podcast In A Box application code.
The entire transferable unit is:

```text
.cursor/skills/find-seam/
├── SKILL.md
├── INTEGRATION.md
└── scripts/
    └── find_seams.py
```

It uses only the Python standard library plus `ffmpeg` and `ffprobe on PATH`.
It does not import PIAB modules, mutate media, or assume a repository layout.

## Lowest-conflict transfer

Preferred when the destination repository has diverged:

1. Copy `.cursor/skills/find-seam/` unchanged into the destination repository.
2. Run the smoke command in `SKILL.md` against known media and transcript files.
3. Compare the emitted seam timestamps with the source report.
4. Commit the directory as one isolated commit in the destination repository.

If this directory is committed alone in the source repository, the destination
agent can instead cherry-pick that commit. Because the commit adds a new
directory and touches no shared PIAB files, merge conflicts should be unlikely.

## Stable command contract

Required:

```text
--media PATH
--transcript PATH
--markers MARKER [MARKER ...]
```

Useful integration options:

```text
--section-names NAME [NAME ...]
--marker-occurrences N [N ...]
--context-words N
--output-json PATH
--output-markdown PATH
--json-stdout
--case-sensitive
--allow-overwrite
```

`--section-names` contains one more value than `--markers`: the first section
has no marker because there is no seam before it.

## JSON output contract

The top-level `schema_version` is currently `1`. Consumers should reject future
unknown major versions rather than guessing.

Each item in `seams` includes:

- `boundary`: human-readable prior and following section names.
- `time_seconds`, `time_milliseconds`, `timecode`: selected seam.
- `method`: `zero-crossing` or `minimum-absolute-sample`.
- `midpoint_seconds` and `offset_from_midpoint_ms`.
- `window.start_seconds` / `window.end_seconds`.
- `last_word` / `first_word`, each with text and transcript timestamps.
- `words_before` / `words_after`.
- `marker`: selected transcript marker and ambiguity metadata.

Fields may be added within schema version 1. Consumers should ignore fields they
do not need.

## Integrating into a divergent editor

Keep seam detection as a pure analysis stage:

1. Run `find_seams.py` before building the edit timeline.
2. Read the report and map section boundary IDs to `time_seconds`.
3. Validate that marker text and Last/First Word still match the intended
   transcript before applying edits.
4. Insert clips at those exact timeline timestamps.
5. Re-encode around joins. Do not rely on keyframe-only stream copying.
6. Preserve the seam report beside other temporary edit artifacts for audit and
   reproducibility.

Do not copy internal functions into a diverged `generate_full_dsl.py`. Invoke
the script through its CLI/JSON contract or import it behind a small adapter.
That keeps future updates cherry-pickable and avoids coupling to either
repository's transcript-row or rendering internals.

## Transcript adapters

The script directly accepts:

- Detail JSON with top-level `segments[]`, each containing `words[]`.
- A top-level list of rows containing `words[]`.
- A dictionary of row objects containing `words[]`.

Supported word timestamp names are `start_time`/`end_time` and `start`/`end`.
If another PIAB branch uses a different transcript schema, convert it to one of
these shapes in a destination-side adapter; do not fork the seam algorithm.

## Compatibility verification

On the destination computer, verify:

```bash
python --version
ffmpeg -version
ffprobe -version
python .cursor/skills/find-seam/scripts/find_seams.py --help
```

Then run one known fixture and compare JSON values for:

- `analysis.sample_rate_hz`
- every `window`
- every `time_milliseconds`
- every `last_word.text` and `first_word.text`

Differences larger than one source-audio sample indicate a transcript, media,
stream-selection, or ffmpeg-version mismatch that should be investigated before
editing.
