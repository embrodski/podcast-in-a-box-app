#!/usr/bin/env python3
"""PIAB full interview render after 1-min approval + Estimate B confirmation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_env import load_harness_env

load_harness_env()

from harness_notify_failure import (
    FAILURE_JSON_NAME,
    FAILURE_TXT_NAME,
    notify_harness_failure,
)
from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE
from harness_podcast_autocut_render import rebuild_interview_dsl
from harness_autocut_common import render_dsl
from harness_av_sync_lib import (
    SYNC_CHOICE_FORCED_OFFSET,
    ensure_forced_offset_prep,
    full_interview_output_name,
    render_full_with_prep,
)
from podcast_flag_phrases import report_flag_timestamps_after_render
from piab_lib import (
    estimate_full_render,
    load_piab_state,
    mark_step,
    print_json,
    save_piab_state,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="PIAB full interview render.")
    parser.add_argument("working_folder", type=Path)
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Do not show a desktop alert on failure (failure files are still written).",
    )
    parser.add_argument(
        "--rebuild-dsl",
        action="store_true",
        help="Regenerate interview.dsl before render (e.g. after speaker-id swap).",
    )
    parser.add_argument(
        "--delivery-dry-run",
        action="store_true",
        help="Validate Frame.io/SMTP config and delivery settings without uploading.",
    )
    args = parser.parse_args()
    working = args.working_folder.resolve()
    state: dict | None = None
    temp = working / "Temp"
    current_step = "13_full_render"
    current_title = "Full interview render"

    try:
        state = load_piab_state(working)
        temp = Path(state["paths"]["temp"])
        for name in (FAILURE_JSON_NAME, FAILURE_TXT_NAME):
            marker = temp / name
            if marker.is_file():
                marker.unlink()
        if not state.get("interview_dsl") and not state.get("main_segment_id"):
            raise RuntimeError("Missing interview DSL / segment; run piab_run_prep.py first.")

        dur = float(state.get("source_duration_sec") or 0)
        eta = estimate_full_render(dur)
        state["estimate_full"] = eta
        mark_step(
            state,
            "11_one_min_approval",
            title="1-min test approval",
            status="completed",
        )
        mark_step(
            state,
            "18_interview_test_approval",
            title="Interview 1-min test approval",
            status="completed",
        )
        mark_step(
            state,
            "12_estimate_full",
            title="Estimate full interview render",
            status="completed",
            **eta,
        )

        output_dir = Path(state["paths"]["output"])
        temp = Path(state["paths"]["temp"])
        out_mp4 = output_dir / full_interview_output_name(state)
        simplified = temp / "interview_transcript_simplified.json"

        if args.rebuild_dsl or not Path(state.get("interview_dsl", "")).is_file():
            dsl = rebuild_interview_dsl(state)
            state["interview_dsl"] = str(dsl)
        else:
            dsl = Path(state["interview_dsl"])

        if state.get("sync_offset_choice") == SYNC_CHOICE_FORCED_OFFSET:
            prep = ensure_forced_offset_prep(state, allow_overwrite=args.allow_overwrite)
            render_full_with_prep(
                interview_dsl=dsl,
                prep=prep,
                simplified_json=simplified,
                output_mp4=out_mp4,
                segments_dir=temp / "av-sync" / "render-segments" / "forced-offset-full",
                allow_overwrite=args.allow_overwrite,
            )
        else:
            render_dsl(
                dsl,
                out_mp4,
                temp,
                max_seconds=None,
                allow_overwrite=args.allow_overwrite,
            )

        flag_summary = report_flag_timestamps_after_render(
            Path(dsl),
            temp,
            state=state,
        )
        state["flag_timestamps"] = flag_summary

        from harness_deliver_video import deliver_piab_full_interview

        state["delivery"] = deliver_piab_full_interview(
            state,
            video_path=out_mp4,
            dry_run=args.delivery_dry_run,
        )
        save_piab_state(working, state)

        state["full_interview_mp4"] = str(out_mp4)
        mark_step(
            state,
            "13_full_render",
            title="Full interview render",
            status="completed",
            output_mp4=str(out_mp4),
        )
        mark_step(
            state,
            "20_full_interview_render",
            title="Full interview render",
            status="completed",
            output_mp4=str(out_mp4),
        )
        mark_step(
            state,
            "14_done",
            title="Done",
            status="completed",
            output_mp4=str(out_mp4),
        )
        state["resume_at"] = "14_done"
        save_piab_state(working, state)
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        if state is not None:
            mark_step(
                state,
                current_step,
                title=current_title,
                status="failed",
                error=str(exc)[:4000],
            )
            state["resume_at"] = "12_estimate_full"
            save_piab_state(working, state)
            failure = notify_harness_failure(
                temp_dir=temp,
                pipeline="piab_full_render",
                step_id=current_step,
                step_title=current_title,
                exc=exc,
                working_folder=working,
                alert_title="Podcast In A Box full render failed",
                notify=not args.no_notify,
            )
            print(f"ERROR: {exc}", file=sys.stderr)
            print_json(failure)
            return 1
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(
        {
            "full_interview": str(out_mp4),
            "message": f"Full render is complete: {out_mp4}",
            "flag_timestamps_hhmmss": state.get("flag_timestamps", {}).get(
                "flag_timestamps_hhmmss", []
            ),
            "delivery": state.get("delivery"),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
