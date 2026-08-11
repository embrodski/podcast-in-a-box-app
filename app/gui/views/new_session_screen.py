"""Screen A3 — record now or already recorded."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QVBoxLayout

from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label


class NewSessionScreen(ScreenWidget):
    screen_id = "A3"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        layout.addWidget(
            body_label(
                "Have you already recorded this podcast, or will you be recording now?"
            )
        )

        record_btn = QPushButton("Record now")
        record_btn.setMinimumHeight(44)
        record_btn.clicked.connect(lambda: self._begin("record"))
        layout.addWidget(record_btn)

        already_btn = QPushButton("Already recorded — start autocut")
        already_btn.setMinimumHeight(44)
        already_btn.clicked.connect(lambda: self._begin("already_recorded"))
        layout.addWidget(already_btn)

        layout.addStretch()

        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("A1"))
        layout.addWidget(back)

    def _begin(self, entry_path: str) -> None:
        window = self.window()
        if hasattr(window, "begin_session_flow"):
            window.begin_session_flow(entry_path)
            return
        self.navigate.emit("C1")

    def on_enter(self) -> None:
        pass
