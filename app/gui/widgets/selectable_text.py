"""Copy-friendly labels and read-only text areas."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit

SELECTABLE_TEXT = (
    Qt.TextInteractionFlag.TextSelectableByMouse
    | Qt.TextInteractionFlag.TextSelectableByKeyboard
)


def apply_selectable(label: QLabel) -> QLabel:
    label.setTextInteractionFlags(SELECTABLE_TEXT)
    label.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    return label


def body_label(text: str = "", *, word_wrap: bool = True) -> QLabel:
    label = QLabel(text)
    if word_wrap:
        label.setWordWrap(True)
    return apply_selectable(label)


def heading_label(text: str = "", *, word_wrap: bool = False) -> QLabel:
    label = body_label(text, word_wrap=word_wrap)
    label.setStyleSheet("font-size: 18px; font-weight: 600;")
    return label


def selectable_plain_text(*, visible_rows: int = 4) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setReadOnly(True)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    edit.setTabChangesFocus(True)
    row_height = edit.fontMetrics().lineSpacing()
    edit.setFixedHeight(row_height * visible_rows + 2 * edit.frameWidth() + 8)
    return edit


def set_plain_lines(edit: QPlainTextEdit, lines: list[str]) -> None:
    edit.setPlainText("\n".join(lines) if lines else "(none)")
