"""
A/V sync confidence fallback — shared by Inkhaven harness and PIAB.

When ``sync_video_wav_replace`` falls back to start-aligned mux (low correlation),
write ``Temp/failed-sync-confidence.json``, render two 1-minute tests, and gate on
user choice before full render.

See ``docs/av-sync-confidence-fallback.md`` for the full workflow (PIAB port notes).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from episode_segments import MAIN_SEGMENT_KEY, save_segments_file, segments_path
from harness_autocut_common import render_dsl, run_cmd
from harness_episode_lib import REPO_ROOT, pick_interview_videos, step_state, utc_now_iso
from harness_overwrite_guard import refuse_overwrite
from harness_video_sync import (
    find_scope_videos,
    prepped_basename,
    prepped_wav_basename,
    synced_basename,
)

FAILED_SYNC_CONFIDENCE_JSON = "failed-sync-confidence.json"
AV_SYNC_FORCED_DIR = Path("av-sync") / "forced-offset"

ONE_MIN_NO_OFFSET = "1 Min Test no offset.mp4"
ONE_MIN_FORCED_OFFSET = "1 Min Test forced audio offset.mp4"
ONE_MIN_DEFAULT = "1 Min Test.mp4"
FULL_VIDEO_FORCED_OFFSET = "full video with audio offset.mp4"
FULL_INTERVIEW_DEFAULT = "Full Interview.mp4"

SYNC_CHOICE_START_ALIGNED = "start_aligned"
SYNC_CHOICE_FORCED_OFFSET = "forced_offset"


def failed_sync_flag_path(temp_dir: Path) -> Path:
    return temp_dir.resolve() / FAILED_SYNC_CONFIDENCE_JSON


def forced_sync_work_dir(temp_dir: Path) -> Path:
    return temp_dir.resolve() / AV_SYNC_FORCED_DIR


def load_sync_report(report_path: Path) -> dict[str, Any]:
    return json.loads(report_path.read_text(encoding="utf-8"))


def sync_report_for_synced_mp4(synced_mp4: Path) -> Path:
    return synced_mp4.with_suffix(".json")


def reports_from_synced_paths(synced_paths: list[Path]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for synced in synced_paths:
        report_path = sync_report_for_synced_mp4(synced)
        if not report_path.is_file():
            raise FileNotFoundError(f"Missing sync JSON report: {report_path}")
        report = load_sync_report(report_path)
        report["synced_mp4"] = str(synced.resolve())
        reports.append(report)
    return reports


def sync_confidence_failed(reports: list[dict[str, Any]]) -> bool:
    """True when any camera used start-aligned fallback due to weak correlation."""
    for report in reports:
        if not report.get("start_aligned"):
            continue
        if report.get("assume_start_aligned"):
            continue
        if report.get("start_aligned_fallback"):
            return True
        reason = str(report.get("start_aligned_reason", ""))
        if "below threshold" in reason:
            return True
    return False


def summarize_sync_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "video": r.get("video_path") or r.get("synced_mp4"),
            "detected_ms": r.get("correlation_lag_ms"),
            "applied_ms": r.get("lag_ms"),
            "strength": r.get("correlation_peak_strength"),
            "start_aligned": r.get("start_aligned"),
            "start_aligned_fallback": r.get("start_aligned_fallback"),
        }
        for r in reports
    ]


def write_failed_sync_confidence_flag(
    temp_dir: Path,
    reports: list[dict[str, Any]],
    *,
    scope: str = "main",
) -> Path:
    path = failed_sync_flag_path(temp_dir)
    payload = {
        "failed": True,
        "scope": scope,
        "created_at": utc_now_iso(),
        "sync_reports": summarize_sync_reports(reports),
        "raw_reports": reports,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_failed_sync_confidence_flag(temp_dir: Path) -> dict[str, Any] | None:
    path = failed_sync_flag_path(temp_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if data.get("failed") else None


def clear_failed_sync_confidence_flag(temp_dir: Path) -> None:
    path = failed_sync_flag_path(temp_dir)
    if path.is_file():
        path.unlink()


def run_sync_pass(
    video: Path,
    sync_audio: Path,
    synced_path: Path,
    *,
    force_detected_lag: bool,
    allow_overwrite: bool,
) -> dict[str, Any]:
    report_path = sync_report_for_synced_mp4(synced_path)
    refuse_overwrite(synced_path, allow_overwrite=allow_overwrite)
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "sync_video_wav_replace.py"),
        str(video.resolve()),
        str(sync_audio.resolve()),
        "-o",
        str(synced_path),
        "--json-report",
        str(report_path),
    ]
    if force_detected_lag:
        cmd.append("--force-detected-lag")
    run_cmd(cmd)
    return load_sync_report(report_path)


def prep_video_sync_variant(
    raw_dir: Path,
    sync_audio: Path,
    videos: list[Path],
    work_dir: Path,
    *,
    force_detected_lag: bool,
    allow_overwrite: bool,
) -> dict[str, Any]:
    """Sync + multicam + extract anchor WAV under ``work_dir``."""
    sync_dir = work_dir / "synced"
    prepped_dir = work_dir / "prepped"
    sync_dir.mkdir(parents=True, exist_ok=True)
    prepped_dir.mkdir(parents=True, exist_ok=True)

    synced_paths: list[Path] = []
    reports: list[dict[str, Any]] = []
    for video in videos:
        synced_path = sync_dir / synced_basename(video)
        report = run_sync_pass(
            video,
            sync_audio,
            synced_path,
            force_detected_lag=force_detected_lag,
            allow_overwrite=allow_overwrite,
        )
        reports.append(report)
        synced_paths.append(synced_path)

    mc_cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "multicam_align_trim.py"),
        "--prepped-names",
        "--out-dir",
        str(prepped_dir),
        "--json-report",
        str(work_dir / "video-sync-multicam.json"),
        *[str(p) for p in synced_paths],
    ]
    run_cmd(mc_cmd)

    prepped_paths: list[Path] = []
    for synced in synced_paths:
        prepped = prepped_dir / prepped_basename(synced.name)
        if not prepped.is_file():
            raise FileNotFoundError(f"Missing multicam output: {prepped}")
        prepped_paths.append(prepped)

    anchor_prepped = prepped_dir / prepped_basename(synced_paths[0].name)
    wav_out = prepped_dir / prepped_wav_basename(sync_audio)
    run_cmd(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "extract_mp4_audio_wav.py"),
            str(anchor_prepped),
            str(wav_out),
        ]
    )

    ben, guest, wide = pick_interview_videos([str(p) for p in prepped_paths])
    return {
        "input_dir": str(prepped_dir.resolve()),
        "temp_dir": str(work_dir.resolve()),
        "synced": [str(p.resolve()) for p in synced_paths],
        "prepped_videos": [str(ben), str(guest), str(wide)],
        "prepped_audio_wav": str(wav_out.resolve()),
        "sync_reports": reports,
        "force_detected_lag": force_detected_lag,
        "work_dir": str(work_dir.resolve()),
    }


def build_segment_entry(prep: dict[str, Any], simplified_json: Path) -> dict[str, Any]:
    ben, guest, wide = pick_interview_videos(prep["prepped_videos"])
    return {
        "audio_file": prep["prepped_audio_wav"],
        "audio_offset": 0,
        "enable_color_match": False,
        "video_files": {
            "speaker_0": {"file": str(ben), "offset": 0},
            "speaker_1": {"file": str(guest), "offset": 0},
            "wide": {"file": str(wide), "offset": 0},
        },
        "transcript_file": str(simplified_json.resolve()),
    }


def render_one_min_with_prep(
    *,
    interview_dsl: Path,
    simplified_json: Path,
    prep: dict[str, Any],
    output_mp4: Path,
    segments_dir: Path,
    allow_overwrite: bool,
) -> None:
    segments_dir.mkdir(parents=True, exist_ok=True)
    save_segments_file(
        segments_path(segments_dir),
        {MAIN_SEGMENT_KEY: build_segment_entry(prep, simplified_json)},
    )
    refuse_overwrite(output_mp4, allow_overwrite=allow_overwrite)
    render_dsl(
        interview_dsl,
        output_mp4,
        segments_dir,
        max_seconds=60,
        allow_overwrite=allow_overwrite,
    )


def render_full_with_prep(
    *,
    interview_dsl: Path,
    prep: dict[str, Any],
    simplified_json: Path,
    output_mp4: Path,
    segments_dir: Path,
    allow_overwrite: bool,
) -> None:
    segments_dir.mkdir(parents=True, exist_ok=True)
    save_segments_file(
        segments_path(segments_dir),
        {MAIN_SEGMENT_KEY: build_segment_entry(prep, simplified_json)},
    )
    refuse_overwrite(output_mp4, allow_overwrite=allow_overwrite)
    render_dsl(
        interview_dsl,
        output_mp4,
        segments_dir,
        max_seconds=None,
        allow_overwrite=allow_overwrite,
    )


def ensure_forced_offset_prep(
    state: dict[str, Any],
    *,
    allow_overwrite: bool,
) -> dict[str, Any]:
    """Build or return cached forced-offset ``main_prepped`` under Temp/av-sync."""
    if state.get("main_prepped_forced_offset"):
        return state["main_prepped_forced_offset"]

    raw_dir = Path(state["paths"]["raw"])
    temp = Path(state["paths"]["temp"])
    clean = Path(state["main_clean_audio"])
    if not clean.is_file():
        raise FileNotFoundError("main_clean_audio missing from episode state.")

    videos = find_scope_videos(raw_dir, "main")
    work_dir = forced_sync_work_dir(temp)
    prep = prep_video_sync_variant(
        raw_dir,
        clean,
        videos,
        work_dir,
        force_detected_lag=True,
        allow_overwrite=allow_overwrite,
    )
    state["main_prepped_forced_offset"] = prep
    return prep


def run_sync_ab_one_min_tests(
    state: dict[str, Any],
    *,
    allow_overwrite: bool,
) -> dict[str, Any]:
    """
    Render ``1 Min Test no offset`` and ``1 Min Test forced audio offset``.

    Assumes ``main_prepped`` (start-aligned, Input/) and ``interview.dsl`` exist.
    """
    temp = Path(state["paths"]["temp"])
    output_dir = Path(state["paths"]["output"])
    interview_dsl = Path(state.get("interview_dsl") or temp / "interview.dsl")
    simplified = temp / "interview_transcript_simplified.json"
    if not interview_dsl.is_file() or not simplified.is_file():
        raise FileNotFoundError("interview.dsl and simplified transcript required.")

    if not state.get("main_prepped"):
        raise FileNotFoundError("main_prepped missing from state.")

    forced_prep = ensure_forced_offset_prep(state, allow_overwrite=allow_overwrite)
    no_offset_mp4 = output_dir / ONE_MIN_NO_OFFSET
    forced_mp4 = output_dir / ONE_MIN_FORCED_OFFSET

    render_one_min_with_prep(
        interview_dsl=interview_dsl,
        simplified_json=simplified,
        prep=state["main_prepped"],
        output_mp4=no_offset_mp4,
        segments_dir=temp / "av-sync" / "render-segments" / "start-aligned",
        allow_overwrite=allow_overwrite,
    )
    render_one_min_with_prep(
        interview_dsl=interview_dsl,
        simplified_json=simplified,
        prep=forced_prep,
        output_mp4=forced_mp4,
        segments_dir=temp / "av-sync" / "render-segments" / "forced-offset",
        allow_overwrite=allow_overwrite,
    )

    flag = load_failed_sync_confidence_flag(temp) or {}
    flag.update(
        {
            "one_min_no_offset": str(no_offset_mp4.resolve()),
            "one_min_forced_offset": str(forced_mp4.resolve()),
            "updated_at": utc_now_iso(),
        }
    )
    failed_sync_flag_path(temp).write_text(json.dumps(flag, indent=2) + "\n", encoding="utf-8")

    state["sync_offset_choice_pending"] = True
    state["sync_offset_choice"] = None
    state["podcast_autocut_test_mp4_no_offset"] = str(no_offset_mp4)
    state["podcast_autocut_test_mp4_forced_offset"] = str(forced_mp4)

    return {
        "one_min_no_offset": str(no_offset_mp4),
        "one_min_forced_offset": str(forced_mp4),
        "forced_prep": forced_prep,
    }


def active_main_prepped(state: dict[str, Any]) -> dict[str, Any]:
    choice = state.get("sync_offset_choice")
    if choice == SYNC_CHOICE_FORCED_OFFSET and state.get("main_prepped_forced_offset"):
        return state["main_prepped_forced_offset"]
    if state.get("main_prepped"):
        return state["main_prepped"]
    raise FileNotFoundError("No active main_prepped in episode state.")


def apply_sync_offset_choice(state: dict[str, Any], choice: str) -> dict[str, Any]:
    if choice not in (SYNC_CHOICE_START_ALIGNED, SYNC_CHOICE_FORCED_OFFSET):
        raise ValueError(f"Invalid sync offset choice: {choice!r}")

    temp = Path(state["paths"]["temp"])
    simplified = temp / "interview_transcript_simplified.json"
    if choice == SYNC_CHOICE_FORCED_OFFSET:
        prep = state.get("main_prepped_forced_offset")
        if not prep:
            raise FileNotFoundError("Forced-offset prep missing; re-run A/B 1-min tests.")
    else:
        prep = state.get("main_prepped")
        if not prep:
            raise FileNotFoundError("Start-aligned main_prepped missing.")

    state["sync_offset_choice"] = choice
    state["sync_offset_choice_pending"] = False
    state["active_main_prepped_key"] = choice

    save_segments_file(
        segments_path(temp),
        {MAIN_SEGMENT_KEY: build_segment_entry(prep, simplified)},
    )

    if choice == SYNC_CHOICE_FORCED_OFFSET:
        state["podcast_autocut_test_mp4"] = state.get(
            "podcast_autocut_test_mp4_forced_offset",
            str(Path(state["paths"]["output"]) / ONE_MIN_FORCED_OFFSET),
        )
    else:
        state["podcast_autocut_test_mp4"] = state.get(
            "podcast_autocut_test_mp4_no_offset",
            str(Path(state["paths"]["output"]) / ONE_MIN_NO_OFFSET),
        )
    return prep


def mark_sync_ab_steps(state: dict[str, Any], *, ab_result: dict[str, Any]) -> None:
    steps = state.setdefault("steps", {})
    steps["18a_sync_offset_approval"] = step_state(
        steps,
        "18a_sync_offset_approval",
        title="Sync offset A/B approval",
        status="awaiting_user",
        one_min_no_offset=ab_result["one_min_no_offset"],
        one_min_forced_offset=ab_result["one_min_forced_offset"],
    )
    steps["18_interview_test_approval"] = step_state(
        steps,
        "18_interview_test_approval",
        title="Interview 1-min test approval",
        status="pending",
        note="Complete sync offset choice (step 18a) first.",
    )
    state["resume_at"] = "18a_sync_offset_approval"


def mark_sync_choice_completed(state: dict[str, Any]) -> None:
    steps = state.setdefault("steps", {})
    steps["18a_sync_offset_approval"] = step_state(
        steps,
        "18a_sync_offset_approval",
        title="Sync offset A/B approval",
        status="completed",
        choice=state.get("sync_offset_choice"),
    )
    steps["18_interview_test_approval"] = step_state(
        steps,
        "18_interview_test_approval",
        title="Interview 1-min test approval",
        status="awaiting_user",
        chosen_test=state.get("podcast_autocut_test_mp4"),
    )
    state["resume_at"] = "18_interview_test_approval"


def full_interview_output_name(state: dict[str, Any]) -> str:
    if state.get("sync_offset_choice") == SYNC_CHOICE_FORCED_OFFSET:
        return FULL_VIDEO_FORCED_OFFSET
    return FULL_INTERVIEW_DEFAULT


def maybe_write_sync_confidence_flag(
    state: dict[str, Any],
    reports: list[dict[str, Any]],
    *,
    scope: str = "main",
) -> bool:
    """Write flag and state when confidence failed. Returns True if flag written."""
    if not sync_confidence_failed(reports):
        clear_failed_sync_confidence_flag(Path(state["paths"]["temp"]))
        state["sync_confidence_failed"] = False
        return False

    temp = Path(state["paths"]["temp"])
    write_failed_sync_confidence_flag(temp, reports, scope=scope)
    state["sync_confidence_failed"] = True
    state["sync_reports"] = summarize_sync_reports(reports)
    return True
