#!/usr/bin/env python3
"""Re-render PIAB 1 Min Test after approval-loop fixes (e.g. speaker-id swap)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from episode_segments import MAIN_SEGMENT_KEY, segments_path, upsert_segment
from harness_autocut_common import render_dsl, run_cmd
from harness_episode_lib import REPO_ROOT, pick_interview_videos, podcast_phrase_cli_args, podcast_swap_speaker_ids_cli_args
from harness_av_sync_lib import (
    ONE_MIN_DEFAULT,
    load_failed_sync_confidence_flag,
    run_sync_ab_one_min_tests,
)
from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE, refuse_overwrite
from piab_lib import load_piab_state, mark_piab_sync_ab_steps, mark_step, print_json, save_piab_state


def rerun_one_min_test(working_folder: Path, *, allow_overwrite: bool = False) -> Path:
    """Reconvert transcript, regenerate DSL, and render 1 Min Test from existing Input."""
    working = working_folder.resolve()
    state = load_piab_state(working)
    temp = Path(state["paths"]["temp"])
    output_dir = Path(state["paths"]["output"])
    ben, guest, wide = pick_interview_videos(state["main_prepped"]["prepped_videos"])
    audio_wav = Path(state["main_prepped"]["prepped_audio_wav"])
    detail_json = Path(state["main_transcript_json"])
    simplified = temp / "interview_transcript_simplified.json"
    interview_dsl = temp / "interview.dsl"
    for path in (simplified, interview_dsl):
        refuse_overwrite(path, allow_overwrite=allow_overwrite)

    convert_cmd = [
        sys.executable,
        str(REPO_ROOT / "convert_transcript_json.py"),
        str(detail_json),
        "-o",
        str(simplified),
    ]
    convert_cmd.extend(podcast_swap_speaker_ids_cli_args(state))
    run_cmd(convert_cmd)

    segment_id = state.get("main_segment_id") or MAIN_SEGMENT_KEY
    upsert_segment(
        temp,
        segment_id,
        {
            "audio_file": str(audio_wav),
            "audio_offset": 0,
            "enable_color_match": False,
            "video_files": {
                "speaker_0": {"file": str(ben), "offset": 0},
                "speaker_1": {"file": str(guest), "offset": 0},
                "wide": {"file": str(wide), "offset": 0},
            },
            "transcript_file": str(simplified),
        },
        allow_overwrite=allow_overwrite,
    )
    state["main_segment_id"] = segment_id
    state["segments_file"] = str(segments_path(temp))
    run_cmd(
        [
            sys.executable,
            str(REPO_ROOT / "generate_full_dsl.py"),
            str(simplified),
            "--segment",
            segment_id,
            "--output",
            str(interview_dsl),
            *podcast_phrase_cli_args(state),
        ]
    )
    state["interview_dsl"] = str(interview_dsl)

    sync_flag = load_failed_sync_confidence_flag(temp)
    if sync_flag is None and state.get("sync_confidence_failed"):
        sync_flag = {"failed": True}

    if sync_flag:
        ab_result = run_sync_ab_one_min_tests(state, allow_overwrite=allow_overwrite)
        mark_piab_sync_ab_steps(state, ab_result=ab_result)
        out_mp4 = Path(ab_result["one_min_no_offset"])
    else:
        out_mp4 = output_dir / ONE_MIN_DEFAULT
        render_dsl(
            interview_dsl,
            out_mp4,
            temp,
            max_seconds=60,
            allow_overwrite=allow_overwrite,
        )
        state["podcast_autocut_test_mp4"] = str(out_mp4)
        mark_step(
            state,
            "11_one_min_approval",
            title="1-min test approval",
            status="awaiting_user",
        )
        state["resume_at"] = "11_one_min_approval"

    mark_step(
        state,
        "10_one_min_test",
        title="Podcast autocut 1-min test",
        status="completed",
        output_mp4=str(out_mp4),
        sync_ab=bool(sync_flag),
    )
    save_piab_state(working, state)
    return out_mp4


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-render PIAB 1 Min Test.")
    parser.add_argument("working_folder", type=Path)
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()

    try:
        out_mp4 = rerun_one_min_test(args.working_folder, allow_overwrite=args.allow_overwrite)
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    state = load_piab_state(args.working_folder)
    print_json({"one_min_test": str(out_mp4), "swap_speaker_ids": state.get("swap_speaker_ids")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
