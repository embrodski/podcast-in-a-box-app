"""Resume / Abort prompt for jobs interrupted by quit or crash."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QWidget

from app.gui.dialogs import REMOVE_FROM_QUEUE_TEXT


def prompt_interrupted_job(
    parent: QWidget,
    *,
    folder: Path,
    lane: str,
    name: str = "",
) -> str:
    """Return 'resume', 'abort', or 'skip'."""
    label = name or folder.name
    kind = "full render" if lane == "full" else "Fast Preview"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Interrupted autocut")
    box.setText(f"A prior {kind} job was interrupted:\n{label}")
    box.setInformativeText("Would you like to resume?")
    resume = box.addButton("Resume", QMessageBox.AcceptRole)
    abort = box.addButton("Abort", QMessageBox.DestructiveRole)
    box.exec()
    if box.clickedButton() == resume:
        return "resume"
    if box.clickedButton() != abort:
        return "skip"
    confirm = QMessageBox(parent)
    confirm.setIcon(QMessageBox.Warning)
    confirm.setWindowTitle("Confirm abort")
    confirm.setText(REMOVE_FROM_QUEUE_TEXT)
    confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    confirm.setDefaultButton(QMessageBox.No)
    if confirm.exec() == QMessageBox.Yes:
        return "abort"
    return "skip"
