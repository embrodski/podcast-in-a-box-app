"""Placeholder for screens not yet implemented."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QPushButton, QVBoxLayout

from app.gui.screens import SCREEN_TITLES
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label


class PlaceholderScreen(ScreenWidget):
    def __init__(self, screen_id: str, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self.screen_id = screen_id
        self._session_folder: Path | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._title = heading_label("")
        layout.addWidget(self._title)

        self._body = body_label("")
        layout.addWidget(self._body)

        layout.addStretch()

        back = QPushButton("Back to home")
        back.clicked.connect(lambda: self.navigate.emit("A1"))
        layout.addWidget(back)

    def set_session_folder(self, folder: Path | None) -> None:
        self._session_folder = folder

    def on_enter(self) -> None:
        title = SCREEN_TITLES.get(self.screen_id, self.screen_id)
        self._title.setText(f"{self.screen_id} — {title}")
        lines = [
            "This step is not built in the app yet.",
            "The controller and CLI can still run the pipeline.",
        ]
        if self._session_folder is not None:
            lines.append(f"Session folder:\n{self._session_folder}")
        self._body.setText("\n\n".join(lines))
