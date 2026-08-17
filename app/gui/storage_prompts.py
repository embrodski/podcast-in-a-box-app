"""Low Disk Space prompts and Clean Old Working Files offer."""

from __future__ import annotations

from typing import Literal

from PySide6.QtWidgets import QMessageBox, QWidget

from app.controller.storage_gate import StorageAssessment
from app.gui.widgets.screen_base import ScreenWidget

StorageGateAction = Literal["proceed", "go_clean", "abort"]


def _has_clean_candidates(screen: ScreenWidget) -> bool:
    try:
        return bool(screen.controller.list_clean_working_candidates())
    except Exception:
        return False


def offer_clean_old_working_files(
    parent: QWidget,
    *,
    detail: str,
) -> bool:
    """Ask whether to open A4. Returns True if the user chose Yes."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Low Disk Space")
    box.setText("Low Disk Space")
    box.setInformativeText(
        f"{detail}\n\nWould you like to clean old working files?"
    )
    yes = box.addButton("Yes", QMessageBox.YesRole)
    box.addButton("No", QMessageBox.NoRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() == yes


def confirm_critical_recording(parent: QWidget) -> bool:
    """Second confirmation when free space is under 5 GB. True = Continue Anyway."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Low Disk Space")
    box.setText(
        "Are you sure? You have less than 5 GB and recording is ~1.2GB/min. "
        "You will only be able to record 4 minutes at most."
    )
    continue_btn = box.addButton("Continue Anyway", QMessageBox.AcceptRole)
    box.addButton("Go Back", QMessageBox.RejectRole)
    box.setDefaultButton(continue_btn)
    box.exec()
    return box.clickedButton() == continue_btn


def confirm_continue_despite_low_disk(parent: QWidget, *, detail: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Low Disk Space")
    box.setText(detail)
    continue_btn = box.addButton("Continue Anyway", QMessageBox.AcceptRole)
    box.addButton("Go Back", QMessageBox.RejectRole)
    box.setDefaultButton(continue_btn)
    box.exec()
    return box.clickedButton() == continue_btn


def navigate_to_clean_working_files(screen: ScreenWidget, *, return_screen: str) -> None:
    ctx = screen.context()
    if ctx is not None:
        ctx.clean_return_screen = return_screen
    screen.navigate.emit("A4")


def gate_low_disk(
    screen: ScreenWidget,
    assessment: StorageAssessment,
    *,
    return_screen: str,
    critical_recording: bool = False,
) -> StorageGateAction:
    """
    If space is low, offer Clean Old Working Files, then optional continue/abort.

    Returns:
      proceed — caller may continue
      go_clean — already navigating to A4
      abort — user chose Go Back / declined continue
    """
    if assessment.level == "ok":
        return "proceed"

    offered_clean = False
    if _has_clean_candidates(screen):
        offered_clean = True
        if offer_clean_old_working_files(screen, detail=assessment.message):
            navigate_to_clean_working_files(screen, return_screen=return_screen)
            return "go_clean"

    if critical_recording and assessment.level == "critical":
        if confirm_critical_recording(screen):
            return "proceed"
        return "abort"

    if assessment.level in {"insufficient", "critical"}:
        if confirm_continue_despite_low_disk(screen, detail=assessment.message):
            return "proceed"
        return "abort"

    if assessment.level == "warn" and not offered_clean:
        if confirm_continue_despite_low_disk(screen, detail=assessment.message):
            return "proceed"
        return "abort"

    # Soft warn after declining clean: continue.
    return "proceed"


def maybe_offer_clean_on_disk_failure(
    screen: ScreenWidget,
    *,
    summary: str,
    retry_screen: str,
    detail: str | None = None,
    aborted: bool = False,
) -> None:
    """Set failure context, optionally offer A4, then go to F1 (or A4 first)."""
    from app.controller.paths import ensure_scripts_path
    from app.gui.failure_context import set_failure_context

    ensure_scripts_path()
    from piab_disk_errors import is_disk_full_error

    set_failure_context(
        screen.context(),
        summary=summary,
        retry_screen=retry_screen,
        detail=detail,
        aborted=aborted,
    )

    if not aborted:
        from app.gui.failure_alert import alert_workflow_failure

        ctx = screen.context()
        folder = ctx.session_folder if ctx is not None else None
        alert_workflow_failure(
            screen,
            working_folder=folder,
            summary=summary,
            detail=detail,
            aborted=aborted,
        )

    text = f"{summary}\n{detail or ''}"
    if is_disk_full_error(text) and _has_clean_candidates(screen):
        if offer_clean_old_working_files(screen, detail=summary):
            navigate_to_clean_working_files(screen, return_screen="F1")
            return

    screen.navigate.emit("F1")
