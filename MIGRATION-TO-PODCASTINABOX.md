# Migration: repo → `E:\PodcastRoom\PodcastInABox`

**Status:** executed flatten + `Sessions\` work root (2026-08-15).  
**Date:** 2026-08-15  
**Source (still on disk; rename blocked while in use):** `E:\PodcastRoom\Cursor\podcast-in-a-box-app` — rename to `OLD-podcast-in-a-box-app` or delete after closing anything still using that path.  
**Destination (repo root):** `E:\PodcastRoom\PodcastInABox`  
**Work root:** `E:\PodcastRoom\PodcastInABox\Sessions`

**Chosen layout (user confirmed):** the git repo *is* `E:\PodcastRoom\PodcastInABox`. Session files, process log, job queue, and app lock live in `Sessions\`. Secrets (`.env`, `ElevenLabs 100k Key.txt`, `.frameio-oauth.json`) moved with the tree.

## Execution log (2026-08-15)

- Pointed `DEFAULT_WORK_ROOT` / process log / lock / queue at `E:\PodcastRoom\PodcastInABox\Sessions`.
- `migrate_legacy_work_files()` also picks up leftover log/queue/lock at `E:\PodcastRoom` and at the `PodcastInABox` repo root.
- Updated GUI/CLI/README/architecture/skill strings; `.gitignore` now ignores `/Sessions/` and leftover root log/queue/lock files.
- Copied the full tree with robocopy (including `.git` and the three secrets).
- Rebuilt `Sessions\piab-process-log.json` from `E:\PodcastRoom\Test` and `E:\PodcastRoom\Test 2` after unit tests had overwritten the live log with temp-folder rows. `sync_process_log_from_state()` now skips folders under the system temp directory so that cannot happen again.
- Cursor workspace is now `E:\PodcastRoom\PodcastInABox`. Launch the app with `run_piab_app.bat` from that folder.
- Rename of the old `Cursor\podcast-in-a-box-app` folder was denied (path still in use). Do that manually when nothing is using it.

This file is the inventory of what must move, what must stay, and which code/docs change so the GUI, CLI, skills, and fallback pipeline keep working after the move.

---

## 1. What this is (and is not)

This is a **codebase / git-repo relocation**. It is separate from the earlier **data work-root** change (already live):

| Role | Path today | After this migration |
|------|------------|----------------------|
| MultiCorder dumps (vMix hardware) | `E:\PodcastRoom` | **unchanged** |
| vMix presets | `E:\PodcastRoom\vMix Configs` | **unchanged** |
| Fallback pipeline repo | `E:\PodcastRoom\Cursor\automated-video-editing` | **unchanged** (stays under `Cursor\`) |
| PIAB sessions, process log, job queue, app lock | `E:\PodcastRoom\PodcastInABox` | **stays this folder** (see layout) |
| This git repo (app + scripts + skills) | `E:\PodcastRoom\Cursor\podcast-in-a-box-app` | **moves** (see layout) |

Existing session folders `E:\PodcastRoom\Test` and `E:\PodcastRoom\Test 2` are **not** part of the repo. They stay where they are unless you later ask to relocate them. Resume / Clean already find them via the process log and Browse.

---

## 2. Recommended layout (do not flatten)

`E:\PodcastRoom\PodcastInABox` already exists and already holds live app data:

- `piab-process-log.json` (present today)

Dumping the git tree **into that same folder** would mix `.git`, `app\`, `scripts\`, secrets, and the repo `Temp\` with session folders and the process log. Lost-session scan and Clean would then walk repo directories (`app`, `scripts`, `Temp`) as if they might be sessions.

**Recommended target:**

```
E:\PodcastRoom\PodcastInABox\                 work root (sessions + log + queue + lock)
  piab-process-log.json
  piab-job-queue.json                        created when the queue is used
  .piab-app.lock                             created while the app is open
  2026-08-15_1745\                           future session folders
  podcast-in-a-box-app\                      THIS GIT REPO (moved here)
    .git\
    app\
    scripts\
    .cursor\skills\
    run_piab_app.bat
    ElevenLabs 100k Key.txt                  untracked secret — must move
    .env                                     untracked secret — must move
    .frameio-oauth.json                      untracked secret — must move
    ...
```

That is “everything moved to `PodcastInABox`” without colliding with session data.

### Alternative (only if you want the repo root to *be* `PodcastInABox`)

```
E:\PodcastRoom\PodcastInABox\                 git repo root
  app\  scripts\  .git\  ...
  Sessions\                                  NEW work root
    piab-process-log.json
    session folders...
```

This needs extra code (section 6B). Prefer the recommended layout unless you specifically want File Explorer to open the repo when you open `PodcastInABox`.

---

## 3. What to move

**Move the entire working tree.** Runtime paths are computed from `__file__` (`REPO_ROOT = Path(__file__).resolve().parents[2]` in `app/controller/paths.py`, and `parent.parent` from scripts). If the whole tree moves together, Python keeps working.

### Must move (functionality)

| Item | Why |
|------|-----|
| Entire tracked repo (`.git`, `app\`, `scripts\`, `src\`, `tests\`, `docs\`, `assets\`, `generate_full_dsl.py`, `convert_transcript_json.py`, `podcast-phrase-gates.json`, `run_piab_app.bat`, `requirements-app.txt`, `AGENTS.md`, `.cursor\skills\`, …) | App, CLI, skills, phrase gates, renderer |
| `.git` | Keep history and remotes (`origin` = `embrodski/podcast-in-a-box-app`, `upstream` = `automated-video-editing`) |
| `ElevenLabs 100k Key.txt` | Preflight / transcribe (gitignored) |
| `.env` | Frame.io / email / other secrets (gitignored) |
| `.frameio-oauth.json` | Existing Frame.io login (gitignored) |
| `.cursor\skills\` | PIAB and Inkhaven agent skills |

`run_piab_app.bat` uses `cd /d "%~dp0"` then `python -m app.main`, so it keeps working after the folder moves. No bat edit required.

### Should move (keep the tree intact)

| Item | Notes |
|------|-------|
| `piab-local-handoff\` | Historical handoff notes; paths inside are stale after the move |
| Repo `Temp\` (e.g. `test-scan.json`) | Small debug leftovers; moving is safer than leaving a second copy |
| Loose JSON / DSL / transcript samples at repo root | Not required for the GUI; moving avoids a split tree |
| `__pycache__\` | Optional; safer to omit and let Python recreate |

### Do not move

| Path | Why |
|------|-----|
| `E:\PodcastRoom` MultiCorder `*.MP4` / `*.WAV` dumps | Hardware still writes here; `DEFAULT_SCAN_ROOT` stays this path |
| `E:\PodcastRoom\vMix Configs` | `DEFAULT_VMIX_PRESET_DIRS` stays here |
| `E:\PodcastRoom\Cursor\automated-video-editing` | Fallback pipeline; `DEFAULT_UPSTREAM_REPO_ROOT` stays here |
| `E:\PodcastRoom\Cursor\Inkhaven *` episode folders | Unrelated media; only referenced in `src/podcast_dsl/config.py` examples |
| `E:\PodcastRoom\Test`, `E:\PodcastRoom\Test 2` | Live/old sessions, not repo files |
| `E:\PodcastRoom\PodcastInABox\piab-process-log.json` | Already at the work root; do not nest it inside the repo |
| `C:\Users\jakey\.cursor\projects\e-PodcastRoom-Cursor-podcast-in-a-box-app\` | Cursor cache / old chat transcripts; a new project folder is created when you open the new path |
| `D:\Crash Report` | Crash dumps; unrelated |

No desktop or Start Menu shortcuts named PIAB / podcast-in-a-box were found on this machine (2026-08-15). If you later pin `run_piab_app.bat`, recreate the pin after the move.

---

## 4. How the app finds paths today (why a whole-tree move works)

These resolve **relative to the moved files**. No Python edit is required if the recommended layout is used.

| Symbol | Defined in | Resolves to |
|--------|------------|-------------|
| `REPO_ROOT` | `app/controller/paths.py` | Two parents above `paths.py` → repo root |
| `SCRIPTS_DIR` / `ASSETS_DIR` | same | `REPO_ROOT / scripts`, `REPO_ROOT / assets` |
| `harness_episode_lib.REPO_ROOT` | `scripts/harness_episode_lib.py` | Parent of `scripts\` |
| `harness_env.REPO_ROOT` / `.env` | `scripts/harness_env.py` | Repo-root `.env` |
| Phrase gates | `scripts/podcast_phrase_gates.py` | `REPO_ROOT / podcast-phrase-gates.json` |
| ElevenLabs key file | `scripts/harness_episode_lib.py` | `REPO_ROOT / ElevenLabs 100k Key.txt` |
| Subprocess `cwd` | `app/controller/{labeling,fast_preview,review,sync_offset}.py` | `REPO_ROOT` |

These stay **absolute and must not follow the repo**:

| Symbol | Value | Reason |
|--------|-------|--------|
| `DEFAULT_SCAN_ROOT` | `E:\PodcastRoom` | MultiCorder dump location |
| `DEFAULT_WORK_ROOT` | `E:\PodcastRoom\PodcastInABox` | Sessions + log + queue + lock |
| `DEFAULT_PROCESS_LOG_ROOT` | `E:\PodcastRoom\PodcastInABox` | Same |
| `DEFAULT_VMIX_PRESET_DIRS` | `E:\PodcastRoom\vMix Configs` | Hardware presets |
| `DEFAULT_UPSTREAM_REPO_ROOT` | `E:\PodcastRoom\Cursor\automated-video-editing` | Fallback repo (optional `.env` inherit) |

GUI copy that already says “files are saved under `E:\PodcastRoom\PodcastInABox`” stays correct under the recommended layout.

---

## 5. Code / doc changes — recommended layout (repo as subfolder)

Python runtime constants **do not need to change**. Only human-facing paths that still name the old Cursor folder.

### Required doc edits

| File | Change |
|------|--------|
| `README.md` | Table “Local path” → `E:\PodcastRoom\PodcastInABox\podcast-in-a-box-app`. Sync-test `cd` line → the new `scripts\` path. |
| `docs/piab-app-architecture.md` | Fallback path can stay `E:\PodcastRoom\Cursor\automated-video-editing`. Add one line that the **app repo** now lives under `PodcastInABox\podcast-in-a-box-app`. |
| `.cursor/skills/lighthaven-podcast-in-a-box/SKILL.md` | Work/scan roots already correct. No path to the old repo. Optional: note the new repo location at the top. |
| This file | After the move, mark **Status: executed** and record the date. |

### Optional / historical (do not block the move)

| File | Change |
|------|--------|
| `piab-local-handoff/HANDOFF-local-agent-final-render-bug.md` | Old “Repo (local)” path. Leave as history or add a one-line “moved to …”. |
| `src/podcast_dsl/config.py` | Many `E:\PodcastRoom\Cursor\Inkhaven …` example media paths. **Do not retarget** — those episode folders are not moving. |

### Tests

No test hardcodes `E:\PodcastRoom\Cursor\podcast-in-a-box-app`. After the move, from the new repo root:

```powershell
python -m unittest tests.test_controller tests.test_job_queue tests.test_abort_session tests.test_storage_gate tests.test_window_manager tests.test_failure_alert -v
python -m unittest scripts.test_piab_clean_working_files scripts.test_piab_process_log scripts.test_piab_fast_preview -v
```

### Git remotes

No change. `origin` and `upstream` are URLs, not local paths.

### Cursor after the move

1. Close the PIAB app (so `.piab-app.lock` is not held).
2. Open the new folder in Cursor (`File → Open Folder` → `E:\PodcastRoom\PodcastInABox\podcast-in-a-box-app`), or use `move_agent_to_root` once the copy exists.
3. Confirm skills still load from `.cursor\skills\` in the new root.
4. Old Cursor project cache under `C:\Users\jakey\.cursor\projects\e-PodcastRoom-Cursor-podcast-in-a-box-app` can stay; it is history, not runtime.

---

## 6. Extra code changes — only if you flatten the repo into `PodcastInABox`

Do **not** do this without the following. Otherwise Clean / lost-session scan can treat repo folders as sessions.

### 6A. Minimum safety if work root stays `E:\PodcastRoom\PodcastInABox`

| File | Change |
|------|--------|
| `scripts/piab_clean_working_files.py` → `SKIP_SCAN_DIR_NAMES` | Add: `podcast-in-a-box-app` is N/A if flattened; instead add `app`, `scripts`, `tests`, `docs`, `assets`, `src`, `.git`, `.cursor`, `piab-local-handoff`, and the repo `temp` name. |
| `app/controller/session_store.py` → `list_recent_sessions` | Already only picks `*/podcast-in-a-box.json`. Still skip children whose names are in the skip set so a stray state file in `Temp` cannot appear. |
| `app/controller/session_store.py` → `generate_session_name` | Already uses date stamps; low collision risk. Still skip reserved names if a stamp ever matches. |

This still leaves `.git` next to session folders and the process log. Not recommended.

### 6B. Cleaner flatten: new `Sessions` work root

| File | Change |
|------|--------|
| `app/controller/paths.py` | `DEFAULT_WORK_ROOT = DEFAULT_SCAN_ROOT / "PodcastInABox" / "Sessions"` |
| `scripts/piab_lib.py` | Same `DEFAULT_WORK_ROOT` |
| `scripts/piab_process_log.py` | `DEFAULT_PROCESS_LOG_ROOT` → the new Sessions folder |
| `migrate_legacy_work_files()` | Also move today’s `PodcastInABox\piab-process-log.json` (and queue/lock if present) into `Sessions\` if the dest is missing |
| GUI strings | `welcome_screen.py`, `resume_screen.py`, `clean_working_screen.py`, `autocut_screens.py` — show `...\PodcastInABox\Sessions` |
| Skill + README | Work-root sentences → `E:\PodcastRoom\PodcastInABox\Sessions` |

Then the git repo can occupy `E:\PodcastRoom\PodcastInABox` itself.

---

## 7. Suggested move procedure (when you approve)

Do not start this until you say to execute. Close the PIAB GUI first.

1. Confirm `E:\PodcastRoom\PodcastInABox\podcast-in-a-box-app` does not exist.
2. Copy the tree (preserves `.git` and secrets):

   ```powershell
   robocopy "E:\PodcastRoom\Cursor\podcast-in-a-box-app" "E:\PodcastRoom\PodcastInABox\podcast-in-a-box-app" /E /COPY:DAT /R:2 /W:2 /XD __pycache__
   ```

3. Apply the README / architecture path edits **in the new copy** (or apply them here first, then copy).
4. Open the new folder in Cursor. Run the unit tests in section 5.
5. Launch `E:\PodcastRoom\PodcastInABox\podcast-in-a-box-app\run_piab_app.bat`. Check Home, Resume (Test / Test 2 still listed via the process log), and that new sessions would be created under `E:\PodcastRoom\PodcastInABox\` **beside** `podcast-in-a-box-app`, not inside it.
6. Only after that works: delete or rename `E:\PodcastRoom\Cursor\podcast-in-a-box-app` (ask before delete). Leaving a `OLD-podcast-in-a-box-app` rename for a few days is safer than an immediate delete.

Do **not** use `git clone` alone — it would drop `.env`, the ElevenLabs key, and Frame.io oauth.

---

## 8. Verification checklist

- [ ] `python -m app.main` starts from the new folder
- [ ] Preflight still finds the ElevenLabs key and vMix preset
- [ ] Resume lists logged sessions (`Test`, `Test 2`, any special folders)
- [ ] New session create would use `E:\PodcastRoom\PodcastInABox\<date>` (not inside the repo, not `E:\PodcastRoom\<date>`)
- [ ] MultiCorder scan still looks at `E:\PodcastRoom`
- [ ] Clean Old Working Files still reads `E:\PodcastRoom\PodcastInABox\piab-process-log.json`
- [ ] Frame.io / email still see `.env` and oauth at the new repo root
- [ ] Cursor skills still apply in the new workspace
- [ ] `git status` / `git remote -v` look normal in the new folder
- [ ] Old path is retired only after the above

---

## 9. Decision needed before execute

1. **Layout:** recommended `PodcastInABox\podcast-in-a-box-app` (minimal code change) vs flatten + `Sessions\` (section 6B).
2. **When:** close the app, then copy.
3. **Old folder:** keep as rename for a few days vs delete after verify.

This file is the log of the investigation. Say when to execute and which layout to use.
