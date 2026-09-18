"""Background work for GUI screens."""

from __future__ import annotations

import inspect
import warnings
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


def start_callable_worker(
    owner: object,
    fn: Callable[..., Any],
    *args: Any,
    attr: str = "_worker",
    on_ok: Callable[..., Any] | None = None,
    on_fail: Callable[..., Any] | None = None,
    on_progress: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> CallableWorker | None:
    """Start a CallableWorker on ``owner.attr`` unless one is already running."""
    current = getattr(owner, attr, None)
    if isinstance(current, QThread) and current.isRunning():
        return None
    worker = CallableWorker(fn, *args, **kwargs)
    if on_ok is not None:
        worker.finished_ok.connect(on_ok)
    if on_fail is not None:
        worker.failed.connect(on_fail)
    if on_progress is not None:
        worker.progress.connect(on_progress)
    setattr(owner, attr, worker)
    worker.start()
    return worker


def stop_callable_worker(
    worker: CallableWorker | None,
    *,
    wait_ms: int | None = None,
) -> None:
    """Disconnect signals; optionally wait for the thread to finish."""
    if worker is None:
        return
    for signal in (worker.finished_ok, worker.failed, worker.progress):
        _disconnect_all(signal)
    if wait_ms is not None and worker.isRunning():
        worker.wait(wait_ms)


def _disconnect_all(signal: Any) -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"libpyside: Failed to disconnect",
            category=RuntimeWarning,
        )
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass
