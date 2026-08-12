"""Read full-render progress from session state on disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.controller.prep_progress import (
    PREP_STEP_LABELS,
    PREP_STEP_ORDER,
    _format_local_time,
    _format_remaining,
    _parse_state_timestamp,
    failure_summary,
    read_prep_failure,
)

RENDER_STEP_ID = "13_full_render"
RENDER_STEP_ALIASES: tuple[str, ...] = ("13_full_render", "20_full_interview_render")

# Phases marked by piab_run_full_render.py (and shown on F4 / E1 after Fast Preview).
RENDER_PHASE_ORDER: tuple[str, ...] = (
    "13_full_render",
    "13_output_transcripts",
    "13_delivery",
)

RENDER_PHASE_LABELS: dict[str, str] = {
    "13_full_render": "Rendering full interview",
    "13_output_transcripts": "Writing transcript files",
    "13_delivery": "Uploading & sharing",
}

RENDER_STEP_LABEL = RENDER_PHASE_LABELS[RENDER_STEP_ID]


@dataclass
class RenderProgress:
    resume_at: str | None = None
    current_step: str | None = None
    current_label: str = "Starting…"
    step_started_display: str | None = None
    step_eta_display: str | None = None
    step_lines: list[str] = field(default_factory=list)
    render_complete: bool = False
    failure: dict | None = None


def _step_done(status: str | None) -> bool:
    return status in {"completed", "skipped"}


def _render_entry(steps: dict, step_id: str) -> dict | None:
    entry = steps.get(step_id)
    if isinstance(entry, dict):
        return entry
    if step_id == RENDER_STEP_ID:
        for alias in RENDER_STEP_ALIASES:
            alt = steps.get(alias)
            if isinstance(alt, dict):
                return alt
    return None


def _render_estimate_sec(state: dict) -> int | None:
    estimate = state.get("estimate_full")
    if not isinstance(estimate, dict):
        return None
    center = estimate.get("center_sec")
    if isinstance(center, (int, float)):
        return int(center)
    return None


def _phase_started_at(
    state: dict,
    step_id: str,
    *,
    fallback_started_at: datetime | None = None,
) -> datetime | None:
    steps = state.get("steps")
    if isinstance(steps, dict):
        entry = _render_entry(steps, step_id)
        if entry is not None:
            started = entry.get("started_at")
            if isinstance(started, str):
                parsed = _parse_state_timestamp(started)
                if parsed is not None:
                    return parsed
    resume_at = state.get("resume_at")
    if resume_at == step_id or (
        step_id == RENDER_STEP_ID and resume_at in RENDER_STEP_ALIASES
    ):
        updated = state.get("updated_at")
        if isinstance(updated, str):
            parsed = _parse_state_timestamp(updated)
            if parsed is not None:
                return parsed
    return fallback_started_at


def _phase_timing_display(
    state: dict,
    step_id: str | None,
    *,
    now: datetime | None = None,
    fallback_started_at: datetime | None = None,
) -> tuple[str | None, str | None]:
    if step_id is None:
        return None, None

    started = _phase_started_at(
        state, step_id, fallback_started_at=fallback_started_at
    )
    if started is None:
        return None, None

    started_display = _format_local_time(started)
    # Only the long encode has a meaningful ETA from estimate_full.
    if step_id != RENDER_STEP_ID:
        return started_display, None

    estimate_sec = _render_estimate_sec(state)
    if estimate_sec is None:
        return started_display, None

    current = now or datetime.now().astimezone()
    elapsed = (current - started).total_seconds()
    remaining = estimate_sec - elapsed
    if remaining <= 0:
        return started_display, "Taking longer than estimated"
    return started_display, f"Est. {_format_remaining(remaining)} for this step"


def _current_render_phase(state: dict, steps: dict, resume_at: str | None) -> str | None:
    if resume_at == "14_done":
        return None

    if isinstance(resume_at, str) and resume_at in RENDER_PHASE_ORDER:
        entry = _render_entry(steps, resume_at)
        status = entry.get("status") if entry else None
        if not _step_done(status):
            return resume_at

    for step_id in RENDER_PHASE_ORDER:
        entry = _render_entry(steps, step_id)
        status = entry.get("status") if entry else None
        if not _step_done(status):
            return step_id
    return None


def prep_lines_before_render(state: dict) -> list[str]:
    """Completed prep checklist shown above render phases on the chained E1 path."""
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    lines: list[str] = []
    for step_id in PREP_STEP_ORDER:
        label = PREP_STEP_LABELS[step_id]
        entry = steps.get(step_id) if isinstance(steps, dict) else None
        status = entry.get("status") if isinstance(entry, dict) else None
        if step_id == "10_one_min_test" and status == "skipped":
            lines.append("✓ 1-minute preview (from Fast Preview)")
        elif _step_done(status):
            lines.append(f"✓ {label}")
        else:
            lines.append(f"  {label}")
    return lines


def read_render_progress(
    state: dict,
    working_folder: Path,
    *,
    now: datetime | None = None,
    fallback_started_at: datetime | None = None,
    include_prep_prefix: bool = False,
) -> RenderProgress:
    resume_at = state.get("resume_at")
    if not isinstance(resume_at, str):
        resume_at = None

    failure = read_prep_failure(working_folder)
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}

    if resume_at == "14_done":
        lines = [f"✓ {RENDER_PHASE_LABELS[step_id]}" for step_id in RENDER_PHASE_ORDER]
        if include_prep_prefix:
            lines = prep_lines_before_render(state) + lines
        return RenderProgress(
            resume_at=resume_at,
            current_step=None,
            current_label="Render complete",
            step_lines=lines,
            render_complete=True,
            failure=failure,
        )

    current_step = _current_render_phase(state, steps, resume_at)

    lines: list[str] = []
    if include_prep_prefix:
        lines.extend(prep_lines_before_render(state))

    for step_id in RENDER_PHASE_ORDER:
        label = RENDER_PHASE_LABELS[step_id]
        entry = _render_entry(steps, step_id)
        status = entry.get("status") if entry else None
        if _step_done(status):
            lines.append(f"✓ {label}")
        elif step_id == current_step:
            lines.append(f"→ {label}")
        else:
            lines.append(f"  {label}")

    started_display, eta_display = _phase_timing_display(
        state,
        current_step,
        now=now,
        fallback_started_at=fallback_started_at,
    )

    current_label = (
        RENDER_PHASE_LABELS.get(current_step, "Starting full render…")
        if current_step
        else "Starting full render…"
    )
    summary = failure_summary(failure, state=state, working_folder=working_folder)
    if summary:
        current_label = summary
    elif current_step is None and resume_at not in RENDER_STEP_ALIASES:
        # Job just started; encode step not marked yet.
        current_label = "Starting full render…"
        if lines and not any(line.startswith("→") for line in lines):
            # Point at first incomplete phase for display.
            for i, step_id in enumerate(RENDER_PHASE_ORDER):
                entry = _render_entry(steps, step_id)
                status = entry.get("status") if entry else None
                if not _step_done(status):
                    idx = i + (len(PREP_STEP_ORDER) if include_prep_prefix else 0)
                    if 0 <= idx < len(lines):
                        lines[idx] = f"→ {RENDER_PHASE_LABELS[step_id]}"
                    current_step = step_id
                    current_label = RENDER_PHASE_LABELS[step_id]
                    break

    return RenderProgress(
        resume_at=resume_at,
        current_step=current_step,
        current_label=current_label,
        step_started_display=started_display,
        step_eta_display=eta_display,
        step_lines=lines,
        render_complete=False,
        failure=failure,
    )
