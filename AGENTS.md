# Agent directives (project-wide)

## No overwrite without user verification (Inkhaven harness)

When running the **inkhaven-episode-harness** or any step it chains, **never replace an existing file** (Output deliverables, Temp DSL/JSON, user exports, etc.) without **listing what would be overwritten** and getting **explicit user approval** first. After approval only: pass **`--allow-overwrite`** on harness scripts or write to a **new filename**. See **`.cursor/skills/inkhaven-episode-harness/SKILL.md`** (hard rule section).

## Primary directive: avoid long renders during debugging

When doing **debugging**, **error-correction**, or **investigation**, do **not** start any **long-running video render / re-encode / multicam / ffmpeg** job (anything likely to take more than ~30 seconds, create multi-GB outputs, or lock files) **without explicitly asking the user first**.

If a long job seems necessary, first propose the smallest safe alternative (e.g. `--dry-run`, `ffprobe` checks, short clip, `-t` sample render, or reporting-only mode), then wait for approval before launching the full run.

## Long jobs: 5-minute completion checks

After the user approves and you **start** a long prep/render/video-sync job in the background: confirm it started once and report that it is running plus the estimate. Do **not** poll every few seconds.

**While any harness rendering-class task is running** (video-sync / multicam prep, `podcast_dsl` / reading renders, stitch, PIAB prep or full render, or a chained prep→1-min-test pipeline): **check status about every 5 minutes** until the job completes or fails, then notify the user immediately. Prefer a short progress note on each check (current step / newest outputs) when useful; always notify on completion or failure.

PIAB prep/full render also write **`Temp/harness-FAILURE.json`** and raise a **Windows toast with sound** on step failure (e.g. ElevenLabs payment errors). Treat that marker or a non-zero exit as an immediate failure — do not wait for the next 5-minute poll.

Do not busy-wait with sub-minute polling. Five minutes is the default cadence for these jobs unless the user asks for a different interval.

## PIAB / podcast autocut: Host–Guest audio “swapped”

When the user says Host and Guest **audio** are swapped, reversed, or on the wrong mic in the edit, assume **Raw and Input are labeled correctly**. Fix with speaker-ID remapping only (`piab_fix_audio_speaker_swap.py` for PIAB, or `--swap-speaker-ids` + DSL + 1-min render for standalone autocut). **Do not** swap Raw audio files or re-run video-sync / full prep unless the user explicitly says the **Raw** mic or camera files were mislabeled during labeling.

## Crash dumps (this machine)

For post-mortem debugging of BSODs / kernel bugchecks, newer kernel dumps on this machine are kept under **`D:\Crash Report`** (user-configured location). When investigating a render-time crash, check that folder for `.dmp` files and timestamps that match the incident.

