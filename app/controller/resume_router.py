"""Map podcast-in-a-box.json resume_at to GUI screen IDs."""

from __future__ import annotations

RESUME_AT_TO_SCREEN: dict[str, str] = {
    "01_scan_confirm": "C2a",
    "02_create_folder": "C3",
    "03_label_videos": "D1",
    "04_label_audio": "D2",
    "05_estimate_prep": "D4",
    "06_conversation_sync": "E1",
    "07_deroom_placeholder": "E1",
    "08_video_sync": "E1",
    "09_transcribe": "E1",
    "10_one_min_test": "E1",
    "10a_sync_offset_approval": "F2a",
    "11_one_min_approval": "F2",
    "12_estimate_full": "F3",
    "13_full_render": "F4",
    "14_done": "F5",
}


def resume_screen_for(resume_at: str | None) -> str:
    if not resume_at:
        return "A1"
    return RESUME_AT_TO_SCREEN.get(resume_at, "A1")
