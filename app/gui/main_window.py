"""PySide6 main window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.controller import PiabController
from app.gui.dialogs import confirm_close_while_busy
from app.gui.screens import PLACEHOLDER_SCREENS, SCREEN_TITLES
from app.gui.session_context import SessionContext
from app.gui.views import (
    CameraSetupScreen,
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
from app.gui.widgets.screen_base import ScreenWidget


class MainWindow(QMainWindow):
    def __init__(self, controller: PiabController, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._session_folder: Path | None = None
        self._context = SessionContext()
        self._screens: dict[str, ScreenWidget] = {}
        self._stack = QStackedWidget()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._stack)
        self.setCentralWidget(central)

        self.setWindowTitle("Podcast in a Box")
        self.resize(720, 520)

        self._register_screen(PreflightScreen(controller))
        self._register_screen(WelcomeScreen(controller))
        self._register_screen(ResumeScreen(controller))
        self._register_screen(NewSessionScreen(controller))
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
        self.setStatusBar(self._status)

        self._poll = QTimer(self)
        self._poll.setInterval(2000)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()

        self.navigate("A0")

    def _register_screen(self, screen: ScreenWidget) -> None:
        screen.navigate.connect(self.navigate)
        screen.navigate_session.connect(self._navigate_session)
        screen.bind_context(lambda: self._context)
        self._screens[screen.screen_id] = screen
        self._stack.addWidget(screen)

    def begin_session_flow(self, entry_path: str) -> None:
        self._context.reset(entry_path=entry_path)
        self._session_folder = None
        self.navigate("C1")

    def navigate(self, screen_id: str) -> None:
        screen = self._screens.get(screen_id)
        if screen is None:
            QMessageBox.warning(
                self,
                "Missing screen",
                f"No screen registered for {screen_id!r}.",
            )
            return
        if isinstance(screen, PlaceholderScreen):
            screen.set_session_folder(self._session_folder)
        self._stack.setCurrentWidget(screen)
        title = SCREEN_TITLES.get(screen_id, screen_id)
        self.setWindowTitle(f"Podcast in a Box — {title}")
        self._status.showMessage(f"Screen {screen_id}", 3000)
        screen.on_enter()

    def _navigate_session(self, screen_id: str, session_folder: object) -> None:
        folder = Path(str(session_folder))
        self._session_folder = folder
        self._context.session_folder = folder
        self.navigate(screen_id)

    def _on_poll(self) -> None:
        self.controller.poll_jobs()
        reasons = self.controller.busy_reasons()
        if reasons:
            self._status.showMessage(f"Active: {', '.join(reasons)}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.controller.is_busy():
            self.controller.release_app_lock()
            event.accept()
            return

        if not confirm_close_while_busy(self, self.controller.busy_reasons()):
            event.ignore()
            return

        for job in self.controller.list_jobs():
            if job.status == "running":
                self.controller.abort_job(job.id, confirmed=True)

        self.controller.release_app_lock()
        event.accept()
