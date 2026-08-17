"""Base class for wizard screens."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from app.gui.session_context import SessionContext


class ScreenWidget(QWidget):
    screen_id: str = ""

    navigate = Signal(str)
    navigate_session = Signal(str, object)  # screen_id, session_folder Path

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._context_provider: Callable[[], SessionContext | None] | None = None

    def bind_context(self, provider: Callable[[], SessionContext | None]) -> None:
        self._context_provider = provider

    def context(self) -> SessionContext | None:
        if self._context_provider is None:
            return None
        return self._context_provider()

    def on_enter(self) -> None:
        """Called when this screen becomes visible."""

    def on_leave(self) -> None:
        """Called when navigating away or when the window is closing."""

    def title(self) -> str:
        from app.gui.screens import SCREEN_TITLES

        return SCREEN_TITLES.get(self.screen_id, self.screen_id)
