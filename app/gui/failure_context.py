"""Pass failure details between screens and F1."""

from __future__ import annotations

from app.gui.session_context import SessionContext


def set_failure_context(
    ctx: SessionContext | None,
    *,
    summary: str,
    retry_screen: str,
    detail: str | None = None,
    aborted: bool = False,
) -> None:
    if ctx is None:
        return
    ctx.failure_summary = summary
    ctx.failure_detail = detail
    ctx.failure_retry_screen = retry_screen
    ctx.failure_aborted = aborted


def clear_failure_context(ctx: SessionContext | None) -> None:
    if ctx is None:
        return
    ctx.failure_summary = None
    ctx.failure_detail = None
    ctx.failure_retry_screen = None
    ctx.failure_aborted = False


def navigate_to_failure(
    screen,
    *,
    summary: str,
    retry_screen: str,
    detail: str | None = None,
    aborted: bool = False,
    report: bool = True,
) -> None:
    set_failure_context(
        screen.context(),
        summary=summary,
        retry_screen=retry_screen,
        detail=detail,
        aborted=aborted,
    )
    if report and not aborted:
        from app.gui.failure_alert import alert_workflow_failure

        ctx = screen.context()
        folder = ctx.session_folder if ctx is not None else None
        alert_workflow_failure(
            screen,
            working_folder=folder,
            summary=summary,
            detail=detail,
            aborted=aborted,
            report=report,
        )
    screen.navigate.emit("F1")
