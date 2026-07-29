# Podcast in a Box App

Standalone app leg of **Podcast in a Box (PIAB)** — built from the Cursor/agent pipeline in the parent project.

## Relationship to the fallback pipeline

| | **This repo (App)** | **Fallback (PIAB pipeline)** |
|---|---------------------|------------------------------|
| **Local path** | `E:\PodcastRoom\Cursor\podcast-in-a-box-app` | `E:\PodcastRoom\Cursor\automated-video-editing` |
| **Git remote (`upstream`)** | — | `https://github.com/embrodski/automated-video-editing` |
| **Frozen branch** | — | `lighthaven-podcast-in-a-box` |
| **Purpose** | Next phase: standalone app / GUI / productization | Cursor-guided walk-in pipeline (recording + autocut + delivery) |

The fallback branch is **not modified** here. To cherry-pick fixes or sync shared scripts, use `upstream`:

```powershell
git fetch upstream
git cherry-pick <commit>   # or merge upstream/lighthaven-podcast-in-a-box
```

## Starting point

This fork was created from commit on `lighthaven-podcast-in-a-box` with the full recording flow (vMix / MultiCorder) and autocut flow (label → prep → render → Frame.io delivery).

Default branch: **`main`**.

## Run (unchanged from PIAB)

```powershell
python scripts/piab_start_session.py
```

See `.cursor/skills/lighthaven-podcast-in-a-box/SKILL.md` for the full pipeline until app-specific docs replace it.
