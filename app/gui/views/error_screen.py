"""Screen F1 — failure recovery."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

from app.gui.failure_context import clear_failure_context
from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import (
    body_label,
    heading_label,
    selectable_plain_text,
    set_plain_lines,
)


def _session_folder(screen: ScreenWidget) -> Path | None:
    ctx = screen.context()
    if ctx is None or ctx.session_folder is None:
        return None
    return ctx.session_folder


class ErrorScreen(ScreenWidget):
    """F1 — show failure details and offer retry or home."""

    screen_id = "F1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("Something went wrong"))
        self._summary = body_label("")
        layout.addWidget(self._summary)

        self._step = body_label("")
        layout.addWidget(self._step)

        self._detail = selectable_plain_text(visible_rows=5)
        self._detail.hide()
        layout.addWidget(self._detail)

        self._hint = body_label(
            "Your session is saved at the path below. "
            "Try again now, or return home and use Resume session later."
        )
        layout.addWidget(self._hint)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        layout.addStretch()

        row = QHBoxLayout()
        home = QPushButton("Back to home")
        home.clicked.connect(self._go_home)
        row.addWidget(home)
        row.addStretch()
        self._retry = QPushButton("Try again")
        self._retry.setDefault(True)
        self._retry.setMinimumHeight(44)
        self._retry.clicked.connect(self._try_again)
        row.addWidget(self._retry)
        layout.addLayout(row)

        self._retry_target = "A1"

    def on_enter(self) -> None:
        ctx = self.context()
        folder = _session_folder(self)
        self._banner.set_path(folder)

        state = None
        if folder is not None:
            try:
                state = self.controller.load_session_state(folder)
            except Exception:
                state = None

        info = self.controller.read_failure_info(
            folder,
            state,
            summary=ctx.failure_summary if ctx else None,
            detail=ctx.failure_detail if ctx else None,
            retry_screen=ctx.failure_retry_screen if ctx else None,
            aborted=bool(ctx and ctx.failure_aborted),
        )

        if info.aborted and info.summary == "Something went wrong during processing.":
            info.summary = "The job was stopped."

        self._summary.setText(info.summary)
        self._retry_target = info.retry_screen

        if info.step_title:
            self._step.setText(f"Failed during: {info.step_title}")
            self._step.show()
        elif info.aborted:
            self._step.setText("The run was aborted before it finished.")
            self._step.show()
        else:
            self._step.hide()

        detail_lines: list[str] = []
        if info.detail and info.detail != info.summary:
            detail_lines.append(info.detail)
        if folder is None:
            detail_lines.append("Session folder is not available.")

        if detail_lines:
            set_plain_lines(self._detail, detail_lines)
            self._detail.show()
        else:
            self._detail.hide()

        self._retry.setText(
            "Try again"
            if info.retry_screen in {"E1", "F4"}
            else "Continue"
        )

    def _try_again(self) -> None:
        ctx = self.context()
        target = self._retry_target
        clear_failure_context(ctx)
        self.navigate.emit(target)

    def _go_home(self) -> None:
        clear_failure_context(self.context())
        self.navigate.emit("A1")
