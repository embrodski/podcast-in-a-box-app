"""Recording flow screens B1–B6."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.controller.paths import ASSETS_DIR, DEFAULT_SCAN_ROOT
from app.controller.recording import recording_warmup_phrases_html
from app.gui.dialogs import confirm_action
from app.gui.recording_alarm import (
    RECORDING_ALARM_URGENT_MS,
    RECORDING_STOPPED_TEXT,
    alarm_flash_stylesheet,
    alarm_label_style,
    bring_window_to_front,
    ping_alarm_beep,
    raise_app_window,
    release_alarm_window,
    start_alarm_sound,
    stop_alarm_sound,
)
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label
from app.gui.widgets.worker import CallableWorker, start_callable_worker


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

        self._enter_token = 0
        self._stay_after_success = False

        row = QHBoxLayout()
        self._back = QPushButton("Back")
        self._back.clicked.connect(self._go_back)
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

    def _go_back(self) -> None:
        self.navigate.emit("C1")

    def _go_next(self) -> None:
        raise NotImplementedError

    def on_leave(self) -> None:
        self._enter_token += 1

    def _advance_after_ok(self, screen_id: str) -> None:
        if self._stay_after_success:
            self._next.show()
            self._next.setEnabled(True)
            return
        token = self._enter_token
        QTimer.singleShot(400, lambda: self._emit_if_current(token, screen_id))

    def _emit_if_current(self, token: int, screen_id: str) -> None:
        if token != self._enter_token or not self.isVisible():
            return
        self.navigate.emit(screen_id)

    def _mark_skip_vmix_auto_advance(self) -> None:
        ctx = self.context()
        if ctx is not None:
            ctx.skip_vmix_auto_advance = True

    def _set_busy(self, message: str) -> None:
        self._detail.setText(message)
        self._retry.setEnabled(False)
        self._next.hide()

    def _set_error(self, message: str) -> None:
        self._detail.setText(message)
        self._retry.show()
        self._retry.setEnabled(True)

    def _run(self, fn, *, on_ok) -> None:
        start_callable_worker(
            self,
            fn,
            on_ok=on_ok,
            on_fail=self._set_error,
        )


class VmixEnsureScreen(_StatusScreen):
    screen_id = "B1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__("B1", controller, parent)
        self._headline.setText("Starting vMix")
        self._next_screen = "B2"

    def _go_next(self) -> None:
        self.navigate.emit(self._next_screen)

    def on_enter(self) -> None:
        self._enter_token += 1
        ctx = self.context()
        stay = bool(ctx is not None and ctx.skip_vmix_auto_advance)
        if ctx is not None:
            ctx.skip_vmix_auto_advance = False
        self._stay_after_success = stay
        self._retry.hide()
        self._next.hide()
        if stay:
            self._detail.setText("vMix is ready.")
            self._next.show()
            return
        self._set_busy("Checking whether vMix is running…")
        self._run(self.controller.ensure_vmix_step, on_ok=self._on_ok)

    def _on_ok(self, result) -> None:
        if not result.ok:
            self._set_error(result.message or "vMix could not be started.")
            return
        self._detail.setText(result.message or "vMix is ready.")
        self._advance_after_ok("B2")


class VmixPresetScreen(_StatusScreen):
    screen_id = "B2"

    def __init__(self, controller, parent=None) -> None:
        super().__init__("B2", controller, parent)
        self._headline.setText("Opening vMix and loading preset. This will take a minute.")

    def _go_back(self) -> None:
        self._mark_skip_vmix_auto_advance()
        self.navigate.emit("B1")

    def _go_next(self) -> None:
        self.navigate.emit("B3")

    def on_enter(self) -> None:
        self._enter_token += 1
        ctx = self.context()
        stay = bool(ctx is not None and ctx.skip_vmix_auto_advance)
        if ctx is not None:
            ctx.skip_vmix_auto_advance = False
        self._stay_after_success = stay
        self._retry.hide()
        self._next.hide()
        if stay:
            self._detail.setText("vMix preset is ready.")
            self._next.show()
            self._next.setEnabled(True)
            return
        self._set_busy("Opening the PIAB vMix preset…")
        self._run(self.controller.open_vmix_preset_step, on_ok=self._on_ok)

    def _on_ok(self, result) -> None:
        if not result.ok:
            self._set_error(result.message or "Could not open the vMix preset.")
            return
        label = result.preset_path or result.message or "Preset loaded."
        self._detail.setText(label)
        raise_app_window(self)
        self._advance_after_ok("B3")


def _scaled_asset_label(
    filename: str,
    *,
    width: int,
    height: int,
    alignment: Qt.AlignmentFlag = Qt.AlignCenter,
) -> QLabel:
    img = QLabel()
    img.setAlignment(alignment)
    img.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    path = ASSETS_DIR / filename
    if path.is_file():
        pix = QPixmap(str(path))
        img.setPixmap(pix.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    else:
        img.setText(f"Missing {filename}")
    return img


class CameraSetupScreen(ScreenWidget):
    screen_id = "B3"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        content_layout.addWidget(heading_label("Camera setup"))
        content_layout.addWidget(
            body_label(
                "Position cameras so speakers are slightly off-center, looking toward "
                "the middle of frame, with eyes near the top guide line in the viewfinder."
            )
        )

        images_row = QHBoxLayout()
        images_row.setContentsMargins(0, 0, 0, 0)
        images_row.setSpacing(12)
        images_row.setAlignment(Qt.AlignTop)
        self._image_labels: list[QLabel] = []
        for name, filename in (
            ("Left", "piab-camera-left.jpg"),
            ("Right", "piab-camera-right.jpg"),
            ("Wide", "piab-camera-wide.jpg"),
        ):
            col = QVBoxLayout()
            caption = QLabel(name)
            caption.setStyleSheet("font-size: 18px; font-weight: 700;")
            caption.ensurePolished()
            blank_line = caption.fontMetrics().lineSpacing()
            col.setContentsMargins(0, blank_line, 0, 0)
            col.setSpacing(blank_line)
            caption.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            caption.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            caption.setFixedHeight(max(1, caption.fontMetrics().height()))
            col.addWidget(caption)
            img = _scaled_asset_label(filename, width=200, height=150)
            col.addWidget(img)
            wrap = QWidget()
            wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            wrap.setLayout(col)
            images_row.addWidget(wrap, alignment=Qt.AlignTop)
            self._image_labels.append(img)
        cameras = QWidget()
        cameras.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        cameras.setLayout(images_row)
        content_layout.addWidget(cameras)

        content_layout.addWidget(heading_label("Microphone setup"))
        content_layout.addWidget(
            body_label(
                "Make sure any microphones in use are set at 80%-100% volume"
            )
        )
        content_layout.addWidget(
            _scaled_asset_label("volume-control.jpg", width=560, height=360)
        )
        content_layout.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self._go_back)
        row.addWidget(back)
        row.addStretch()
        cont = QPushButton("Continue")
        cont.setDefault(True)
        cont.setMinimumHeight(40)
        cont.clicked.connect(lambda: self.navigate.emit("B4"))
        row.addWidget(cont)
        layout.addLayout(row)
        self._enter_token = 0

    def on_enter(self) -> None:
        self._enter_token += 1
        token = self._enter_token
        raise_app_window(self)
        # vMix often reclaims focus after the preset finishes loading.
        for delay_ms in (500, 1500, 3000):
            QTimer.singleShot(
                delay_ms,
                lambda t=token: self._raise_over_vmix_if(t),
            )

    def on_leave(self) -> None:
        self._enter_token += 1

    def _raise_over_vmix_if(self, token: int) -> None:
        if token != self._enter_token or not self.isVisible():
            return
        raise_app_window(self)

    def _go_back(self) -> None:
        ctx = self.context()
        if ctx is not None:
            ctx.skip_vmix_auto_advance = True
        self.navigate.emit("B2")


_RECORDING_OK_STYLE = "color: #4ade80; font-size: 18px; font-weight: 600;"
_RECORDING_BAD_STYLE = "color: #f87171; font-size: 22px; font-weight: 700;"
_HEARTBEAT_MS = 2000
_ALARM_BLINK_MS = 100
_ALARM_BEEP_MS = 500


class RecordingScreen(ScreenWidget):
    screen_id = "B4"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._recording_started = False
        self._recording_confirmed = False
        self._lost_alert_shown = False
        self._alarm_flash_on = False
        self._enter_token = 0
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(_HEARTBEAT_MS)
        self._heartbeat.timeout.connect(self._on_heartbeat)
        self._alarm_blink = QTimer(self)
        self._alarm_blink.setInterval(_ALARM_BLINK_MS)
        self._alarm_blink.timeout.connect(self._on_alarm_blink)
        self._alarm_beep = QTimer(self)
        self._alarm_beep.setInterval(_ALARM_BEEP_MS)
        self._alarm_beep.timeout.connect(ping_alarm_beep)
        self._alarm_urgent = QTimer(self)
        self._alarm_urgent.setSingleShot(True)
        self._alarm_urgent.setInterval(RECORDING_ALARM_URGENT_MS)
        self._alarm_urgent.timeout.connect(self._end_urgent_alarm)

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
        warmup_layout.addSpacing(36)
        self._warmup_phrases = body_label("")
        self._warmup_phrases.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._warmup_phrases.setTextFormat(Qt.TextFormat.RichText)
        warmup_layout.addWidget(self._warmup_phrases)
        warmup_layout.addStretch()
        layout.addWidget(self._warmup_page, stretch=1)

        self._recording_panel = QWidget()
        rec = QVBoxLayout(self._recording_panel)
        rec.setContentsMargins(0, 0, 0, 0)
        heading = heading_label("Recording")
        heading.setAlignment(Qt.AlignCenter)
        rec.addWidget(heading)
        self._heading = heading
        self._instructions = body_label("")
        self._instructions.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._instructions.setTextFormat(Qt.TextFormat.RichText)
        rec.addWidget(self._instructions)

        self._status = body_label("")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setWordWrap(True)
        rec.addWidget(self._status)

        self._alarm_overlay = QWidget()
        self._alarm_overlay.hide()
        alarm_layout = QVBoxLayout(self._alarm_overlay)
        alarm_layout.setContentsMargins(8, 8, 8, 8)
        alarm_layout.addStretch()
        self._alarm_label = QLabel(RECORDING_STOPPED_TEXT)
        self._alarm_label.setAlignment(Qt.AlignCenter)
        self._alarm_label.setWordWrap(True)
        self._alarm_label.setStyleSheet(alarm_label_style())
        alarm_layout.addWidget(self._alarm_label)
        self._alarm_detail = body_label("")
        self._alarm_detail.setAlignment(Qt.AlignCenter)
        self._alarm_detail.setStyleSheet("color: #fee2e2; font-size: 18px; font-weight: 600;")
        alarm_layout.addWidget(self._alarm_detail)
        alarm_layout.addStretch()
        rec.addWidget(self._alarm_overlay, stretch=1)

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
        self._stop_heartbeat()
        self._stop_lost_alarm()
        self._recording_started = False
        self._recording_confirmed = False
        self._stop.setEnabled(False)
        self._set_status("", error=False)
        self._show_warmup()
        try:
            self._instructions.setText(self.controller.recording_instructions())
            self._warmup_phrases.setText(recording_warmup_phrases_html())
        except Exception as exc:
            self._show_recording()
            self._set_status(str(exc), error=True)
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

        probe = self.controller.probe_multicorder()
        if probe.status == "unreachable":
            self._show_recording()
            message = probe.message or "Could not read vMix MultiCorder state."
            self._set_status(message, error=True)
            QMessageBox.warning(self, "Cannot confirm vMix recording", message)
            return
        if probe.is_recording:
            self._show_recording()
            already_action = self._ask_already_recording()
            if already_action is None:
                self.navigate.emit("B3")
                return
            self._start_multicorder(already_action)
            return

        start_callable_worker(
            self,
            self.controller.warmup_cameras_for_recording,
            on_ok=lambda _result: self._after_warmup(token),
            on_fail=self._on_warmup_failed,
        )

    def on_leave(self) -> None:
        self._enter_token += 1
        self._stop_heartbeat()
        self._stop_lost_alarm()

    def _set_status(self, message: str, *, error: bool) -> None:
        self._status.setText(message)
        self._status.setStyleSheet(_RECORDING_BAD_STYLE if error else _RECORDING_OK_STYLE)

    def _start_heartbeat(self) -> None:
        self._heartbeat.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat.stop()

    def _on_warmup_failed(self, message: str) -> None:
        if not self.isVisible():
            return
        self._show_recording()
        self._set_status(message, error=True)
        QMessageBox.warning(self, "Camera warmup failed", message)

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
        self._set_status("Starting MultiCorder…", error=False)
        start_callable_worker(
            self,
            self.controller.begin_recording,
            already_recording_action=already_recording_action,
            on_ok=self._on_started,
            on_fail=self._on_start_failed,
        )

    def _on_started(self, _job) -> None:
        if not self.isVisible():
            return
        self._recording_started = True
        self._recording_confirmed = True
        self._stop_lost_alarm()
        self._set_status("Recording is in progress.", error=False)
        self._stop.setEnabled(True)
        self._start_heartbeat()

    def _on_start_failed(self, message: str) -> None:
        if not self.isVisible():
            return
        self._stop_heartbeat()
        self._recording_confirmed = False
        self._stop_lost_alarm()
        self._set_status(message, error=True)
        QMessageBox.warning(self, "Could not start recording", message)

    def _on_heartbeat(self) -> None:
        if not self._recording_confirmed or not self.isVisible():
            return
        lost = self.controller.recording_lost_message(timeout_sec=1.5)
        if lost is None:
            if self._lost_alert_shown:
                self._stop_lost_alarm()
                self._set_status("Recording is in progress.", error=False)
            return
        self._start_lost_alarm(lost)

    def _start_lost_alarm(self, detail: str) -> None:
        already = self._lost_alert_shown
        self._lost_alert_shown = True
        self._heading.hide()
        self._instructions.hide()
        self._status.hide()
        self._alarm_overlay.show()
        self._alarm_detail.setText(detail)
        self._alarm_overlay.setStyleSheet(alarm_flash_stylesheet(on=True))
        self._alarm_label.setVisible(True)
        if already:
            return
        start_alarm_sound()
        ping_alarm_beep()
        self._alarm_flash_on = True
        bring_window_to_front(self)
        self._alarm_blink.start()
        self._alarm_beep.start()
        self._alarm_urgent.start()

    def _end_urgent_alarm(self) -> None:
        """After 2s: keep flashing, but stop sound and always-on-top."""
        if not self._lost_alert_shown:
            return
        self._alarm_beep.stop()
        stop_alarm_sound()
        release_alarm_window(self)

    def _stop_lost_alarm(self) -> None:
        was_alarming = self._lost_alert_shown
        self._lost_alert_shown = False
        self._alarm_blink.stop()
        self._alarm_beep.stop()
        self._alarm_urgent.stop()
        stop_alarm_sound()
        if was_alarming:
            release_alarm_window(self)
        self._alarm_overlay.hide()
        self._heading.show()
        self._instructions.show()
        self._status.show()
        self._alarm_label.setVisible(True)
        self._alarm_overlay.setStyleSheet("")

    def _on_alarm_blink(self) -> None:
        self._alarm_flash_on = not self._alarm_flash_on
        self._alarm_overlay.setStyleSheet(
            alarm_flash_stylesheet(on=self._alarm_flash_on)
        )
        self._alarm_label.setVisible(not self._alarm_label.isVisible())

    def _stop_recording(self) -> None:
        if not confirm_action(
            self,
            title="Stop recording",
            text="Stop MultiCorder recording now?",
            detail="Only press this when the podcast is finished.",
        ):
            return
        self._stop.setEnabled(False)
        self._stop_heartbeat()
        self._recording_confirmed = False
        self._stop_lost_alarm()
        self._set_status("Stopping recording…", error=False)
        start_callable_worker(
            self,
            self.controller.finish_recording,
            on_ok=lambda _job: self._continue_to_autocut(),
            on_fail=lambda msg: QMessageBox.warning(self, "Stop failed", msg),
        )

    def _continue_to_autocut(self) -> None:
        ctx = self.context()
        if ctx is not None:
            ctx.source_mode = "default"
        self.navigate.emit("C3")

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
