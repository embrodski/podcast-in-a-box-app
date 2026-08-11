"""Detect and resume interrupted Podcast In A Box prep runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from harness_av_sync_lib import (
    ONE_MIN_DEFAULT,
    ONE_MIN_FORCED_OFFSET,
    ONE_MIN_NO_OFFSET,
    load_failed_sync_confidence_flag,
)
from harness_episode_lib import BEN_HOST_RE, pick_interview_videos
from harness_notify_failure import FAILURE_JSON_NAME

PREP_STEP_ORDER: tuple[str, ...] = (
    "06_conversation_sync",
    "07_deroom_placeholder",
    "08_video_sync",
    "09_transcribe",
    "10_one_min_test",
)

PREP_STEP_TITLES: dict[str, str] = {
    "06_conversation_sync": "Conversation-sync",
    "07_deroom_placeholder": "Clean audio selection",
    "08_video_sync": "Video-sync (main)",
    "09_transcribe": "Transcribe prepped WAV",
    "10_one_min_test": "Podcast autocut 1-min test",
}

FROM_STEP_ALIASES: dict[str, str] = {
    "conversation_sync": "06_conversation_sync",
    "conversation-sync": "06_conversation_sync",
    "06": "06_conversation_sync",
    "deroom": "07_deroom_placeholder",
    "clean": "07_deroom_placeholder",
    "07": "07_deroom_placeholder",
    "video_sync": "08_video_sync",
    "video-sync": "08_video_sync",
    "08": "08_video_sync",
    "transcribe": "09_transcribe",
    "09": "09_transcribe",
    "one_min": "10_one_min_test",
    "one-min": "10_one_min_test",
    "10": "10_one_min_test",
}


@dataclass
class PrepResumePlan:
    """Which prep steps to skip and what was rehydrated from disk."""

    working_folder: Path
    start_step: str
    skipped_steps: list[str] = field(default_factory=list)
    completed_through: str | None = None
    resume_at: str | None = None
    last_failure: dict | None = None
    rehydrated: dict = field(default_factory=dict)
    ready_for_approval: bool = False

    @property
    def message(self) -> str:
        if self.ready_for_approval:
            if self.resume_at == "10a_sync_offset_approval":
                return "Sync offset A/B tests ready; waiting on user choice."
            return "1 Min Test already exists; waiting on user approval."
        if self.skipped_steps:
            skipped = ", ".join(PREP_STEP_TITLES.get(s, s) for s in self.skipped_steps)
            start = PREP_STEP_TITLES.get(self.start_step, self.start_step)
            return f"Resuming prep at {start} (skipping completed: {skipped})."
        return "Running full prep from conversation-sync."


def normalize_from_step(value: str) -> str:
    key = value.strip().lower().replace(" ", "_")
    if key in FROM_STEP_ALIASES:
        return FROM_STEP_ALIASES[key]
    if key in PREP_STEP_TITLES:
        return key
    raise ValueError(
        f"Unknown --from-step {value!r}. "
        f"Use one of: {', '.join(sorted(set(FROM_STEP_ALIASES)))} "
        f"or step ids {', '.join(PREP_STEP_ORDER)}."
    )


def _step_index(step_id: str) -> int:
    try:
        return PREP_STEP_ORDER.index(step_id)
    except ValueError as exc:
        raise ValueError(f"Not a prep step id: {step_id}") from exc


def _find_combined_audio(raw: Path) -> Path | None:
    for path in sorted(raw.glob("*Combined Audio.wav")):
        if path.is_file():
            return path
    return None


def _find_clean_audio(raw: Path) -> Path | None:
    for path in sorted(raw.glob("*Clean Audio.wav")):
        if path.is_file() and "Combined" not in path.name:
            return path
    return None


def transcript_path_for_wav(wav: Path) -> Path:
    return wav.parent / f"{wav.stem} Transcript.json"


def rehydrate_main_prepped(state: dict) -> dict | None:
    """Rebuild ``main_prepped`` from Input when state lost it but files remain."""
    input_dir = Path(state["paths"]["input"])
    temp_dir = Path(state["paths"]["temp"])
    if not input_dir.is_dir():
        return None

    prepped_videos = sorted(
        (p for p in input_dir.iterdir() if p.is_file() and p.name.endswith("-prepped.mp4")),
        key=lambda p: p.name.lower(),
    )
    if len(prepped_videos) < 3:
        return None
    try:
        pick_interview_videos([str(p) for p in prepped_videos])
    except FileNotFoundError:
        return None

    prepped_wavs = sorted(
        (p for p in input_dir.iterdir() if p.is_file() and p.name.endswith("-prepped.wav")),
        key=lambda p: p.name.lower(),
    )
    if not prepped_wavs:
        return None
    anchor = next((w for w in prepped_wavs if BEN_HOST_RE.search(w.name)), prepped_wavs[0])
    return {
        "input_dir": str(input_dir.resolve()),
        "temp_dir": str(temp_dir.resolve()),
        "prepped_videos": [str(p.resolve()) for p in prepped_videos],
        "prepped_audio_wav": str(anchor.resolve()),
    }


def _read_last_failure(temp: Path) -> dict | None:
    path = temp / FAILURE_JSON_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def detect_prep_completion(state: dict) -> dict[str, bool]:
    """Return which prep steps have usable artifacts on disk."""
    raw = Path(state["paths"]["raw"])
    input_dir = Path(state["paths"]["input"])
    output_dir = Path(state["paths"]["output"])
    temp = Path(state["paths"]["temp"])

    combined = None
    if state.get("main_combined_audio") and Path(state["main_combined_audio"]).is_file():
        combined = Path(state["main_combined_audio"])
    else:
        combined = _find_combined_audio(raw)

    clean = None
    if state.get("main_clean_audio") and Path(state["main_clean_audio"]).is_file():
        clean = Path(state["main_clean_audio"])
    else:
        clean = _find_clean_audio(raw)

    main_prepped = state.get("main_prepped")
    if not main_prepped or not main_prepped.get("prepped_audio_wav"):
        rebuilt = rehydrate_main_prepped(state)
        if rebuilt:
            main_prepped = rebuilt

    transcript_json = None
    if state.get("main_transcript_json") and Path(state["main_transcript_json"]).is_file():
        transcript_json = Path(state["main_transcript_json"])
    elif main_prepped and main_prepped.get("prepped_audio_wav"):
        candidate = transcript_path_for_wav(Path(main_prepped["prepped_audio_wav"]))
        if candidate.is_file():
            transcript_json = candidate

    temp = Path(state["paths"]["temp"])
    sync_flag = load_failed_sync_confidence_flag(temp)
    ab_no_offset = output_dir / ONE_MIN_NO_OFFSET
    ab_forced = output_dir / ONE_MIN_FORCED_OFFSET
    one_min = output_dir / ONE_MIN_DEFAULT
    one_min_complete = (
        (ab_no_offset.is_file() and ab_forced.is_file())
        if sync_flag
        else one_min.is_file()
    )

    return {
        "06_conversation_sync": combined is not None,
        "07_deroom_placeholder": clean is not None,
        "08_video_sync": main_prepped is not None,
        "09_transcribe": transcript_json is not None,
        "10_one_min_test": one_min_complete,
    }


def apply_rehydration(state: dict, completion: dict[str, bool]) -> dict:
    """Patch ``state`` fields from disk-detected artifacts; return keys updated."""
    updated: dict = {}
    raw = Path(state["paths"]["raw"])

    if completion["06_conversation_sync"]:
        combined = (
            Path(state["main_combined_audio"])
            if state.get("main_combined_audio") and Path(state["main_combined_audio"]).is_file()
            else _find_combined_audio(raw)
        )
        if combined is not None:
            state["main_combined_audio"] = str(combined.resolve())
            updated["main_combined_audio"] = state["main_combined_audio"]

    if completion["07_deroom_placeholder"]:
        clean = (
            Path(state["main_clean_audio"])
            if state.get("main_clean_audio") and Path(state["main_clean_audio"]).is_file()
            else _find_clean_audio(raw)
        )
        if clean is not None:
            state["main_clean_audio"] = str(clean.resolve())
            updated["main_clean_audio"] = state["main_clean_audio"]

    if completion["08_video_sync"]:
        if not state.get("main_prepped") or not state["main_prepped"].get("prepped_audio_wav"):
            rebuilt = rehydrate_main_prepped(state)
            if rebuilt:
                state["main_prepped"] = rebuilt
                updated["main_prepped"] = rebuilt

    if completion["09_transcribe"]:
        transcript = None
        if state.get("main_transcript_json") and Path(state["main_transcript_json"]).is_file():
            transcript = Path(state["main_transcript_json"])
        elif state.get("main_prepped", {}).get("prepped_audio_wav"):
            candidate = transcript_path_for_wav(Path(state["main_prepped"]["prepped_audio_wav"]))
            if candidate.is_file():
                transcript = candidate
        if transcript is not None:
            state["main_transcript_json"] = str(transcript.resolve())
            updated["main_transcript_json"] = state["main_transcript_json"]

    if completion["10_one_min_test"]:
        output_dir = Path(state["paths"]["output"])
        temp = Path(state["paths"]["temp"])
        sync_flag = load_failed_sync_confidence_flag(temp)
        if sync_flag:
            no_offset = output_dir / ONE_MIN_NO_OFFSET
            forced = output_dir / ONE_MIN_FORCED_OFFSET
            if no_offset.is_file():
                state["podcast_autocut_test_mp4_no_offset"] = str(no_offset.resolve())
                updated["podcast_autocut_test_mp4_no_offset"] = state[
                    "podcast_autocut_test_mp4_no_offset"
                ]
            if forced.is_file():
                state["podcast_autocut_test_mp4_forced_offset"] = str(forced.resolve())
                updated["podcast_autocut_test_mp4_forced_offset"] = state[
                    "podcast_autocut_test_mp4_forced_offset"
                ]
            choice = state.get("sync_offset_choice")
            chosen = None
            if choice == "forced_offset" and forced.is_file():
                chosen = forced
            elif choice == "start_aligned" and no_offset.is_file():
                chosen = no_offset
            if chosen is not None:
                state["podcast_autocut_test_mp4"] = str(chosen.resolve())
                updated["podcast_autocut_test_mp4"] = state["podcast_autocut_test_mp4"]
        else:
            out_mp4 = output_dir / ONE_MIN_DEFAULT
            if out_mp4.is_file():
                state["podcast_autocut_test_mp4"] = str(out_mp4.resolve())
                updated["podcast_autocut_test_mp4"] = state["podcast_autocut_test_mp4"]

    return updated


def _pending_sync_offset_choice(state: dict, temp: Path) -> bool:
    if state.get("sync_offset_choice_pending"):
        return True
    if load_failed_sync_confidence_flag(temp) and not state.get("sync_offset_choice"):
        return True
    resume_at = state.get("resume_at")
    if resume_at == "10a_sync_offset_approval":
        return True
    step = state.get("steps", {}).get("10a_sync_offset_approval", {})
    return isinstance(step, dict) and step.get("status") == "awaiting_user"


def _infer_start_from_failure(last_failure: dict | None) -> str | None:
    if not last_failure:
        return None
    step_id = last_failure.get("step_id")
    if isinstance(step_id, str) and step_id in PREP_STEP_TITLES:
        return step_id
    return None


def build_prep_resume_plan(
    state: dict,
    working_folder: Path,
    *,
    resume: bool = False,
    from_step: str | None = None,
) -> PrepResumePlan:
    """
    Decide where prep should restart.

    ``resume=True`` uses failed-step marker, ``resume_at``, and on-disk artifacts.
    ``from_step`` forces a start point (still requires earlier artifacts to exist).
    """
    working_folder = working_folder.resolve()
    temp = Path(state["paths"]["temp"])
    completion = detect_prep_completion(state)
    rehydrated = apply_rehydration(state, completion)
    last_failure = _read_last_failure(temp)

    if completion["10_one_min_test"] and not from_step:
        if _pending_sync_offset_choice(state, temp):
            return PrepResumePlan(
                working_folder=working_folder,
                start_step="10_one_min_test",
                skipped_steps=list(PREP_STEP_ORDER),
                completed_through="10_one_min_test",
                resume_at="10a_sync_offset_approval",
                last_failure=last_failure,
                rehydrated=rehydrated,
                ready_for_approval=True,
            )
        resume_at = state.get("resume_at")
        if isinstance(resume_at, str) and resume_at in (
            "10a_sync_offset_approval",
            "11_one_min_approval",
        ):
            target = resume_at
        else:
            target = "11_one_min_approval"
        return PrepResumePlan(
            working_folder=working_folder,
            start_step="11_one_min_approval",
            skipped_steps=list(PREP_STEP_ORDER),
            completed_through="10_one_min_test",
            resume_at=target,
            last_failure=last_failure,
            rehydrated=rehydrated,
            ready_for_approval=True,
        )

    if from_step:
        start_step = normalize_from_step(from_step)
    elif resume:
        start_step = _infer_start_from_failure(last_failure)
        if start_step is None:
            resume_at = state.get("resume_at")
            if isinstance(resume_at, str) and resume_at in PREP_STEP_TITLES:
                start_step = resume_at
            else:
                # First incomplete step from artifacts.
                start_step = PREP_STEP_ORDER[0]
                for step_id in PREP_STEP_ORDER:
                    if not completion[step_id]:
                        start_step = step_id
                        break
                else:
                    start_step = PREP_STEP_ORDER[-1]
    else:
        start_step = PREP_STEP_ORDER[0]

    start_idx = _step_index(start_step)

    # Ensure prior steps have artifacts when resuming mid-pipeline.
    missing: list[str] = []
    for step_id in PREP_STEP_ORDER[:start_idx]:
        if not completion[step_id]:
            missing.append(PREP_STEP_TITLES[step_id])
    if missing:
        raise FileNotFoundError(
            "Cannot resume prep at "
            f"{PREP_STEP_TITLES[start_step]}: missing outputs from "
            + ", ".join(missing)
            + ". Re-run from an earlier step with --from-step or complete prep without --resume."
        )

    skipped = [step_id for step_id in PREP_STEP_ORDER if _step_index(step_id) < start_idx]
    completed_through = PREP_STEP_ORDER[start_idx - 1] if start_idx > 0 else None

    return PrepResumePlan(
        working_folder=working_folder,
        start_step=start_step,
        skipped_steps=skipped,
        completed_through=completed_through,
        resume_at=state.get("resume_at") if isinstance(state.get("resume_at"), str) else None,
        last_failure=last_failure,
        rehydrated=rehydrated,
    )


def is_prep_resumable(state: dict, working_folder: Path) -> bool:
    """True when prep can continue without redoing everything from scratch."""
    try:
        plan = build_prep_resume_plan(state, working_folder, resume=True)
    except FileNotFoundError:
        return False
    return bool(plan.skipped_steps) or plan.last_failure is not None


def plan_to_json(plan: PrepResumePlan) -> dict:
    return {
        "working_folder": str(plan.working_folder),
        "start_step": plan.start_step,
        "start_step_title": PREP_STEP_TITLES.get(plan.start_step, plan.start_step),
        "skipped_steps": plan.skipped_steps,
        "completed_through": plan.completed_through,
        "ready_for_approval": plan.ready_for_approval,
        "resume_at": plan.resume_at,
        "last_failure": plan.last_failure,
        "rehydrated": plan.rehydrated,
        "message": plan.message,
    }
