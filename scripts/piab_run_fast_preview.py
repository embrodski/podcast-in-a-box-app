#!/usr/bin/env python3
"""Run PIAB prep steps 06–10 on Fast Preview sandbox media."""

from __future__ import annotations

import argparse
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
from harness_av_sync_lib import maybe_write_sync_confidence_flag
from harness_video_sync import find_scope_videos, run_video_sync
from piab_fast_preview_lib import (
    apply_preview_paths_to_state,
    create_preview_clips,
    fast_preview_eligible,
    render_preview_one_min_test,
)
from piab_lib import load_piab_state, mark_step, print_json, save_piab_state


def _alias_combined_as_clean(combined: Path, *, allow_overwrite: bool) -> Path:
    import shutil

    from harness_overwrite_guard import refuse_overwrite

    if "Combined Audio" in combined.name:
        clean_name = combined.name.replace("Combined Audio", "Clean Audio")
    else:
        clean_name = f"{combined.stem} Clean Audio{combined.suffix}"
    clean = combined.parent / clean_name
    refuse_overwrite(clean, allow_overwrite=allow_overwrite)
    shutil.copy2(combined, clean)
    return clean


def _clear_failure_markers(temp: Path) -> None:
    for name in (FAILURE_JSON_NAME, FAILURE_TXT_NAME):
        path = temp / name
        if path.is_file():
            path.unlink()


def run_fast_preview_prep(
    working: Path,
    *,
    allow_overwrite: bool = False,
    create_clips: bool = True,
) -> dict:
    working = working.resolve()
    state = load_piab_state(working)
    canonical_raw = Path(state["paths"]["raw"])

    if not fast_preview_eligible(canonical_raw):
        raise ValueError("Session is not eligible for Fast Preview (>10 min max video).")

    if create_clips or not (working / "Preview Files").is_dir():
        create_preview_clips(working, allow_overwrite=allow_overwrite)
        state = load_piab_state(working)

    apply_preview_paths_to_state(state, working)
    raw = Path(state["paths"]["raw"])
    temp = Path(state["paths"]["temp"])
    _clear_failure_markers(temp)

    mark_step(state, "06p_conversation_sync", title="Fast Preview conversation-sync", status="in_progress")
    state["resume_at"] = "06p_conversation_sync"
    save_piab_state(working, state)

    wav1, wav2 = find_conversation_wav_pair(raw, intro=False)
    combined_path = raw / combined_audio_output_name(wav1)
    refuse_overwrite(combined_path, allow_overwrite=allow_overwrite)
    combined = run_conversation_sync(wav1, wav2)
    state["main_combined_audio"] = str(combined)
    mark_step(
        state,
        "06p_conversation_sync",
        title="Fast Preview conversation-sync",
        status="completed",
        output=str(combined),
    )
    save_piab_state(working, state)

    mark_step(state, "07p_deroom_placeholder", title="Fast Preview clean audio", status="in_progress")
    state["resume_at"] = "07p_deroom_placeholder"
    save_piab_state(working, state)
    clean = _alias_combined_as_clean(combined, allow_overwrite=allow_overwrite)
    state["main_clean_audio"] = str(clean)
    mark_step(
        state,
        "07p_deroom_placeholder",
        title="Fast Preview clean audio",
        status="completed",
        main_clean_audio=str(clean),
    )
    save_piab_state(working, state)

    mark_step(state, "08p_video_sync", title="Fast Preview video-sync", status="in_progress")
    state["resume_at"] = "08p_video_sync"
    save_piab_state(working, state)
    videos = find_scope_videos(raw, "main")
    result = run_video_sync(raw, clean, videos, allow_overwrite=allow_overwrite)
    state["main_prepped"] = result
    if result.get("sync_reports"):
        maybe_write_sync_confidence_flag(state, result["sync_reports"], scope="main")
    mark_step(state, "08p_video_sync", title="Fast Preview video-sync", status="completed", **result)
    save_piab_state(working, state)

    mark_step(state, "09p_transcribe", title="Fast Preview transcribe", status="in_progress")
    state["resume_at"] = "09p_transcribe"
    save_piab_state(working, state)
    api_key = read_elevenlabs_api_key()
    wav = Path(result["prepped_audio_wav"])
    transcript = _run_transcribe(wav, api_key, allow_overwrite=allow_overwrite)
    state["main_transcript_json"] = str(transcript)
    mark_step(
        state,
        "09p_transcribe",
        title="Fast Preview transcribe",
        status="completed",
        transcript_json=str(transcript),
    )
    save_piab_state(working, state)

    out_mp4, preview_render_mode, sync_ab_required = render_preview_one_min_test(
        state,
        working,
        allow_overwrite=allow_overwrite,
    )

    state = load_piab_state(working)
    state.setdefault("fast_preview", {})["preview_render_mode"] = preview_render_mode
    state["fast_preview"]["sync_ab_required"] = sync_ab_required
    save_piab_state(working, state)

    return {
        "preview_one_min_test": str(out_mp4),
        "preview_render_mode": preview_render_mode,
        "sync_ab_required": sync_ab_required,
        "resume_at": state.get("resume_at"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PIAB Fast Preview prep through 1-min test.")
    parser.add_argument("working_folder", type=Path)
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument(
        "--skip-create-clips",
        action="store_true",
        help="Reuse existing Preview Files/ source clips.",
    )
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()

    working = args.working_folder.resolve()
    temp = working / "Preview Files" / "Temp"
    current_step = "06p_conversation_sync"
    current_title = "Fast Preview prep"

    try:
        state = load_piab_state(working)
        steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
        for step_id, title in (
            ("10p_fast_preview_one_min", "Fast Preview 1-min test"),
            ("09p_transcribe", "Fast Preview transcribe"),
            ("08p_video_sync", "Fast Preview video-sync"),
            ("07p_deroom_placeholder", "Fast Preview clean audio"),
            ("06p_conversation_sync", "Fast Preview conversation-sync"),
        ):
            entry = steps.get(step_id) if isinstance(steps, dict) else None
            if isinstance(entry, dict) and entry.get("status") == "in_progress":
                current_step = step_id
                current_title = str(entry.get("title") or title)
                break
        else:
            resume_at = state.get("resume_at")
            if isinstance(resume_at, str) and resume_at.startswith(
                ("06p_", "07p_", "08p_", "09p_", "10p_")
            ):
                current_step = resume_at

        payload = run_fast_preview_prep(
            working,
            allow_overwrite=args.allow_overwrite,
            create_clips=not args.skip_create_clips,
        )
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        notify_harness_failure(
            temp_dir=temp,
            pipeline="piab_fast_preview",
            step_id=current_step,
            step_title=current_title,
            exc=exc,
            working_folder=working,
            alert_title="Podcast In A Box Fast Preview failed",
            notify=not args.no_notify,
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(
        {
            **payload,
            "message": (
                f"Fast Preview 1-min test ready: {payload['preview_one_min_test']}. "
                "Review on F2/F2a before full processing."
            ),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
