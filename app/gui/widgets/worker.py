"""Background work for GUI screens."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal


class CallableWorker(QThread):
    """Run a callable on a background thread."""

    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            kwargs = dict(self._kwargs)
            try:
                if "progress" in inspect.signature(self._fn).parameters:
                    kwargs["progress"] = self.progress.emit
            except (TypeError, ValueError):
                pass
            result = self._fn(*self._args, **kwargs)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit(result)
