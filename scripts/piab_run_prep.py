#!/usr/bin/env python3

"""

PIAB prep: conversation-sync → Combined-as-Clean (DeRoom placeholder) →

video-sync → transcribe → podcast autocut 1-min test.



Use ``--resume`` to skip completed steps (e.g. after transcribe billing failure).

"""



from __future__ import annotations



import argparse

import shutil

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
    ONE_MIN_DEFAULT,
    load_failed_sync_confidence_flag,
    maybe_write_sync_confidence_flag,
    run_sync_ab_one_min_tests,
)
from harness_video_sync import find_scope_videos, run_video_sync

from piab_lib import load_piab_state, mark_piab_sync_ab_steps, mark_step, print_json, save_piab_state

from piab_resume import (

    PREP_STEP_ORDER,

    PREP_STEP_TITLES,

    PrepResumePlan,

    build_prep_resume_plan,

    plan_to_json,

)





def _alias_combined_as_clean(

    combined: Path,

    *,

    allow_overwrite: bool,

) -> Path:

    """DeRoom placeholder: copy Combined → Clean so video-sync can proceed."""

    name = combined.name

    if "Combined Audio" in name:

        clean_name = name.replace("Combined Audio", "Clean Audio")

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





def _should_run(step_id: str, plan: PrepResumePlan) -> bool:

    if plan.ready_for_approval:

        return False

    return PREP_STEP_ORDER.index(step_id) >= PREP_STEP_ORDER.index(plan.start_step)

def _begin_prep_step(state: dict, working: Path, step_id: str, title: str) -> None:
    mark_step(state, step_id, title=title, status="in_progress")
    state["resume_at"] = step_id
    save_piab_state(working, state)


def _finish_prep_step(state: dict, working: Path) -> None:
    save_piab_state(working, state)


def _handle_failure(

    *,

    state: dict,

    working: Path,

    temp: Path,

    step_id: str,

    step_title: str,

    exc: BaseException,

    notify: bool,

) -> int:

    mark_step(

        state,

        step_id,

        title=step_title,

        status="failed",

        error=str(exc)[:4000],

    )

    state["resume_at"] = step_id

    save_piab_state(working, state)

    failure = notify_harness_failure(

        temp_dir=temp,

        pipeline="piab_prep",

        step_id=step_id,

        step_title=step_title,

        exc=exc,

        working_folder=working,

        alert_title="Podcast In A Box prep failed",

        notify=notify,

    )

    print(f"ERROR: {exc}", file=sys.stderr)

    print_json(failure)

    return 1





