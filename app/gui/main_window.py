"""PySide6 wizard window (Home, prep flow, or Final Render)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.controller import PiabController
from app.gui.dialogs import (
    REMOVE_FROM_QUEUE_TEXT,
    confirm_action,
    confirm_cancel_label_apply,
)
from app.gui.screens import PLACEHOLDER_SCREENS, SCREEN_TITLES
from app.gui.session_context import SessionContext
from app.gui.views import (
    CameraSetupScreen,
    CleanWorkingFilesScreen,
    ConfirmSourceScreen,
    CreateSessionScreen,
    DeliveryScreen,
    DoneScreen,
    ErrorScreen,
    EstimateFullScreen,
    EstimatePrepScreen,
    FullRenderScreen,
    ApplyLabelsScreen,
    LabelCamerasScreen,
    LabelMicrophonesScreen,
    NewSessionScreen,
    OneMinReviewScreen,
    SyncOffsetReviewScreen,
    PlaceholderScreen,
    PreflightScreen,
    ProcessingScreen,
    e1_close_requires_confirm,
    RecordingCompleteScreen,
    RecordingSavedScreen,
    RecordingScreen,
    ResumeScreen,
    SessionNameScreen,
    SessionReadyScreen,
    SourceLocationScreen,
    VmixEnsureScreen,
    VmixPresetScreen,
    WelcomeScreen,
)
from app.gui.widgets.autocut_footer import AutocutFooter
from app.gui.widgets.screen_base import ScreenWidget

if TYPE_CHECKING:
    from app.gui.window_manager import WindowManager

HOME_SCREENS = frozenset({"A0", "A1", "A2", "A4"})
FINAL_SCREENS = frozenset({"F4", "F5"})
PREP_SCREENS = frozenset({
    "A3",
    "C1",
    "C2",
    "C2a",
    "C2b",
    "C3",
    "C4",
    "D1",
    "D2",
    "D3",
    "D4",
    "E1",
    "F2",
    "F2a",
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "B6",
})


class MainWindow(QMainWindow):
    def __init__(
        self,
        controller: PiabController,
        parent=None,
        *,
        manager: WindowManager | None = None,
        context: SessionContext | None = None,
        role: str = "home",
        start_screen: str = "A0",
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.manager = manager
        self.role = role
        self._allow_close = False
        self._session_folder: Path | None = None
        self._context = context or SessionContext()
        if self._context.session_folder is not None:
            self._session_folder = self._context.session_folder
        self._screens: dict[str, ScreenWidget] = {}
        self._stack = QStackedWidget()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._stack)
        self._footer = AutocutFooter()
        layout.addWidget(self._footer)
        self.setCentralWidget(central)

        self.setWindowTitle("Podcast in a Box")
        self.resize(720, 520)

        self._register_screen(PreflightScreen(controller))
        self._register_screen(WelcomeScreen(controller))
        self._register_screen(ResumeScreen(controller))
        self._register_screen(NewSessionScreen(controller))
        self._register_screen(CleanWorkingFilesScreen(controller))
        self._register_screen(VmixEnsureScreen(controller))
        self._register_screen(VmixPresetScreen(controller))
        self._register_screen(CameraSetupScreen(controller))
        self._register_screen(RecordingScreen(controller))
        self._register_screen(RecordingCompleteScreen(controller))
        self._register_screen(RecordingSavedScreen(controller))
        self._register_screen(DeliveryScreen(controller))
        self._register_screen(SourceLocationScreen(controller))
        self._register_screen(ConfirmSourceScreen(controller))
        self._register_screen(SessionNameScreen(controller))
        self._register_screen(CreateSessionScreen(controller))
        self._register_screen(SessionReadyScreen(controller))
        self._register_screen(LabelCamerasScreen(controller))
        self._register_screen(LabelMicrophonesScreen(controller))
        self._register_screen(ApplyLabelsScreen(controller))
        self._register_screen(EstimatePrepScreen(controller))
        self._register_screen(ProcessingScreen(controller))
        self._register_screen(SyncOffsetReviewScreen(controller))
        self._register_screen(OneMinReviewScreen(controller))
        self._register_screen(EstimateFullScreen(controller))
        self._register_screen(FullRenderScreen(controller))
        self._register_screen(ErrorScreen(controller))
        self._register_screen(DoneScreen(controller))
        for screen_id in sorted(PLACEHOLDER_SCREENS):
            placeholder = PlaceholderScreen(screen_id, controller)
            self._register_screen(placeholder)

        self._status = QStatusBar()
        self._screen_id_label = QLabel()
        self._status.addWidget(self._screen_id_label)
        self.setStatusBar(self._status)

        self._poll = QTimer(self)
        self._poll.setInterval(2000)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()

        self.navigate(start_screen, _internal=True)

    def _register_screen(self, screen: ScreenWidget) -> None:
        screen.navigate.connect(self.navigate)
        screen.navigate_session.connect(self._navigate_session)
        screen.bind_context(lambda: self._context)
        self._screens[screen.screen_id] = screen
        self._stack.addWidget(screen)

    def begin_session_flow(self, entry_path: str) -> None:
        self._context.reset(entry_path=entry_path)
        self._session_folder = None
        self.navigate("C1", _internal=True)

    def handoff_to_final_render(self, folder: Path) -> None:
        self._leave_current_screen()
        if self.manager is not None:
            self.manager.handoff_to_final_render(folder, source=self)
            return
        self._session_folder = folder
        self._context.session_folder = folder
        self.navigate("F4", _internal=True)

    def close_flow_to_home(self) -> None:
        """Close this Autocut flow window and show Home."""
        self._leave_current_screen()
        if self.manager is not None:
            self.manager.close_flow_to_home(self)
            return
        self.navigate("A1", _internal=True)

    def close_final_render(self) -> None:
        self._allow_close = True
        if self.manager is not None:
            self.manager.close_final_render(self)
            return
        self.navigate("A1", _internal=True)

    def focus_home_keep_final(self) -> None:
        """Switch to Home without aborting or closing this Final Render window."""
        if self.manager is not None:
            self.manager.focus_home()
            return
        self.navigate("A1", _internal=True)

    def allow_close(self) -> None:
        self._allow_close = True

    def request_abort_final(self) -> bool:
        """Confirm, abort Final Render work, close this window, show Home."""
        if not self._abort_final_work(ask_confirm=True):
            return False
        self._allow_close = True
        self.close_final_render()
        return True

    def _current_screen_id(self) -> str:
        widget = self._stack.currentWidget()
        return str(getattr(widget, "screen_id", "") or "")

    def _stop_prep_poll(self) -> None:
        screen = self._screens.get("E1")
        if isinstance(screen, ProcessingScreen):
            screen.prepare_for_abort_close()

    def _e1_queue_status(self) -> str | None:
        folder = self._context.session_folder
        if folder is None:
            return None
        entry = self.controller.job_queue.entry_for(folder, "fast_preview")
        if entry is None:
            return None
        return entry.status

    def _abort_prep_work(self, *, ask_confirm: bool) -> bool:
        folder = self._context.session_folder
        job = None
        if folder is not None:
            job = self.controller.find_running_prep_job(folder)

        if ask_confirm:
            if job is None:
                if not confirm_action(
                    self,
                    title="Remove from queue?",
                    text=REMOVE_FROM_QUEUE_TEXT,
                ):
                    return False
            elif not confirm_action(
                self,
                title="Abort processing?",
                text="Stop the current prep run?",
                detail=REMOVE_FROM_QUEUE_TEXT,
            ):
                return False

        self._stop_prep_poll()
        if job is not None:
            self.controller.abort_job(
                job.id, confirmed=True, advance_queue=True
            )
        elif folder is not None:
            self.controller.cancel_queued_job(folder, "fast_preview")
        return True

    def _stop_final_poll(self) -> None:
        screen = self._screens.get("F4")
        if isinstance(screen, FullRenderScreen):
            screen.prepare_for_abort_close()

    def _abort_final_work(self, *, ask_confirm: bool) -> bool:
        folder = self._context.session_folder
        job = None
        if folder is not None:
            job = self.controller.find_running_render_job(folder)

        if ask_confirm:
            if job is None:
                if not confirm_action(
                    self,
                    title="Remove from queue?",
                    text=REMOVE_FROM_QUEUE_TEXT,
                ):
                    return False
            elif not confirm_action(
                self,
                title="Abort full render?",
                text="Stop the current render?",
                detail=REMOVE_FROM_QUEUE_TEXT,
            ):
                return False

        self._stop_final_poll()
        if job is not None:
            self.controller.abort_job(
                job.id, confirmed=True, advance_queue=True
            )
        elif folder is not None:
            self.controller.cancel_queued_job(folder, "full")
        return True

    def quit_program(self) -> None:
        if self.manager is not None:
            self.manager.quit_program()
            return
        self.controller.interrupt_running_for_quit()
        self.controller.release_app_lock()
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _leave_current_screen(self) -> None:
        widget = self._stack.currentWidget()
        on_leave = getattr(widget, "on_leave", None)
        if callable(on_leave):
            on_leave()

    def navigate(self, screen_id: str, _internal: bool = False) -> None:
        if not _internal and self.manager is not None:
            if self.role == "home" and screen_id not in HOME_SCREENS:
                self._leave_current_screen()
                self.manager.route_from_home(self, screen_id)
                return
            if self.role == "flow" and screen_id in HOME_SCREENS:
                self._leave_current_screen()
                self.manager.ensure_home(navigate_to=screen_id)
                return
            if self.role == "flow" and screen_id in FINAL_SCREENS:
                folder = self._context.session_folder
                if folder is not None:
                    self._leave_current_screen()
                    self.manager.handoff_to_final_render(folder, source=self)
                    return
            if self.role == "final" and screen_id == "A1":
                self._leave_current_screen()
                self.manager.close_final_render(self)
                return
        screen = self._screens.get(screen_id)
        if screen is None:
            QMessageBox.warning(
                self,
                "Missing screen",
                f"No screen registered for {screen_id!r}.",
            )
            return
        if self._stack.currentWidget() is not screen:
            self._leave_current_screen()
        if isinstance(screen, PlaceholderScreen):
            screen.set_session_folder(self._session_folder)
        self._stack.setCurrentWidget(screen)
        title = SCREEN_TITLES.get(screen_id, screen_id)
        prefix = {
            "home": "Podcast in a Box",
            "flow": "Podcast in a Box — Autocut",
            "final": "Final Render",
        }.get(self.role, "Podcast in a Box")
        self.setWindowTitle(f"{prefix} — {title}")
        self._refresh_footer(screen_id)
        self._update_status_bar(screen_id)
        screen.on_enter()

    def _navigate_session(self, screen_id: str, session_folder: object) -> None:
        folder = Path(str(session_folder))
        try:
            self.controller.remember_session_folder(folder)
        except Exception:
            pass
        if self.role == "home" and self.manager is not None:
            if screen_id in FINAL_SCREENS or screen_id == "F4":
                self.manager.open_final(folder)
                return
            self.manager.open_flow(screen_id, folder=folder)
            return
        self._session_folder = folder
        self._context.session_folder = folder
        self.navigate(screen_id, _internal=True)

    def _update_status_bar(self, screen_id: str | None = None) -> None:
        if screen_id is None:
            widget = self._stack.currentWidget()
            screen_id = str(getattr(widget, "screen_id", "") or "")
        parts: list[str] = []
        if screen_id:
            parts.append(f"Screen {screen_id}")
        reasons = self.controller.busy_reasons()
        if reasons:
            parts.append(f"Active: {', '.join(reasons)}")
        self._screen_id_label.setText("  ".join(parts))

    def _refresh_footer(self, screen_id: str | None = None) -> None:
        current = screen_id
        if current is None:
            widget = self._stack.currentWidget()
            current = getattr(widget, "screen_id", "")
        if current == "A1":
            self._footer.set_status("")
            return
        line = self.controller.autocut_status_line()
        prep_notice = bool(line) and current in PREP_SCREENS
        self._footer.set_status(line, prep_notice=prep_notice)

    def _alert_finished_failures(self, finished) -> None:
        from app.gui.failure_alert import alert_workflow_failure

        for job in finished:
            if job.status != "failed" or job.kind == "recording":
                continue
            alert_workflow_failure(
                self,
                working_folder=job.session_folder,
                summary=job.message or "The autocut failed.",
                detail=job.message,
            )

    def _on_poll(self) -> None:
        finished = self.controller.poll_jobs()
        if self.role == "home":
            self._alert_finished_failures(finished)
            welcome = self._screens.get("A1")
            if isinstance(welcome, WelcomeScreen) and self._stack.currentWidget() is welcome:
                welcome._refresh_queue()
        self._update_status_bar()
        self._refresh_footer()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.manager is not None:
            if self.role == "final":
                if not self._allow_close:
                    folder = self._context.session_folder
                    complete = False
                    if folder is not None:
                        try:
                            state = self.controller.load_session_state(folder)
                            complete = state.get("resume_at") == "14_done"
                        except Exception:
                            complete = False
                    if not complete:
                        if not self._abort_final_work(ask_confirm=True):
                            event.ignore()
                            return
                    self._allow_close = True
                self._leave_current_screen()
                self.manager.ensure_home(navigate_to="A1")
                self.manager.window_closed(self)
                event.accept()
                return
            if self.role == "flow" and not self._allow_close and self._current_screen_id() == "D3":
                screen = self._screens.get("D3")
                if isinstance(screen, ApplyLabelsScreen) and screen.is_apply_running():
                    if not confirm_cancel_label_apply(self):
                        event.ignore()
                        return
                    screen.cancel_apply_and_record()
                self._leave_current_screen()
                self.manager.ensure_home(navigate_to="A1")
                self.manager.window_closed(self)
                event.accept()
                return
            if self.role == "flow" and not self._allow_close and self._current_screen_id() == "E1":
                folder = self._context.session_folder
                running = (
                    folder is not None
                    and self.controller.find_running_prep_job(folder) is not None
                )
                if e1_close_requires_confirm(
                    has_running_job=running,
                    queue_status=self._e1_queue_status(),
                ):
                    if not self._abort_prep_work(ask_confirm=True):
                        event.ignore()
                        return
                self._leave_current_screen()
                self.manager.ensure_home(navigate_to="A1")
                self.manager.window_closed(self)
                event.accept()
                return
            self._leave_current_screen()
            self.manager.window_closed(self)
            event.accept()
            return

        if not self.controller.is_busy():
            self._leave_current_screen()
            self.controller.release_app_lock()
            event.accept()
            return

        from app.gui.dialogs import confirm_close_while_busy

        if not confirm_close_while_busy(self, self.controller.busy_reasons()):
            event.ignore()
            return

        self._leave_current_screen()
        self.controller.interrupt_running_for_quit()
        self.controller.release_app_lock()
        event.accept()
