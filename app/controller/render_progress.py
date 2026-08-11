"""Read full-render progress from session state on disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.controller.prep_progress import (
    _format_local_time,
    _format_remaining,
    _parse_state_timestamp,
    failure_summary,
    read_prep_failure,
)

RENDER_STEP_ID = "13_full_render"
RENDER_STEP_LABEL = "Rendering full interview"


@dataclass
class RenderProgress:
    resume_at: str | None = None
    current_label: str = "Starting…"
    step_started_display: str | None = None
    step_eta_display: str | None = None
    step_lines: list[str] = field(default_factory=list)
    render_complete: bool = False
    failure: dict | None = None


def _render_estimate_sec(state: dict) -> int | None:
    estimate = state.get("estimate_full")
    if not isinstance(estimate, dict):
        return None
    center = estimate.get("center_sec")
    if isinstance(center, (int, float)):
        return int(center)
    return None


def _render_started_at(
    state: dict,
    *,
    fallback_started_at: datetime | None = None,
) -> datetime | None:
    steps = state.get("steps")
    if isinstance(steps, dict):
        entry = steps.get(RENDER_STEP_ID)
        if isinstance(entry, dict):
            started = entry.get("started_at")
            if isinstance(started, str):
                parsed = _parse_state_timestamp(started)
                if parsed is not None:
                    return parsed
    resume_at = state.get("resume_at")
    if resume_at == RENDER_STEP_ID:
        updated = state.get("updated_at")
        if isinstance(updated, str):
            parsed = _parse_state_timestamp(updated)
            if parsed is not None:
                return parsed
    return fallback_started_at


def _render_timing_display(
    state: dict,
    *,
    now: datetime | None = None,
    fallback_started_at: datetime | None = None,
) -> tuple[str | None, str | None]:
    started = _render_started_at(state, fallback_started_at=fallback_started_at)
    if started is None:
        return None, None

    started_display = _format_local_time(started)
    estimate_sec = _render_estimate_sec(state)
    if estimate_sec is None:
        return started_display, None

    current = now or datetime.now().astimezone()
    elapsed = (current - started).total_seconds()
    remaining = estimate_sec - elapsed
    if remaining <= 0:
        return started_display, "Taking longer than estimated"
    return started_display, f"Est. {_format_remaining(remaining)} for this step"


def read_render_progress(
    state: dict,
    working_folder: Path,
    *,
    now: datetime | None = None,
    fallback_started_at: datetime | None = None,
) -> RenderProgress:
    resume_at = state.get("resume_at")
    if not isinstance(resume_at, str):
        resume_at = None

    failure = read_prep_failure(working_folder)
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    render_entry = steps.get(RENDER_STEP_ID) if isinstance(steps, dict) else None
    render_status = (
        render_entry.get("status") if isinstance(render_entry, dict) else None
    )

    if resume_at == "14_done":
        return RenderProgress(
            resume_at=resume_at,
            current_label="Render complete",
            step_lines=[f"✓ {RENDER_STEP_LABEL}"],
            render_complete=True,
            failure=failure,
        )

    lines: list[str] = []
    if render_status == "completed":
        lines.append(f"✓ {RENDER_STEP_LABEL}")
    elif resume_at == RENDER_STEP_ID or render_status == "in_progress":
        lines.append(f"→ {RENDER_STEP_LABEL}")
    else:
        lines.append(f"  {RENDER_STEP_LABEL}")

    started_display, eta_display = _render_timing_display(
        state,
        now=now,
        fallback_started_at=fallback_started_at,
    )

    current_label = RENDER_STEP_LABEL
    summary = failure_summary(failure)
    if summary:
        current_label = summary
    elif render_status != "in_progress" and resume_at != RENDER_STEP_ID:
        current_label = "Starting full render…"

    return RenderProgress(
        resume_at=resume_at,
        current_label=current_label,
        step_started_display=started_display,
        step_eta_display=eta_display,
        step_lines=lines,
        render_complete=False,
        failure=failure,
    )
