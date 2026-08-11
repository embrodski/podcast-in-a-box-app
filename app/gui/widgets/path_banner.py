"""Reusable session folder path banner."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from app.gui.widgets.selectable_text import apply_selectable


class PathBanner(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "PathBanner { background: #1e293b; border: 1px solid #334155; border-radius: 6px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        caption = apply_selectable(QLabel("Session folder"))
        caption.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(caption)

        self._path = apply_selectable(QLabel(""))
        self._path.setWordWrap(True)
        self._path.setStyleSheet("color: #f8fafc; font-family: Consolas, monospace;")
        layout.addWidget(self._path)

    def set_path(self, path: Path | str | None) -> None:
        self._path.setText(str(path) if path else "")
