"""Screen A1 — welcome / home."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.controller.paths import DEFAULT_SCAN_ROOT, DEFAULT_WORK_ROOT
from app.controller.storage_gate import (
    CLEAN_WORKING_FILES_BUTTON,
    clean_working_files_button_text,
    free_bytes_at,
)
from app.gui.widgets.autocut_footer import AutocutFooter
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label


class WelcomeScreen(ScreenWidget):
    screen_id = "A1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = heading_label("Podcast in a Box")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)

        blurb = body_label(
            "Record a podcast in the Lighthaven room, or bring back files from a "
            "previous session to autocut an interview."
        )
        folder_blue = "#60a5fa"
        sessions_green = "#4ade80"
        podcast_room = f'<span style="color:{folder_blue};">PodcastRoom</span>'
        podcast_in_a_box = f'<span style="color:{folder_blue};">PodcastInABox</span>'
        sessions = f'<span style="color:{sessions_green};">Sessions</span>'
        paths = body_label("")
        paths.setTextFormat(Qt.TextFormat.RichText)
        paths.setContentsMargins(0, 0, 0, 0)
        paths.setText(
            f"All recorded audio and video is saved to the {podcast_room} "
            "folder in E:\\ (\"New Volume\")<br>"
            f"All autocut sessions saved in the folder E: -&gt; {podcast_room} "
            f"-&gt; {podcast_in_a_box} -&gt; {sessions}"
        )
        intro = QWidget()
        intro_layout = QVBoxLayout(intro)
        intro_layout.setContentsMargins(0, 0, 0, 0)
        intro_layout.setSpacing(10)
        intro_layout.addWidget(blurb)
        intro_layout.addWidget(paths)
        layout.addWidget(intro)

        self._banner = body_label("")
        self._banner.setStyleSheet("color: #a00; font-weight: 600;")
        self._banner.hide()
        layout.addWidget(self._banner)

        new_btn = QPushButton("New session")
        new_btn.setMinimumHeight(44)
        new_btn.clicked.connect(lambda: self.navigate.emit("A3"))
        layout.addWidget(new_btn)

        resume_btn = QPushButton("Resume session")
        resume_btn.setMinimumHeight(44)
        resume_btn.clicked.connect(lambda: self.navigate.emit("A2"))
        layout.addWidget(resume_btn)

        self._clean_btn = QPushButton(CLEAN_WORKING_FILES_BUTTON)
        self._clean_btn.setMinimumHeight(44)
        self._clean_btn.clicked.connect(self._open_clean)
        layout.addWidget(self._clean_btn)

        self._status_line = AutocutFooter()
        layout.addWidget(self._status_line)

        self._queue_heading = heading_label("Autocut queue")
        self._queue_heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self._queue_heading)
        self._queue_list = body_label("")
        layout.addWidget(self._queue_list)

        self._hold_heading = heading_label("On hold")
        self._hold_heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self._hold_heading)
        self._hold_empty = body_label("• (none)")
        layout.addWidget(self._hold_empty)
        self._hold_rows = QVBoxLayout()
        layout.addLayout(self._hold_rows)

        layout.addStretch()

        hint = body_label(f"Your files are saved under {DEFAULT_WORK_ROOT}")
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

        close_btn = QPushButton("Close Program")
        close_btn.clicked.connect(self._close_program)
        layout.addWidget(close_btn)

    def _refresh_clean_button(self) -> None:
        try:
            free = free_bytes_at(DEFAULT_SCAN_ROOT)
        except OSError:
            free = None
        self._clean_btn.setText(clean_working_files_button_text(free))

    def _open_clean(self) -> None:
        ctx = self.context()
        if ctx is not None:
            ctx.clean_return_screen = "A1"
        self.navigate.emit("A4")

    def _close_program(self) -> None:
        if self.controller.lock.is_recording_active() or self.controller._recording_job_id:
            QMessageBox.warning(
                self,
                "Recording in progress",
                "Stop recording first before closing the program.",
            )
            return
        if self.controller.should_confirm_close_program():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Close Program")
            box.setText(
                "This will abort any autocut that is currently running, and pause "
                "the autocut queue. Are you sure you wish to quit?"
            )
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            if box.exec() != QMessageBox.Yes:
                return
        self._quit_program()

    def _quit_program(self) -> None:
        window = self.window()
        if hasattr(window, "quit_program"):
            window.quit_program()
            return
        self.controller.interrupt_running_for_quit()
        self.controller.release_app_lock()
        from PySide6.QtWidgets import QApplication

        QApplication.instance().quit()

    def on_enter(self) -> None:
        self._refresh_queue()
        reasons = self.controller.busy_reasons()
        if reasons:
            self._banner.setText(
                f"Note: {', '.join(reasons)} is active in the background."
            )
            self._banner.show()
        else:
            self._banner.setText("")
            self._banner.hide()

    def _refresh_queue(self) -> None:
        self._refresh_clean_button()
        current, waiting = self.controller.full_queue_display()
        lines: list[str] = []
        if current:
            name = current.get("name") or current.get("folder") or "Unknown"
            lines.append(f"Currently being rendered:\n• {name}")
        else:
            lines.append("Currently being rendered:\n• (none)")
        if waiting:
            lines.append("")
            lines.append("Waiting:")
            for row in waiting:
                name = row.get("name") or row.get("folder") or "Unknown"
                lines.append(f"• {name}")
        else:
            lines.append("")
            lines.append("Waiting:\n• (none)")
        self._queue_list.setText("\n".join(lines))
        self._refresh_hold_list()
        self._status_line.set_status(self.controller.autocut_status_line())

    def _clear_hold_rows(self) -> None:
        while self._hold_rows.count():
            item = self._hold_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_hold_list(self) -> None:
        self._clear_hold_rows()
        held = self.controller.list_held_jobs()
        if not held:
            self._hold_empty.show()
            return
        self._hold_empty.hide()
        for row in held:
            folder = Path(str(row.get("folder") or ""))
            lane = str(row.get("lane") or "full")
            name = str(row.get("name") or folder.name or "Unknown")
            kind = "Final Render" if lane == "full" else "Fast Preview"
            bar = QWidget()
            bar_layout = QHBoxLayout(bar)
            bar_layout.setContentsMargins(0, 0, 0, 0)
            bar_layout.addWidget(body_label(f"• {name}  ({kind})"), stretch=1)
            resume = QPushButton("Resume")
            resume.clicked.connect(
                lambda _checked=False, f=folder, ln=lane: self._resume_held(f, ln)
            )
            bar_layout.addWidget(resume)
            self._hold_rows.addWidget(bar)

    def _resume_held(self, folder: Path, lane: str) -> None:
        if not folder:
            return
        self.controller.resume_held_job(folder, lane)  # type: ignore[arg-type]
        screen = "F4" if lane == "full" else "E1"
        self.navigate_session.emit(screen, folder)
