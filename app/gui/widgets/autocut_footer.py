"""Shared footer line for autocut queue / slower-prep notice."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


class AutocutFooter(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setStyleSheet("color: #64748b; font-size: 12px;")
        self.setText("")

    def set_status(self, line: str, *, prep_notice: bool = False) -> None:
        parts: list[str] = []
        if line:
            parts.append(line)
        if prep_notice and line:
            parts.append("These Autocut preparation steps will run a bit slower, be patient.")
        self.setText("\n".join(parts))
        self.setVisible(bool(parts))
