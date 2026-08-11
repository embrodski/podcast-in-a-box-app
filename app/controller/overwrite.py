"""Overwrite risk checks before PIAB harness writes."""

from __future__ import annotations

from pathlib import Path

PREP_RELATIVE_OUTPUTS = (
    "Output/1 Min Test.mp4",
    "Output/1 Min Test no offset.mp4",
    "Output/1 Min Test forced audio offset.mp4",
    "Output/full video with audio offset.mp4",
    "Input/Host Video-prepped.mp4",
    "Input/Guest Video-prepped.mp4",
    "Input/Wide Video-prepped.mp4",
    "Input/Main Prepped Audio.wav",
    "Temp/interview.dsl",
    "Temp/interview_transcript.json",
    "Temp/interview_transcript_simplified.json",
    "Temp/failed-sync-confidence.json",
)

RENDER_RELATIVE_OUTPUTS = (
    "Output/Full Interview.mp4",
    "Output/full video with audio offset.mp4",
    "Output/Full Interview.delivery.json",
    "Output/Full Interview Transcript.json",
)

RERUN_ONE_MIN_OUTPUTS = (
    "Output/1 Min Test.mp4",
    "Output/1 Min Test no offset.mp4",
    "Output/1 Min Test forced audio offset.mp4",
)


def check_overwrite_risk(action: str, working_folder: Path) -> list[Path]:
    folder = working_folder.resolve()
    rel_paths: tuple[str, ...]
    if action == "run_prep":
        rel_paths = PREP_RELATIVE_OUTPUTS
    elif action == "run_render":
        rel_paths = RENDER_RELATIVE_OUTPUTS
    elif action == "rerun_one_min":
        rel_paths = RERUN_ONE_MIN_OUTPUTS
    elif action == "init_session":
        rel_paths = ("podcast-in-a-box.json",)
    else:
        raise ValueError(f"Unknown overwrite action: {action!r}")

    existing: list[Path] = []
    for rel in rel_paths:
        path = folder / rel
        if path.is_file():
            existing.append(path)
    return existing
