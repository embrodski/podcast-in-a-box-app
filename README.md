# Podcast in a Box App

Standalone app leg of **Podcast in a Box (PIAB)** — built from the Cursor/agent pipeline in the parent project.

## Relationship to the fallback pipeline

| | **This repo (App)** | **Fallback (PIAB pipeline)** |
|---|---------------------|------------------------------|
| **Local path** | `E:\PodcastRoom\PodcastInABox` | `E:\PodcastRoom\Cursor\automated-video-editing` |
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

**App design (GUI + controller):** [`docs/piab-app-architecture.md`](docs/piab-app-architecture.md)

**Controller CLI (no GUI):**

```powershell
python -m app.cli preflight
python -m app.cli sessions
python -m app.cli resume --folder "E:\PodcastRoom\PodcastInABox\Sessions\<name>"
```

**Desktop app (PySide6):**

```powershell
pip install -r requirements-app.txt
python -m app.main
```

Or double-click `run_piab_app.bat` (keeps a console for errors).

To add **Start Menu** and **Desktop** shortcuts (launches with `pythonw.exe`, no console, uses `assets/piab.ico`):

```powershell
python scripts/piab_install_shortcuts.py
```

Run that once after clone. Rebuild the icon with `python scripts/piab_build_app_icon.py` if the source PNG changes.

## Process log

Every autocut session is summarized in a single app-wide log:

**`E:\PodcastRoom\PodcastInABox\Sessions\piab-process-log.json`**

Each entry records when the process began, the delivery email (if any), the project folder and its subfolders, completed steps with timestamps, and whether the final video finished, uploaded to Frame.io, and emailed (with dates). The log updates whenever session state is saved.

**Clean Old Working Files** (home screen) lists logged projects that still have `Raw` / `Input` / `Temp` / `Preview Files`, moves those folders to the Recycle Bin (keeps `Output`), and records the cleanup on the same log entry.

## Syncing scripts from the fallback pipeline

Shared PIAB logic lives in both repos. The **app** vendors copies under `scripts/`; the **fallback** repo (`automated-video-editing` @ `lighthaven-podcast-in-a-box`) is the usual source for new features.

**Do not blindly overwrite app scripts.** Merge manually and run tests after each sync.

### Copy as-is (new files only)

- New shared modules with no app-specific fork (e.g. `harness_av_sync_lib.py`, `harness_loudnorm.py`, new one-off `piab_*.py` helpers)

### Merge manually (diff both sides)

| File | Why |
|------|-----|
| `scripts/piab_lib.py` | App: multi-cluster scan (`cluster_index`), GUI session naming, copy-vs-move labeling. Fork: sync step markers, other prep tweaks. |
| `scripts/piab_run_prep.py` | Prep flow + sync A/B; app uses PIAB step IDs (`10`/`11`/`10a`), not harness `15`/`18`. |
| `scripts/piab_rerun_one_min.py` | Must expose `rerun_one_min_test()` for GUI speaker-swap fix. |
| `scripts/piab_run_full_render.py` | Forced-offset full render when sync confidence failed. |
| `scripts/piab_resume.py` | Resume routing including `10a_sync_offset_approval`. |

### Never copy from fork → app

| File | Reason |
|------|--------|
| `scripts/harness_episode_lib.py` | App state file is `podcast-in-a-box.json`; fork/CLI uses `cursor-podcast-in-a-box.json`. App also has ElevenLabs key fallback and prep `started_at` UI support. |

### After every sync

```powershell
cd E:\PodcastRoom\PodcastInABox\scripts
python -m unittest test_piab_lib.DiscoverClustersTests test_piab_sync_offset -v
cd ..
python -m unittest tests.test_controller -v
```

Confirm `collect_session_scan` still accepts `cluster_index` and the GUI special-folder scan works.

## Prep loudness normalization

Video-sync prep (`*-prepped.mp4` / `*-prepped.wav`) is two-pass loudnorm'd to **−14 LUFS**. Multicam applies pass 2 during re-encode (pass 1 uses `-vn` on trimmed audio); single-camera prep uses post-copy loudnorm. See [`docs/loudness-normalization.md`](docs/loudness-normalization.md).

### State files (do not mix)

| Repo | Session JSON |
|------|----------------|
| **This app** | `<folder>/podcast-in-a-box.json` |
| **Fallback / Cursor agent** | `<folder>/cursor-podcast-in-a-box.json` |

Both can exist in the same episode folder; the app treats the Cursor file as a conflict, not a resumable GUI session.
