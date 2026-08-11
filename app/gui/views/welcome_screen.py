"""Screen A1 — welcome / home."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QVBoxLayout

from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label


class WelcomeScreen(ScreenWidget):
    screen_id = "A1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = heading_label("Podcast in a Box")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)

        blurb = body_label(
            "Record a podcast in the Lighthaven room, or bring back files from a "
            "previous session to autocut an interview."
        )
        layout.addWidget(blurb)

        self._banner = body_label("")
        self._banner.setStyleSheet("color: #a00; font-weight: 600;")
        layout.addWidget(self._banner)

        layout.addSpacing(8)

        new_btn = QPushButton("New session")
        new_btn.setMinimumHeight(44)
        new_btn.clicked.connect(lambda: self.navigate.emit("A3"))
        layout.addWidget(new_btn)

        resume_btn = QPushButton("Resume session")
        resume_btn.setMinimumHeight(44)
        resume_btn.clicked.connect(lambda: self.navigate.emit("A2"))
        layout.addWidget(resume_btn)

        layout.addStretch()

        hint = body_label("Your files are saved under E:\\PodcastRoom")
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

    def on_enter(self) -> None:
        reasons = self.controller.busy_reasons()
        if reasons:
            self._banner.setText(
                f"Note: {', '.join(reasons)} is active in the background."
            )
        else:
            self._banner.setText("")
