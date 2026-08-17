"""Read prep step progress from session state on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PREP_STEP_ORDER: tuple[str, ...] = (
    "06_conversation_sync",
    "07_deroom_placeholder",
    "08_video_sync",
    "09_transcribe",
    "10_one_min_test",
)

FAST_PREVIEW_STEP_ORDER: tuple[str, ...] = (
    "06p_conversation_sync",
    "07p_deroom_placeholder",
    "08p_video_sync",
    "09p_transcribe",
    "10p_fast_preview_one_min",
)

PREP_STEP_LABELS: dict[str, str] = {
    "06_conversation_sync": "Syncing audio",
    "07_deroom_placeholder": "Preparing clean audio",
    "08_video_sync": "Syncing video",
    "09_transcribe": "Transcribing (ElevenLabs)",
    "10_one_min_test": "Rendering 1-minute preview",
    "06p_conversation_sync": "Syncing audio",
    "07p_deroom_placeholder": "Preparing clean audio",
    "08p_video_sync": "Syncing video",
    "09p_transcribe": "Transcribing (ElevenLabs)",
    "10p_fast_preview_one_min": "Rendering 1-minute preview",
}

PREP_STEP_ESTIMATE_KEYS: dict[str, str | None] = {
    "06_conversation_sync": "conversation_sync_sec",
    "07_deroom_placeholder": None,
    "08_video_sync": "video_sync_sec",
    "09_transcribe": "transcribe_sec",
    "10_one_min_test": "one_min_render_sec",
    "06p_conversation_sync": "conversation_sync_sec",
    "07p_deroom_placeholder": None,
    "08p_video_sync": "video_sync_sec",
    "09p_transcribe": "transcribe_sec",
    "10p_fast_preview_one_min": "one_min_render_sec",
}

DEROOM_PLACEHOLDER_EST_SEC = 30

FAILURE_JSON_NAME = "harness-FAILURE.json"


def failure_summary(
    failure: dict | None,
    *,
    state: dict | None = None,
    working_folder: Path | None = None,
) -> str | None:
    if not failure:
        return None
    for key in ("error_summary", "summary"):
        value = failure.get(key)
        if isinstance(value, str) and value.strip():
            raw = value.strip()
            break
    else:
        raw = None

    detail = failure.get("error_detail") or failure.get("detail") or ""
    if isinstance(detail, str):
        detail_text = detail
    else:
        detail_text = ""

    folder = working_folder
    if folder is None:
        wf = failure.get("working_folder")
        if isinstance(wf, str) and wf.strip():
            folder = Path(wf)

    duration = None
    if isinstance(state, dict):
        value = state.get("source_duration_sec")
        if isinstance(value, (int, float)) and value > 0:
            duration = float(value)

    from app.controller.paths import ensure_scripts_path

    ensure_scripts_path()
    from piab_disk_errors import format_disk_full_user_message, is_disk_full_error

    if is_disk_full_error(detail_text) or is_disk_full_error(raw or ""):
        return format_disk_full_user_message(
            detail_text or raw or "",
            working_folder=folder,
            source_duration_sec=duration,
        )

    return raw


@dataclass
class PrepProgress:
    resume_at: str | None = None
    current_step: str | None = None
    current_label: str = "Starting…"
    step_started_display: str | None = None
    step_eta_display: str | None = None
    step_lines: list[str] = field(default_factory=list)
    prep_complete: bool = False
    failure: dict | None = None

    def to_dict(self) -> dict:
        return {
            "resume_at": self.resume_at,
            "current_step": self.current_step,
            "current_label": self.current_label,
            "step_started_display": self.step_started_display,
            "step_eta_display": self.step_eta_display,
            "step_lines": list(self.step_lines),
            "prep_complete": self.prep_complete,
            "failure": self.failure,
        }


def _failure_candidates(working_folder: Path) -> list[Path]:
    return [
        working_folder / "Temp" / FAILURE_JSON_NAME,
        working_folder / "Preview Files" / "Temp" / FAILURE_JSON_NAME,
    ]


def read_prep_failure(working_folder: Path) -> dict | None:
    newest: tuple[float, dict] | None = None
    for path in _failure_candidates(working_folder):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and (newest is None or mtime >= newest[0]):
            newest = (mtime, data)
    return newest[1] if newest else None


def clear_prep_failure(working_folder: Path) -> None:
    """Remove stale failure markers so a retry can start prep or full render."""
    for temp in (working_folder / "Temp", working_folder / "Preview Files" / "Temp"):
        for name in (FAILURE_JSON_NAME, "harness-FAILURE.txt"):
            path = temp / name
            if path.is_file():
                path.unlink()


def is_fast_preview_progress(state: dict) -> bool:
    """True while the UI should show Fast Preview prep step lines."""
    resume_at = state.get("resume_at")
    if isinstance(resume_at, str):
        if resume_at in FAST_PREVIEW_STEP_ORDER:
            return True
        if resume_at in PREP_STEP_ORDER:
            return False
        if resume_at in (
            "12_estimate_full",
            "13_full_prep_after_preview",
            "13_full_render",
            "14_done",
        ):
            return False

    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    if not isinstance(steps, dict):
        steps = {}

    for step_id in FAST_PREVIEW_STEP_ORDER:
        entry = steps.get(step_id)
        if isinstance(entry, dict) and entry.get("status") == "in_progress":
            return True

    full_started = any(
        isinstance(steps.get(step_id), dict)
        and steps[step_id].get("status") in {"completed", "in_progress"}
        for step_id in PREP_STEP_ORDER
    )
    if full_started:
        return False

    ten_p = steps.get("10p_fast_preview_one_min")
    ten_p_done = isinstance(ten_p, dict) and ten_p.get("status") == "completed"
    if ten_p_done:
        return False

    return any(
        isinstance(steps.get(step_id), dict)
        and steps[step_id].get("status") in {"completed", "in_progress", "awaiting_user"}
        for step_id in FAST_PREVIEW_STEP_ORDER
    )


def prep_needs_resume(state: dict, working_folder: Path) -> bool:
    resume_at = state.get("resume_at")
    if isinstance(resume_at, str) and resume_at in PREP_STEP_ORDER:
        return True
    if isinstance(resume_at, str) and resume_at in FAST_PREVIEW_STEP_ORDER:
        return True
    if read_prep_failure(working_folder) is not None:
        return True
    steps = state.get("steps") or {}
    if not isinstance(steps, dict):
        return False
    for step_id in (*PREP_STEP_ORDER, *FAST_PREVIEW_STEP_ORDER):
        entry = steps.get(step_id)
        if isinstance(entry, dict) and entry.get("status") == "completed":
            return True
    return False


def _parse_state_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _format_local_time(dt: datetime) -> str:
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{hour}:{dt.strftime('%M %p')}"


def _format_remaining(seconds: float) -> str:
    remaining = max(0, int(round(seconds)))
    if remaining <= 0:
        return "any moment now"
    if remaining < 60:
        return "less than 1 min remaining"
    minutes = remaining // 60
    if minutes < 60:
        return f"about {minutes} min remaining"
    hours, mins = divmod(minutes, 60)
    if mins:
        return f"about {hours} hr {mins} min remaining"
    return f"about {hours} hr remaining"


def _estimate_breakdown(state: dict, *, fast: bool) -> dict | None:
    if fast:
        from app.controller.paths import ensure_scripts_path

        ensure_scripts_path()
        from piab_fast_preview_lib import estimate_fast_preview_prep

        breakdown = estimate_fast_preview_prep().get("breakdown")
        return breakdown if isinstance(breakdown, dict) else None
    estimate = state.get("estimate_prep")
    if not isinstance(estimate, dict):
        return None
    breakdown = estimate.get("breakdown")
    return breakdown if isinstance(breakdown, dict) else None


def _step_estimate_sec(state: dict, step_id: str) -> int | None:
    if step_id in {"07_deroom_placeholder", "07p_deroom_placeholder"}:
        return DEROOM_PLACEHOLDER_EST_SEC
    key = PREP_STEP_ESTIMATE_KEYS.get(step_id)
    if not key:
        return None
    breakdown = _estimate_breakdown(state, fast=step_id in FAST_PREVIEW_STEP_ORDER)
    if breakdown is None:
        return None
    value = breakdown.get(key)
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _step_started_at(
    state: dict,
    step_id: str,
    *,
    fallback_started_at: datetime | None = None,
) -> datetime | None:
    steps = state.get("steps")
    if isinstance(steps, dict):
        entry = steps.get(step_id)
        if isinstance(entry, dict):
            started = entry.get("started_at")
            if isinstance(started, str):
                parsed = _parse_state_timestamp(started)
                if parsed is not None:
                    return parsed
    resume_at = state.get("resume_at")
    if resume_at == step_id:
        updated = state.get("updated_at")
        if isinstance(updated, str):
            parsed = _parse_state_timestamp(updated)
            if parsed is not None:
                return parsed
    return fallback_started_at


def _step_timing_display(
    state: dict,
    step_id: str | None,
    *,
    now: datetime | None = None,
    fallback_started_at: datetime | None = None,
) -> tuple[str | None, str | None]:
    if step_id is None or step_id not in PREP_STEP_LABELS:
        return None, None

    started = _step_started_at(state, step_id, fallback_started_at=fallback_started_at)
    if started is None:
        return None, None

    started_display = _format_local_time(started)
    estimate_sec = _step_estimate_sec(state, step_id)
    if estimate_sec is None:
        return started_display, None

    current = now or datetime.now().astimezone()
    elapsed = (current - started).total_seconds()
    remaining = estimate_sec - elapsed
    if remaining <= 0:
        return started_display, "Taking longer than estimated"
    return started_display, f"Est. {_format_remaining(remaining)} for this step"


def _step_done(status: str | None) -> bool:
    return status in {"completed", "skipped"}


def read_prep_progress(
    state: dict,
    working_folder: Path,
    *,
    now: datetime | None = None,
    fallback_started_at: datetime | None = None,
) -> PrepProgress:
    resume_at = state.get("resume_at")
    if not isinstance(resume_at, str):
        resume_at = None

    failure = read_prep_failure(working_folder)
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    fast = is_fast_preview_progress(state)
    step_order = FAST_PREVIEW_STEP_ORDER if fast else PREP_STEP_ORDER

    # Chained full-after-preview / full render — use render progress (optionally with prep prefix).
    if resume_at in ("13_full_render", "14_done") or resume_at in (
        "13_output_transcripts",
        "13_delivery",
    ):
        from app.controller.render_progress import read_render_progress

        include_prep = bool(
            (state.get("fast_preview_approval") or {}).get("approved_at")
        ) or any(
            isinstance(steps.get(step_id), dict)
            and steps[step_id].get("status") == "skipped"
            for step_id in ("10_one_min_test",)
        )
        render = read_render_progress(
            state,
            working_folder,
            now=now,
            fallback_started_at=fallback_started_at,
            include_prep_prefix=include_prep,
        )
        return PrepProgress(
            resume_at=resume_at,
            current_step=render.current_step,
            current_label=render.current_label,
            step_started_display=render.step_started_display,
            step_eta_display=render.step_eta_display,
            step_lines=render.step_lines,
            prep_complete=render.render_complete,
            failure=render.failure or failure,
        )

    if resume_at in ("11_one_min_approval", "10a_sync_offset_approval"):
        from app.controller.fast_preview import fast_preview_review_pending

        is_fast = fast_preview_review_pending(state)
        lines = [
            f"✓ {PREP_STEP_LABELS[step_id]}"
            for step_id in step_order
        ]
        if is_fast:
            lines = [
                "✓ Fast Preview source clips",
                "✓ Fast Preview sync & transcribe",
                "✓ Fast Preview 1-minute test",
            ]
        label = "Prep complete"
        if resume_at == "10a_sync_offset_approval":
            label = "Sync check — pick which preview sounds better"
        elif is_fast:
            label = "Fast Preview ready for review"
        return PrepProgress(
            resume_at=resume_at,
            current_step=None,
            current_label=label,
            step_lines=lines,
            prep_complete=True,
            failure=failure,
        )

    current_step: str | None = None
    if isinstance(resume_at, str) and resume_at in step_order:
        entry = steps.get(resume_at) if isinstance(steps, dict) else None
        status = entry.get("status") if isinstance(entry, dict) else None
        if not _step_done(status):
            current_step = resume_at
    if current_step is None:
        for step_id in step_order:
            entry = steps.get(step_id) if isinstance(steps, dict) else None
            status = entry.get("status") if isinstance(entry, dict) else None
            if not _step_done(status):
                current_step = step_id
                break

    if current_step is None and failure:
        failed_step = failure.get("step_id")
        if isinstance(failed_step, str) and failed_step in step_order:
            current_step = failed_step

    lines: list[str] = []
    for step_id in step_order:
        label = PREP_STEP_LABELS[step_id]
        entry = steps.get(step_id) if isinstance(steps, dict) else None
        status = entry.get("status") if isinstance(entry, dict) else None
        if _step_done(status):
            if step_id in {"10_one_min_test", "10p_fast_preview_one_min"} and status == "skipped":
                lines.append("✓ 1-minute preview (from Fast Preview)")
            else:
                lines.append(f"✓ {label}")
        elif step_id == current_step:
            lines.append(f"→ {label}")
        else:
            lines.append(f"  {label}")

    current_label = PREP_STEP_LABELS.get(current_step, "Processing…") if current_step else "Processing…"
    summary = failure_summary(failure, state=state, working_folder=working_folder)
    if summary:
        current_label = summary

    started_display, eta_display = _step_timing_display(
        state,
        current_step,
        now=now,
        fallback_started_at=fallback_started_at,
    )

    return PrepProgress(
        resume_at=resume_at,
        current_step=current_step,
        current_label=current_label,
        step_started_display=started_display,
        step_eta_display=eta_display,
        step_lines=lines,
        prep_complete=False,
        failure=failure,
    )
