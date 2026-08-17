"""Reusable GUI dialogs."""

from __future__ import annotations

from typing import Literal

from PySide6.QtWidgets import QMessageBox, QWidget

ExistingSessionChoice = Literal["resume", "choose_different", "force_new"]


def confirm_close_while_busy(parent: QWidget, reasons: list[str]) -> bool:
    """Return True if the user chose to abort and quit."""
    reason_text = ", ".join(reasons)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Work in progress")
    box.setText("Recording or processing is still running.")
    box.setInformativeText(
        f"Active: {reason_text}.\n\n"
        "Stay to keep it running, or quit and abort active jobs."
    )
    stay = box.addButton("Stay", QMessageBox.RejectRole)
    quit_abort = box.addButton("Abort and quit", QMessageBox.DestructiveRole)
    box.setDefaultButton(stay)
    box.exec()
    return box.clickedButton() == quit_abort


def confirm_hold_outside_queue(parent: QWidget) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Hold outside queue?")
    box.setText(
        "This job will be held in its present state and removed from the "
        "auto-process queue."
    )
    box.setInformativeText(
        "You can manually restart it later from Home (On hold) or Resume session. "
        "It will not start automatically."
    )
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


def confirm_action(parent: QWidget, *, title: str, text: str, detail: str = "") -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


def choose_existing_session_action(
    parent: QWidget,
    *,
    title: str,
    text: str,
    detail: str = "",
    allow_resume: bool = True,
) -> ExistingSessionChoice | None:
    """Three-way (or two-way) prompt when a folder already has podcast-in-a-box.json."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    resume_btn = None
    if allow_resume:
        resume_btn = box.addButton("Resume session", QMessageBox.AcceptRole)
    choose_btn = box.addButton("Choose different folder", QMessageBox.RejectRole)
    force_btn = box.addButton("Force new session here", QMessageBox.DestructiveRole)
    box.setDefaultButton(resume_btn or force_btn)
    box.exec()
    clicked = box.clickedButton()
    if resume_btn is not None and clicked == resume_btn:
        return "resume"
    if clicked == choose_btn:
        return "choose_different"
    if clicked == force_btn:
        return "force_new"
    return None
