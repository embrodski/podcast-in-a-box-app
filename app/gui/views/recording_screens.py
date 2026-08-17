"""Recording flow screens B1–B6."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.controller.paths import ASSETS_DIR, DEFAULT_SCAN_ROOT
from app.gui.dialogs import confirm_action
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label
from app.gui.widgets.worker import CallableWorker


class _StatusScreen(ScreenWidget):
    """Base for auto-run steps with status text."""

    def __init__(self, screen_id: str, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self.screen_id = screen_id
        self._worker: CallableWorker | None = None

        layout = QVBoxLayout(self)
        self._headline = heading_label("")
        layout.addWidget(self._headline)

        self._detail = body_label("")
        layout.addWidget(self._detail)

        layout.addStretch()

        row = QHBoxLayout()
        self._back = QPushButton("Back")
        self._back.clicked.connect(lambda: self.navigate.emit("A3"))
        row.addWidget(self._back)
        row.addStretch()
        self._retry = QPushButton("Retry")
        self._retry.clicked.connect(self.on_enter)
        self._retry.hide()
        row.addWidget(self._retry)
        self._next = QPushButton("Continue")
        self._next.hide()
        self._next.clicked.connect(self._go_next)
        row.addWidget(self._next)
        layout.addLayout(row)

    def _go_next(self) -> None:
        raise NotImplementedError

    def _set_busy(self, message: str) -> None:
        self._detail.setText(message)
        self._retry.setEnabled(False)
        self._next.hide()

    def _set_error(self, message: str) -> None:
        self._detail.setText(message)
        self._retry.show()
        self._retry.setEnabled(True)

    def _run(self, fn, *, on_ok) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = CallableWorker(fn)
        self._worker.finished_ok.connect(on_ok)
        self._worker.failed.connect(self._set_error)
        self._worker.start()


class VmixEnsureScreen(_StatusScreen):
    screen_id = "B1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__("B1", controller, parent)
        self._headline.setText("Starting vMix")
        self._next_screen = "B2"

    def _go_next(self) -> None:
        self.navigate.emit(self._next_screen)

    def on_enter(self) -> None:
        self._retry.hide()
        self._next.hide()
        self._set_busy("Checking whether vMix is running…")
        self._run(self.controller.ensure_vmix_step, on_ok=self._on_ok)

    def _on_ok(self, result) -> None:
        if not result.ok:
            self._set_error(result.message or "vMix could not be started.")
            return
        self._detail.setText(result.message or "vMix is ready.")
        QTimer.singleShot(400, lambda: self.navigate.emit("B2"))


class VmixPresetScreen(_StatusScreen):
    screen_id = "B2"

    def __init__(self, controller, parent=None) -> None:
        super().__init__("B2", controller, parent)
        self._headline.setText("Loading podcast preset")
        self._back.clicked.disconnect()
        self._back.clicked.connect(lambda: self.navigate.emit("B1"))

    def _go_next(self) -> None:
        self.navigate.emit("B3")

    def on_enter(self) -> None:
        self._retry.hide()
        self._next.hide()
        self._set_busy("Opening the PIAB vMix preset…")
        self._run(self.controller.open_vmix_preset_step, on_ok=self._on_ok)

    def _on_ok(self, result) -> None:
        if not result.ok:
            self._set_error(result.message or "Could not open the vMix preset.")
            return
        label = result.preset_path or result.message or "Preset loaded."
        self._detail.setText(label)
        QTimer.singleShot(400, lambda: self.navigate.emit("B3"))


class CameraSetupScreen(ScreenWidget):
    screen_id = "B3"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.addWidget(heading_label("Camera and microphone setup"))
        instructions = body_label(
            "Position cameras and turn microphone levels up to about 80%. "
            "Speakers should be slightly off-center, looking toward the middle of frame, "
            "with eyes near the top guide line in the viewfinder."
        )
        layout.addWidget(instructions)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        images_row = QHBoxLayout(inner)
        self._image_labels: list[QLabel] = []
        for name, filename in (
            ("Left", "piab-camera-left.jpg"),
            ("Right", "piab-camera-right.jpg"),
            ("Wide", "piab-camera-wide.jpg"),
        ):
            col = QVBoxLayout()
            col.addWidget(QLabel(name), alignment=Qt.AlignCenter)
            img = QLabel()
            img.setAlignment(Qt.AlignCenter)
            path = ASSETS_DIR / filename
            if path.is_file():
                pix = QPixmap(str(path))
                img.setPixmap(
                    pix.scaled(200, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                img.setText(f"Missing {filename}")
            col.addWidget(img)
            wrap = QWidget()
            wrap.setLayout(col)
            images_row.addWidget(wrap)
            self._image_labels.append(img)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("B2"))
        row.addWidget(back)
        row.addStretch()
        cont = QPushButton("Continue")
        cont.setDefault(True)
        cont.setMinimumHeight(40)
        cont.clicked.connect(lambda: self.navigate.emit("B4"))
        row.addWidget(cont)
        layout.addLayout(row)


class RecordingScreen(ScreenWidget):
    screen_id = "B4"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._recording_started = False
        self._enter_token = 0

        layout = QVBoxLayout(self)

        self._warmup_page = QWidget()
        warmup_layout = QVBoxLayout(self._warmup_page)
        warmup_layout.setContentsMargins(0, 0, 0, 0)
        warmup_layout.addStretch()
        self._warmup_label = heading_label(
            "Please wait, warming up. This will take ~15 sec.",
            word_wrap=True,
        )
        self._warmup_label.setAlignment(Qt.AlignCenter)
        self._warmup_label.setStyleSheet("font-size: 32px; font-weight: 600;")
        warmup_layout.addWidget(self._warmup_label)
        warmup_layout.addStretch()
        layout.addWidget(self._warmup_page, stretch=1)

        self._recording_panel = QWidget()
        rec = QVBoxLayout(self._recording_panel)
        rec.setContentsMargins(0, 0, 0, 0)
        rec.addWidget(heading_label("Recording"))
        self._instructions = body_label("")
        rec.addWidget(self._instructions)

        self._status = body_label("")
        rec.addWidget(self._status)

        rec.addStretch()

        self._stop = QPushButton("Stop recording")
        self._stop.setMinimumHeight(48)
        self._stop.setStyleSheet("font-weight: 600;")
        self._stop.setEnabled(False)
        self._stop.clicked.connect(self._stop_recording)
        rec.addWidget(self._stop)

        back = QPushButton("Back")
        back.clicked.connect(self._confirm_back)
        rec.addWidget(back)
        layout.addWidget(self._recording_panel, stretch=1)
        self._show_warmup()

    def _show_warmup(self) -> None:
        self._recording_panel.hide()
        self._warmup_page.show()

    def _show_recording(self) -> None:
        self._warmup_page.hide()
        self._recording_panel.show()

    def on_enter(self) -> None:
        self._enter_token += 1
        token = self._enter_token
        self._recording_started = False
        self._stop.setEnabled(False)
        self._show_warmup()
        try:
            self._instructions.setText(self.controller.recording_instructions())
        except Exception as exc:
            self._show_recording()
            self._status.setText(str(exc))
            return

        from app.controller.storage_gate import assess_recording_storage
        from app.gui.storage_prompts import gate_low_disk

        assessment = assess_recording_storage()
        disk_action = gate_low_disk(
            self,
            assessment,
            return_screen="B4",
            critical_recording=True,
        )
        if disk_action == "go_clean":
            return
        if disk_action == "abort":
            self.navigate.emit("B3")
            return

        if self.controller.multicorder_is_active():
            self._show_recording()
            already_action = self._ask_already_recording()
            if already_action is None:
                self.navigate.emit("B3")
                return
            self._start_multicorder(already_action)
            return

        self._worker = CallableWorker(self.controller.warmup_cameras_for_recording)
        self._worker.finished_ok.connect(lambda _result: self._after_warmup(token))
        self._worker.failed.connect(lambda _message: self._after_warmup(token))
        self._worker.start()

    def _ask_already_recording(self) -> str | None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("MultiCorder already recording")
        box.setText("MultiCorder is already recording.")
        box.setInformativeText(
            "Continue the current session, or stop and start a new recording?"
        )
        btn_continue = box.addButton(
            "Continue current recording", QMessageBox.AcceptRole
        )
        btn_restart = box.addButton(
            "Stop and restart", QMessageBox.DestructiveRole
        )
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked == box.button(QMessageBox.Cancel):
            return None
        return "continue" if clicked == btn_continue else "restart"

    def _after_warmup(self, token: int) -> None:
        if token != self._enter_token or not self.isVisible():
            return
        self._show_recording()
        self._start_multicorder(None)

    def _start_multicorder(self, already_recording_action: str | None) -> None:
        self._status.setText("Starting MultiCorder…")
        self._worker = CallableWorker(
            self.controller.begin_recording,
            already_recording_action=already_recording_action,
        )
        self._worker.finished_ok.connect(self._on_started)
        self._worker.failed.connect(self._on_start_failed)
        self._worker.start()

    def _on_started(self, _job) -> None:
        self._recording_started = True
        self._status.setText("Recording is in progress.")
        self._stop.setEnabled(True)

    def _on_start_failed(self, message: str) -> None:
        self._status.setText(message)
        QMessageBox.warning(self, "Could not start recording", message)

    def _stop_recording(self) -> None:
        if not confirm_action(
            self,
            title="Stop recording",
            text="Stop MultiCorder recording now?",
            detail="Only press this when the podcast is finished.",
        ):
            return
        self._stop.setEnabled(False)
        self._status.setText("Stopping recording…")
        worker = CallableWorker(self.controller.finish_recording)
        worker.finished_ok.connect(lambda _job: self.navigate.emit("B5"))
        worker.failed.connect(
            lambda msg: QMessageBox.warning(self, "Stop failed", msg)
        )
        worker.start()
        self._worker = worker

    def _confirm_back(self) -> None:
        if self._recording_started:
            if not confirm_action(
                self,
                title="Recording in progress",
                text="Go back while recording?",
                detail="This will not stop MultiCorder. Use Stop recording first.",
            ):
                return
        self.navigate.emit("B3")


class RecordingCompleteScreen(ScreenWidget):
    screen_id = "B5"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        layout = QVBoxLayout(self)
        layout.addWidget(heading_label("Recording complete"))
        self._blurb = body_label(
            f"Your MultiCorder files are in:\n{DEFAULT_SCAN_ROOT}"
        )
        layout.addWidget(self._blurb)
        layout.addStretch()

        autocut = QPushButton("Continue to autocut")
        autocut.setMinimumHeight(44)
        autocut.clicked.connect(self._continue_autocut)
        layout.addWidget(autocut)

        stop = QPushButton("Stop — save files only")
        stop.setMinimumHeight(44)
        stop.clicked.connect(lambda: self.navigate.emit("B6"))
        layout.addWidget(stop)

    def _continue_autocut(self) -> None:
        ctx = self.context()
        if ctx is not None:
            ctx.source_mode = "default"
        self.navigate.emit("C2b")


class RecordingSavedScreen(ScreenWidget):
    screen_id = "B6"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        layout = QVBoxLayout(self)
        layout.addWidget(heading_label("Files saved"))
        layout.addWidget(
            body_label(
                f"Recording files remain in:\n{DEFAULT_SCAN_ROOT}\n\n"
                "You can copy them to a memory stick or start autocut later from the home screen."
            )
        )
        layout.addStretch()
        done = QPushButton("Done")
        done.clicked.connect(self._close_done)
        layout.addWidget(done)

    def _close_done(self) -> None:
        window = self.window()
        if hasattr(window, "close_flow_to_home"):
            window.close_flow_to_home()
            return
        self.navigate.emit("A1")
