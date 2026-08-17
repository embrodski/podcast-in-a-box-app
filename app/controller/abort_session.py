"""Record user abort in podcast-in-a-box.json for later resume."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.controller.prep_progress import FAST_PREVIEW_STEP_ORDER, PREP_STEP_ORDER
from app.controller.render_progress import RENDER_PHASE_ORDER
from app.controller.session_store import load_session_state, save_session_state

_KNOWN_STEP_ORDER: tuple[str, ...] = (
    *FAST_PREVIEW_STEP_ORDER,
    *PREP_STEP_ORDER,
    "10a_sync_offset_approval",
    "11_one_min_approval",
    "12_estimate_full",
    *RENDER_PHASE_ORDER,
    "14_done",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _step_title(steps: dict, step_id: str) -> str:
    entry = steps.get(step_id)
    if isinstance(entry, dict):
        title = entry.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return step_id


def _is_done(status: str | None) -> bool:
    return status in {"completed", "skipped"}


def find_last_completed_step(state: dict) -> dict[str, str] | None:
    steps = state.get("steps")
    if not isinstance(steps, dict):
        return None

    last: dict[str, str] | None = None
    for step_id in _KNOWN_STEP_ORDER:
        entry = steps.get(step_id)
        if not isinstance(entry, dict):
            continue
        if not _is_done(entry.get("status")):
            continue
        last = {
            "id": str(entry.get("id") or step_id),
            "title": _step_title(steps, step_id),
            "status": str(entry.get("status")),
        }

    if last is not None:
        return last

    # Fall back to any completed step by completed_at.
    best: tuple[str, dict[str, str]] | None = None
    for step_id, entry in steps.items():
        if not isinstance(entry, dict) or not _is_done(entry.get("status")):
            continue
        stamp = str(entry.get("completed_at") or "")
        row = {
            "id": str(entry.get("id") or step_id),
            "title": _step_title(steps, step_id),
            "status": str(entry.get("status")),
        }
        if best is None or stamp >= best[0]:
            best = (stamp, row)
    return best[1] if best else None


def find_interrupted_step(state: dict) -> str | None:
    steps = state.get("steps")
    if not isinstance(steps, dict):
        steps = {}

    for step_id in _KNOWN_STEP_ORDER:
        entry = steps.get(step_id)
        if isinstance(entry, dict) and entry.get("status") == "in_progress":
            return step_id

    resume_at = state.get("resume_at")
    if isinstance(resume_at, str) and resume_at.strip():
        entry = steps.get(resume_at)
        status = entry.get("status") if isinstance(entry, dict) else None
        if not _is_done(status) and status != "aborted":
            return resume_at
    return None


def record_session_abort(
    working_folder: Path,
    *,
    message: str = "Aborted by user.",
) -> dict[str, Any]:
    """
    Stop the session workflow marker and log resume info.

    Marks any in-progress step as aborted, sets resume_at to that step (or the
    last completed step), and writes ``last_abort`` for operators / resume UI.
    Syncs the app-wide process log via save_session_state.
    """
    folder = working_folder.resolve()
    state = load_session_state(folder)
    steps = state.setdefault("steps", {})
    if not isinstance(steps, dict):
        steps = {}
        state["steps"] = steps

    last_completed = find_last_completed_step(state)
    interrupted = find_interrupted_step(state)
    stamp = _utc_now_iso()

    if interrupted:
        entry = steps.get(interrupted) if isinstance(steps.get(interrupted), dict) else {}
        title = str(entry.get("title") or interrupted)
        steps[interrupted] = {
            **entry,
            "id": interrupted,
            "title": title,
            "status": "aborted",
            "aborted_at": stamp,
            "error": message[:2000],
        }
        if (
            interrupted in PREP_STEP_ORDER
            and (state.get("fast_preview_approval") or {}).get("approved_at")
        ):
            state["resume_at"] = "13_full_prep_after_preview"
        else:
            state["resume_at"] = interrupted
    elif last_completed is not None:
        state["resume_at"] = last_completed["id"]

    payload: dict[str, Any] = {
        "aborted_at": stamp,
        "message": message,
        "last_completed_step": last_completed,
        "interrupted_step": interrupted,
        "resume_at": state.get("resume_at"),
    }
    state["last_abort"] = payload
    save_session_state(folder, state)
    return payload
