"""Map podcast-in-a-box.json resume_at to GUI screen IDs."""

from __future__ import annotations

from app.controller.fast_preview import full_after_preview_pending

RESUME_AT_TO_SCREEN: dict[str, str] = {
    "01_scan_confirm": "C2a",
    "02_create_folder": "C3",
    "03_label_videos": "D1",
    "04_label_audio": "D2",
    "04a_apply_labels": "D3",
    "05_estimate_prep": "D4",
    "06_conversation_sync": "E1",
    "07_deroom_placeholder": "E1",
    "08_video_sync": "E1",
    "09_transcribe": "E1",
    "10_one_min_test": "E1",
    "06p_conversation_sync": "E1",
    "07p_deroom_placeholder": "E1",
    "08p_video_sync": "E1",
    "09p_transcribe": "E1",
    "10p_fast_preview_one_min": "E1",
    "13_queued_full": "F4",
    "13_full_prep_after_preview": "F4",
    "13_full_render": "F4",
    "13_output_transcripts": "F4",
    "13_delivery": "F4",
    "10a_sync_offset_approval": "F2a",
    "11_one_min_approval": "F2",
    "12_estimate_full": "F3",
    "14_done": "F5",
    "cleaned": "A1",
}


def resume_screen_for(resume_at: str | None, state: dict | None = None) -> str:
    if state and full_after_preview_pending(state):
        return "F4"
    if not resume_at:
        return "A1"
    return RESUME_AT_TO_SCREEN.get(resume_at, "A1")
