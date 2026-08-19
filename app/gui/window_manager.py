"""Multi-window shell: Home, prep flow, and per-job Final Render."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget

from app.controller import PiabController
from app.gui.interrupt_dialog import prompt_interrupted_job
from app.gui.main_window import MainWindow
from app.gui.session_context import SessionContext

HOME_SCREENS = frozenset({"A0", "A1", "A2", "A4"})
FINAL_SCREENS = frozenset({"F4", "F5"})
CASCADE_STEP_PX = 40


def cascaded_position(
    *,
    anchor_x: int,
    anchor_y: int,
    step: int,
    index: int,
    width: int,
    height: int,
    avail_x: int,
    avail_y: int,
    avail_width: int,
    avail_height: int,
) -> tuple[int, int]:
    """Offset a new window from an anchor and keep it on-screen."""
    x = anchor_x + step * index
    y = anchor_y + step * index
    max_x = avail_x + avail_width - width
    max_y = avail_y + avail_height - height
    if max_x < avail_x:
        max_x = avail_x
    if max_y < avail_y:
        max_y = avail_y
    span_x = max(1, max_x - avail_x + 1)
    span_y = max(1, max_y - avail_y + 1)
    if x > max_x:
        x = avail_x + (x - avail_x) % span_x
    if y > max_y:
        y = avail_y + (y - avail_y) % span_y
    return min(max(x, avail_x), max_x), min(max(y, avail_y), max_y)


class WindowManager:
    def __init__(self, controller: PiabController) -> None:
        self.controller = controller
        self.home: MainWindow | None = None
        self.flows: list[MainWindow] = []
        self.finals: dict[str, MainWindow] = {}
        self._interrupt_prompted = False
        self._quitting = False

    def start(self) -> MainWindow:
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)
        self.home = self._make_window(role="home", start_screen="A0")
        self.home.show()
        return self.home

    def _make_window(
        self,
        *,
        role: str,
        start_screen: str,
        context: SessionContext | None = None,
        title_prefix: str | None = None,
    ) -> MainWindow:
        window = MainWindow(
            self.controller,
            manager=self,
            context=context or SessionContext(),
            role=role,
            start_screen=start_screen,
        )
        if title_prefix:
            window.setWindowTitle(title_prefix)
        window.setWindowModality(Qt.WindowModality.NonModal)
        return window

    def ensure_home(
        self,
        *,
        navigate_to: str = "A1",
        activate: bool = True,
    ) -> MainWindow:
        if self.home is None:
            self.home = self._make_window(role="home", start_screen=navigate_to)
        self.home.show()
        if activate:
            self.home.raise_()
            self.home.activateWindow()
        if navigate_to and self.home._stack.currentWidget().screen_id != navigate_to:
            self.home.navigate(navigate_to, _internal=True)
        self._maybe_prompt_interrupted()
        return self.home

    def focus_home(self) -> MainWindow:
        """Bring Home to the front without closing Final Render windows."""
        return self.ensure_home(navigate_to="A1", activate=True)

    def route_from_home(self, _home: MainWindow, screen_id: str) -> None:
        if screen_id in HOME_SCREENS:
            self.ensure_home(navigate_to=screen_id)
            return
        self.open_flow(screen_id)

    def open_flow(self, screen_id: str, *, folder: Path | None = None) -> MainWindow:
        if folder is not None:
            existing = self._flow_for_folder(folder)
            if existing is not None:
                existing.show()
                existing.raise_()
                existing.navigate(screen_id, _internal=True)
                return existing
            final = self.finals.get(str(folder.resolve()))
            if final is not None:
                final.show()
                final.raise_()
                return final
        ctx = SessionContext()
        if folder is not None:
            ctx.session_folder = folder
        window = self._make_window(
            role="flow",
            start_screen=screen_id,
            context=ctx,
            title_prefix="Podcast in a Box — Autocut",
        )
        if folder is not None:
            window._session_folder = folder
        if getattr(window, "_allow_close", False):
            # E1 handed off to Final during construction — do not show this shell again.
            final = self.finals.get(str(folder.resolve())) if folder is not None else None
            return final if final is not None else window
        self.flows.append(window)
        window.show()
        self._cascade_new_window(window)
        window.raise_()
        return window

    def _cascade_index(self, window: MainWindow) -> int:
        others = sum(1 for item in self.flows if item is not window)
        others += sum(1 for item in self.finals.values() if item is not window)
        return others + 1

    def _cascade_new_window(self, window: MainWindow) -> None:
        """Nudge a newly created session window off Home so both stay visible."""
        anchor = self.home if self.home is not None and self.home.isVisible() else None
        if anchor is None:
            return
        screen = anchor.screen()
        if screen is None:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = window.frameGeometry()
        x, y = cascaded_position(
            anchor_x=anchor.frameGeometry().x(),
            anchor_y=anchor.frameGeometry().y(),
            step=CASCADE_STEP_PX,
            index=self._cascade_index(window),
            width=frame.width() or window.width(),
            height=frame.height() or window.height(),
            avail_x=avail.x(),
            avail_y=avail.y(),
            avail_width=avail.width(),
            avail_height=avail.height(),
        )
        window.move(x, y)

    def _flow_for_folder(self, folder: Path) -> MainWindow | None:
        key = str(folder.resolve())
        for window in self.flows:
            ctx_folder = window._context.session_folder
            if ctx_folder is not None and str(ctx_folder.resolve()) == key:
                return window
        return None

    def handoff_to_final_render(self, folder: Path, source: MainWindow | None = None) -> MainWindow:
        # Leave Home where it is (default position if newly created). Final stays focused.
        self.ensure_home(navigate_to="A1", activate=False)
        final = self.open_final(folder)
        final.raise_()
        final.activateWindow()
        if source is not None and source.role == "flow":
            source.allow_close()
            source.close()
        return final

    def open_final(self, folder: Path) -> MainWindow:
        key = str(folder.resolve())
        existing = self.finals.get(key)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            existing.navigate("F4", _internal=True)
            return existing
        ctx = SessionContext()
        ctx.session_folder = folder
        window = self._make_window(
            role="final",
            start_screen="F4",
            context=ctx,
            title_prefix=f"Final Render — {folder.name}",
        )
        window._session_folder = folder
        self.finals[key] = window
        window.show()
        self._cascade_new_window(window)
        window.raise_()
        window.activateWindow()
        return window

    def close_flow_to_home(self, window: MainWindow) -> None:
        home = self.ensure_home(navigate_to="A1")
        if window in self.flows:
            self.flows.remove(window)
        window.allow_close()
        window.close()
        home.show()
        home.raise_()
        home.activateWindow()

    def close_final_render(self, window: MainWindow) -> None:
        home = self.ensure_home(navigate_to="A1")
        key = None
        if window._context.session_folder is not None:
            key = str(window._context.session_folder.resolve())
        window.allow_close()
        window.close()
        if key:
            self.finals.pop(key, None)
        home.show()
        home.raise_()
        home.activateWindow()

    def window_closed(self, window: MainWindow) -> None:
        if self._quitting:
            return
        if window is self.home:
            self.home = None
            return
        if window in self.flows:
            self.flows.remove(window)
        keys = [key for key, value in self.finals.items() if value is window]
        for key in keys:
            self.finals.pop(key, None)

    def quit_program(self) -> None:
        self._quitting = True
        self.controller.interrupt_running_for_quit()
        self.controller.release_app_lock()
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(True)
            app.quit()

    def _maybe_prompt_interrupted(self) -> None:
        if self._interrupt_prompted or self.home is None:
            return
        pending = self.controller.list_interrupted_jobs()
        if not pending:
            return
        self._interrupt_prompted = True
        QTimer.singleShot(0, lambda: self._run_interrupt_prompts(pending))

    def _run_interrupt_prompts(self, pending: list[dict]) -> None:
        parent: QWidget | None = self.home
        for row in pending:
            folder = Path(str(row.get("folder") or ""))
            lane = str(row.get("lane") or "full")
            if not folder:
                continue
            choice = prompt_interrupted_job(
                parent,
                folder=folder,
                lane=lane,
                name=str(row.get("name") or folder.name),
            )
            if choice == "resume":
                self.controller.resume_interrupted(folder, lane)  # type: ignore[arg-type]
                if lane == "full":
                    self.open_final(folder)
                else:
                    self.open_flow("E1", folder=folder)
            elif choice == "abort":
                self.controller.abort_interrupted(folder, lane)  # type: ignore[arg-type]
        self.ensure_home(navigate_to="A1")
