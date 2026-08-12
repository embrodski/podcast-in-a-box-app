#!/usr/bin/env python3
"""Full-length prep (after Fast Preview approval) chained into full render."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_episode_lib import (
    combined_audio_output_name,
    find_conversation_wav_pair,
    read_elevenlabs_api_key,
    run_conversation_sync,
)
from harness_notify_failure import (
    FAILURE_JSON_NAME,
    FAILURE_TXT_NAME,
    notify_harness_failure,
)
from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE, refuse_overwrite
from harness_transcribe_prepped import _run_transcribe
from harness_av_sync_lib import (
    SYNC_CHOICE_FORCED_OFFSET,
    prep_video_sync_variant,
)
from harness_video_sync import find_scope_videos, run_video_sync
from piab_fast_preview_lib import apply_fast_preview_approval_to_state
from piab_lib import load_piab_state, mark_step, print_json, save_piab_state


def _alias_combined_as_clean(combined: Path, *, allow_overwrite: bool) -> Path:
    from harness_overwrite_guard import refuse_overwrite

    if "Combined Audio" in combined.name:
        clean_name = combined.name.replace("Combined Audio", "Clean Audio")
    else:
        clean_name = f"{combined.stem} Clean Audio{combined.suffix}"
    clean = combined.parent / clean_name
    refuse_overwrite(clean, allow_overwrite=allow_overwrite)
    shutil.copy2(combined, clean)
    return clean


def _copy_forced_prep_to_input(prep: dict, input_dir: Path, *, allow_overwrite: bool) -> dict:
    from harness_overwrite_guard import refuse_overwrite

    input_dir.mkdir(parents=True, exist_ok=True)
    copied_videos: list[str] = []
    for raw_path in prep.get("prepped_videos") or []:
        src = Path(str(raw_path))
        dest = input_dir / src.name
        refuse_overwrite(dest, allow_overwrite=allow_overwrite)
        shutil.copy2(src, dest)
        copied_videos.append(str(dest.resolve()))

    wav_src = Path(str(prep["prepped_audio_wav"]))
    wav_dest = input_dir / wav_src.name
    refuse_overwrite(wav_dest, allow_overwrite=allow_overwrite)
    shutil.copy2(wav_src, wav_dest)

    return {
        "input_dir": str(input_dir.resolve()),
        "temp_dir": prep.get("temp_dir"),
        "synced": prep.get("synced"),
        "prepped_videos": copied_videos,
        "prepped_audio_wav": str(wav_dest.resolve()),
        "sync_reports": prep.get("sync_reports"),
        "force_detected_lag": True,
    }


def run_full_prep_after_preview(
    working: Path,
    *,
    allow_overwrite: bool = False,
) -> dict:
    working = working.resolve()
    state = load_piab_state(working)
    apply_fast_preview_approval_to_state(state)

    raw = Path(state["paths"]["raw"])
    input_dir = Path(state["paths"]["input"])
    temp = Path(state["paths"]["temp"])
    temp.mkdir(parents=True, exist_ok=True)

    for name in (FAILURE_JSON_NAME, FAILURE_TXT_NAME):
        marker = temp / name
        if marker.is_file():
            marker.unlink()

    forced_only = state.get("sync_offset_choice") == SYNC_CHOICE_FORCED_OFFSET

    mark_step(state, "06_conversation_sync", title="Conversation-sync", status="in_progress")
    state["resume_at"] = "06_conversation_sync"
    save_piab_state(working, state)

    wav1, wav2 = find_conversation_wav_pair(raw, intro=False)
    combined_path = raw / combined_audio_output_name(wav1)
    refuse_overwrite(combined_path, allow_overwrite=allow_overwrite)
    combined = run_conversation_sync(wav1, wav2)
    state["main_combined_audio"] = str(combined)
    mark_step(
        state,
        "06_conversation_sync",
        title="Conversation-sync",
        status="completed",
        output=str(combined),
    )
    save_piab_state(working, state)

    mark_step(state, "07_deroom_placeholder", title="Clean audio selection", status="in_progress")
    state["resume_at"] = "07_deroom_placeholder"
    save_piab_state(working, state)
    clean = _alias_combined_as_clean(combined, allow_overwrite=allow_overwrite)
    state["main_clean_audio"] = str(clean)
    mark_step(
        state,
        "07_deroom_placeholder",
        title="Clean audio selection",
        status="completed",
        main_clean_audio=str(clean),
    )
    save_piab_state(working, state)

    mark_step(state, "08_video_sync", title="Video-sync (main)", status="in_progress")
    state["resume_at"] = "08_video_sync"
    save_piab_state(working, state)
    videos = find_scope_videos(raw, "main")

    if forced_only:
        work_dir = temp / "av-sync" / "forced-offset-full"
        prep = prep_video_sync_variant(
            raw,
            clean,
            videos,
            work_dir,
            force_detected_lag=True,
            allow_overwrite=allow_overwrite,
        )
        result = _copy_forced_prep_to_input(prep, input_dir, allow_overwrite=allow_overwrite)
        state["main_prepped_forced_offset"] = prep
    else:
        result = run_video_sync(raw, clean, videos, allow_overwrite=allow_overwrite)

    state["main_prepped"] = result
    mark_step(state, "08_video_sync", title="Video-sync (main)", status="completed", **result)
    save_piab_state(working, state)

    mark_step(state, "09_transcribe", title="Transcribe prepped WAV", status="in_progress")
    state["resume_at"] = "09_transcribe"
    save_piab_state(working, state)
    api_key = read_elevenlabs_api_key()
    wav = Path(result["prepped_audio_wav"])
    transcript = _run_transcribe(wav, api_key, allow_overwrite=allow_overwrite)
    state["main_transcript_json"] = str(transcript)
    mark_step(
        state,
        "09_transcribe",
        title="Transcribe prepped WAV",
        status="completed",
        transcript_json=str(transcript),
    )

    mark_step(
        state,
        "10_one_min_test",
        title="Podcast autocut 1-min test",
        status="skipped",
        note="Skipped — Fast Preview approval recorded.",
    )
    mark_step(
        state,
        "11_one_min_approval",
        title="1-min test approval",
        status="completed",
        from_fast_preview=True,
    )
    state["resume_at"] = "13_full_render"
    save_piab_state(working, state)

    return {
        "main_prepped": result,
        "main_transcript_json": str(transcript),
        "forced_only": forced_only,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Full prep after Fast Preview approval, then full render."
    )
    parser.add_argument("working_folder", type=Path)
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--prep-only", action="store_true", help="Stop after full prep (no render).")
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()

    working = args.working_folder.resolve()
    current_step = "13_full_prep_after_preview"
    current_title = "Full prep after Fast Preview"
    temp = working / "Temp"

    try:
        prep_result = run_full_prep_after_preview(working, allow_overwrite=args.allow_overwrite)
        if args.prep_only:
            print_json({"prep": prep_result, "resume_at": "13_full_render"})
            return 0

        from harness_episode_lib import REPO_ROOT

        render_argv = [sys.executable, str(REPO_ROOT / "scripts" / "piab_run_full_render.py"), str(working)]
        if args.allow_overwrite:
            render_argv.append("--allow-overwrite")
        if args.no_notify:
            render_argv.append("--no-notify")
        render_argv.append("--rebuild-dsl")
        proc = subprocess.run(render_argv, cwd=str(REPO_ROOT))
        return int(proc.returncode)
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        notify_harness_failure(
            temp_dir=temp,
            pipeline="piab_full_after_preview",
            step_id=current_step,
            step_title=current_title,
            exc=exc,
            working_folder=working,
            alert_title="Podcast In A Box full processing failed",
            notify=not args.no_notify,
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
