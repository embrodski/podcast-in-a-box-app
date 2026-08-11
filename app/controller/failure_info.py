"""Read harness failure artifacts and suggest a retry screen."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.controller.prep_progress import PREP_STEP_ORDER, read_prep_failure
from app.controller.render_progress import RENDER_STEP_ID
from app.controller.resume_router import resume_screen_for


@dataclass
class FailureInfo:
    summary: str
    detail: str | None = None
    step_id: str | None = None
    step_title: str | None = None
    pipeline: str | None = None
    retry_screen: str = "A1"
    aborted: bool = False

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "detail": self.detail,
            "step_id": self.step_id,
            "step_title": self.step_title,
            "pipeline": self.pipeline,
            "retry_screen": self.retry_screen,
            "aborted": self.aborted,
        }


def retry_screen_for_failure(
    failure: dict | None,
    resume_at: str | None,
) -> str:
    if failure:
        pipeline = str(failure.get("pipeline") or "")
        step_id = str(failure.get("step_id") or "")
        if pipeline == "piab_full_render" or step_id == RENDER_STEP_ID:
            return "F4"
        if pipeline == "piab_prep" or step_id in PREP_STEP_ORDER:
            return "E1"

    if resume_at == "12_estimate_full" or resume_at == RENDER_STEP_ID:
        return "F4"
    if isinstance(resume_at, str) and resume_at in PREP_STEP_ORDER:
        return "E1"
    screen = resume_screen_for(resume_at)
    if screen in {"E1", "F4", "F3", "F2"}:
        return screen
    return "E1"


def read_failure_info(
    working_folder: Path,
    state: dict | None = None,
    *,
    summary: str | None = None,
    detail: str | None = None,
    retry_screen: str | None = None,
    aborted: bool = False,
) -> FailureInfo:
    folder = working_folder.resolve()
    failure = read_prep_failure(folder)
    resume_at = state.get("resume_at") if isinstance(state, dict) else None

    step_id = None
    step_title = None
    pipeline = None
    file_summary = None
    file_detail = None

    if failure:
        step_id = failure.get("step_id")
        if step_id is not None:
            step_id = str(step_id)
        step_title = failure.get("step_title")
        if step_title is not None:
            step_title = str(step_title)
        pipeline = failure.get("pipeline")
        if pipeline is not None:
            pipeline = str(pipeline)
        raw_summary = failure.get("error_summary") or failure.get("summary")
        if isinstance(raw_summary, str) and raw_summary.strip():
            file_summary = raw_summary.strip()
        raw_detail = failure.get("error_detail") or failure.get("detail")
        if isinstance(raw_detail, str) and raw_detail.strip():
            file_detail = raw_detail.strip()

    resolved_summary = (
        summary
        or file_summary
        or "Something went wrong during processing."
    )
    resolved_detail = detail or file_detail

    from app.controller.paths import ensure_scripts_path

    ensure_scripts_path()
    from piab_disk_errors import format_disk_full_user_message, is_disk_full_error

    detail_for_check = resolved_detail or file_summary or ""
    if is_disk_full_error(detail_for_check) or is_disk_full_error(resolved_summary):
        duration = None
        if isinstance(state, dict):
            value = state.get("source_duration_sec")
            if isinstance(value, (int, float)) and value > 0:
                duration = float(value)
        resolved_summary = format_disk_full_user_message(
            detail_for_check,
            working_folder=folder,
            source_duration_sec=duration,
        )

    resolved_retry = retry_screen or retry_screen_for_failure(failure, resume_at)

    return FailureInfo(
        summary=resolved_summary,
        detail=resolved_detail,
        step_id=step_id,
        step_title=step_title,
        pipeline=pipeline,
        retry_screen=resolved_retry,
        aborted=aborted,
    )