def _render_one_min_test(

    state: dict,

    working: Path,

    *,

    allow_overwrite: bool,

) -> Path:

    from harness_autocut_common import render_dsl, run_cmd

    from episode_segments import MAIN_SEGMENT_KEY, segments_path, upsert_segment

    from harness_episode_lib import REPO_ROOT, pick_interview_videos, podcast_phrase_cli_args, podcast_swap_speaker_ids_cli_args



    temp = Path(state["paths"]["temp"])

    output_dir = Path(state["paths"]["output"])

    temp.mkdir(parents=True, exist_ok=True)

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



    segment_id = MAIN_SEGMENT_KEY

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

    parser = argparse.ArgumentParser(description="PIAB prep through 1-min test.")

    parser.add_argument("working_folder", type=Path)

    parser.add_argument("--allow-overwrite", action="store_true")

    parser.add_argument(

        "--resume",

        action="store_true",

        help="Skip prep steps already completed on disk (after failure or interrupt).",

    )

    parser.add_argument(

        "--from-step",

        help=(

            "Force prep to start at this step (aliases: transcribe, video_sync, "

            "one_min, or step ids 06-10). Implies --resume."

        ),

    )

    parser.add_argument(

        "--use-clean-pair",

        action="store_true",

        help=(

            "One-off exception: run conversation-sync on Raw/Host Clean Audio.wav "

            "and Raw/Guest Clean Audio.wav, then use the combined output directly "

            "as the clean master."

        ),

    )

    parser.add_argument(

        "--skip-one-min",

        action="store_true",

        help="Stop after transcript (do not render 1 Min Test).",

    )

    parser.add_argument(

        "--no-notify",

        action="store_true",

        help="Do not show a desktop alert on failure (failure files are still written).",

    )

    args = parser.parse_args()

    working = args.working_folder.resolve()

    state: dict | None = None

    temp = working / "Temp"

    current_step = "05_estimate_prep"

    current_title = "Estimate prep through 1-min test"

    out_mp4: Path | None = None



    try:

        state = load_piab_state(working)

        temp = Path(state["paths"]["temp"])

        _clear_failure_markers(temp)

        raw = Path(state["paths"]["raw"])



        plan = build_prep_resume_plan(

            state,

            working,

            resume=args.resume or bool(args.from_step),

            from_step=args.from_step,

        )

        if plan.rehydrated:

            save_piab_state(working, state)

        if plan.skipped_steps or plan.ready_for_approval:

            print_json({"resume_plan": plan_to_json(plan)})



        if plan.ready_for_approval:

            out_mp4 = Path(state["podcast_autocut_test_mp4"])

            print_json(

                {

                    "one_min_test": str(out_mp4),

                    "message": (

                        f"1 Min Test already exists: {out_mp4}. "

                        "Stop and wait for user approval."

                    ),

                    "state_path": state["paths"]["state"],

                    "resume_plan": plan_to_json(plan),

                }

            )

            return 0



        mark_step(

            state,

            "05_estimate_prep",

            title="Estimate prep through 1-min test",

            status="completed",

        )



        combined: Path | None = None

        clean: Path | None = None

        result: dict | None = None



        if _should_run("06_conversation_sync", plan):

            current_step = "06_conversation_sync"

            current_title = PREP_STEP_TITLES[current_step]

            _begin_prep_step(state, working, current_step, current_title)

            if args.use_clean_pair:

                wav1 = raw / "Host Clean Audio.wav"

                wav2 = raw / "Guest Clean Audio.wav"

                missing = [str(path) for path in (wav1, wav2) if not path.is_file()]

                if missing:

                    raise FileNotFoundError(

                        "Missing requested one-off clean audio source(s): "

                        + ", ".join(missing)

                    )

            else:

                wav1, wav2 = find_conversation_wav_pair(raw, intro=False)

            combined_path = raw / combined_audio_output_name(wav1)

            refuse_overwrite(combined_path, allow_overwrite=args.allow_overwrite)

            combined = run_conversation_sync(wav1, wav2)

            state["main_combined_audio"] = str(combined)

            mark_step(

                state,

                "06_conversation_sync",

                title=current_title,

                status="completed",

                wav1=wav1.name,

                wav2=wav2.name,

                output=str(combined),

            )

            _finish_prep_step(state, working)

        else:

            combined = Path(state["main_combined_audio"])



        if _should_run("07_deroom_placeholder", plan):

            current_step = "07_deroom_placeholder"

            current_title = PREP_STEP_TITLES[current_step]

            _begin_prep_step(state, working, current_step, current_title)

            if combined is None:

                raise FileNotFoundError("Missing combined audio for clean-audio step.")

            if args.use_clean_pair:

                clean = combined

                deroom_note = (

                    "One-off exception: conversation-sync used Host/Guest Clean Audio "

                    "exports; the combined output is the clean master."

                )

            else:

                clean = _alias_combined_as_clean(

                    combined, allow_overwrite=args.allow_overwrite

                )

                deroom_note = (

                    "Future: real DeRoom. For now Combined Audio is copied to Clean Audio."

                )

            state["main_clean_audio"] = str(clean)

            mark_step(

                state,

                "07_deroom_placeholder",

                title=current_title,

                status="completed",

                skipped_real_deroom=not args.use_clean_pair,

                used_clean_pair=args.use_clean_pair,

                main_clean_audio=str(clean),

                note=deroom_note,

            )

            _finish_prep_step(state, working)

        else:

            clean = Path(state["main_clean_audio"])



        if _should_run("08_video_sync", plan):

            current_step = "08_video_sync"

            current_title = PREP_STEP_TITLES[current_step]

            _begin_prep_step(state, working, current_step, current_title)

            if clean is None:

                raise FileNotFoundError("Missing clean audio for video-sync.")

            videos = find_scope_videos(raw, "main")

            result = run_video_sync(

                raw,

                clean,

                videos,

                allow_overwrite=args.allow_overwrite,

            )

            state["main_prepped"] = result

            if result.get("sync_reports"):

                maybe_write_sync_confidence_flag(state, result["sync_reports"], scope="main")

            mark_step(

                state,

                "08_video_sync",

                title=current_title,

                status="completed",

                **result,

            )

            _finish_prep_step(state, working)

        else:

            result = state["main_prepped"]



        if _should_run("09_transcribe", plan):

            current_step = "09_transcribe"

            current_title = PREP_STEP_TITLES[current_step]

            _begin_prep_step(state, working, current_step, current_title)

            if not result or not result.get("prepped_audio_wav"):

                raise FileNotFoundError("Missing prepped audio for transcription.")

            api_key = read_elevenlabs_api_key()

            wav = Path(result["prepped_audio_wav"])

            transcript = _run_transcribe(wav, api_key, allow_overwrite=args.allow_overwrite)

            state["main_transcript_json"] = str(transcript)

            mark_step(

                state,

                "09_transcribe",

                title=current_title,

                status="completed",

                wav=str(wav),

                transcript_json=str(transcript),

            )

            save_piab_state(working, state)

        elif state.get("main_transcript_json"):

            mark_step(

                state,

                "09_transcribe",

                title=PREP_STEP_TITLES["09_transcribe"],

                status="completed",

                resumed=True,

                transcript_json=state["main_transcript_json"],

            )



        if args.skip_one_min:

            state["resume_at"] = "10_one_min_test"

            save_piab_state(working, state)

            print_json(state)

            return 0



        if _should_run("10_one_min_test", plan):

            current_step = "10_one_min_test"

            current_title = PREP_STEP_TITLES[current_step]

            _begin_prep_step(state, working, current_step, current_title)

            out_mp4 = _render_one_min_test(

                state,

                working,

                allow_overwrite=args.allow_overwrite,

            )

        else:

            out_mp4 = Path(state["paths"]["output"]) / "1 Min Test.mp4"



    except HarnessOverwriteError:

        return OVERWRITE_EXIT_CODE

    except (FileNotFoundError, RuntimeError, ValueError) as exc:

        if state is not None:

            return _handle_failure(

                state=state,

                working=working,

                temp=temp,

                step_id=current_step,

                step_title=current_title,

                exc=exc,

                notify=not args.no_notify,

            )

        print(f"ERROR: {exc}", file=sys.stderr)

        return 1



    print_json(

        {

            "one_min_test": str(out_mp4),

            "message": (

                f"1 Min Test is ready for review: {out_mp4}. "

                "Stop and wait for user approval. If Host/Guest audio or cameras "
                "sound swapped in the edit (Raw files labeled correctly), run "
                "piab_fix_audio_speaker_swap.py. If Raw Host/Guest files were "
                "mislabeled during labeling, use piab_swap.py --files instead."

            ),

            "state_path": state["paths"]["state"],

        }

    )

    return 0





if __name__ == "__main__":

    raise SystemExit(main())


