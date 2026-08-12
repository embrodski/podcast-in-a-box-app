"""Screen F5 — episode complete."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import (
    body_label,
    heading_label,
    selectable_plain_text,
    set_plain_lines,
)
from app.gui.widgets.worker import CallableWorker


class DoneScreen(ScreenWidget):
    screen_id = "F5"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("All done"))
        self._summary = body_label("")
        layout.addWidget(self._summary)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        layout.addWidget(heading_label("Flags in the final edit"))
        self._flag_report = selectable_plain_text(visible_rows=8)
        layout.addWidget(self._flag_report)

        self._extra_box = QWidget()
        extra_layout = QVBoxLayout(self._extra_box)
        extra_layout.setContentsMargins(0, 8, 0, 0)
        extra_layout.addWidget(
            body_label(
                "Send a copy of the link to the final video file to another email address:"
            )
        )
        row = QHBoxLayout()
        self._extra_email = QLineEdit()
        self._extra_email.setPlaceholderText("another@example.com")
        row.addWidget(self._extra_email, stretch=1)
        self._send_extra = QPushButton("Send link")
        self._send_extra.clicked.connect(self._send_extra_email)
        row.addWidget(self._send_extra)
        extra_layout.addLayout(row)
        self._extra_status = body_label("")
        extra_layout.addWidget(self._extra_status)
        layout.addWidget(self._extra_box)

        layout.addStretch()

        done = QPushButton("Back to home")
        done.setMinimumHeight(44)
        done.clicked.connect(lambda: self.navigate.emit("A1"))
        layout.addWidget(done)

        self._session_folder: Path | None = None
        self._delivery_email: str | None = None
        self._can_send_link = False

    def on_enter(self) -> None:
        ctx = self.context()
        folder = ctx.session_folder if ctx and ctx.session_folder else None
        self._session_folder = folder
        self._extra_status.setText("")
        self._extra_email.clear()
        self._can_send_link = False
        self._delivery_email = None
        self._extra_box.hide()

        if folder is None:
            self._summary.setText("Session folder is not available.")
            self._banner.set_path(None)
            set_plain_lines(self._flag_report, [])
            return

        self._banner.set_path(folder)

        try:
            state = self.controller.load_session_state(folder)
        except Exception as exc:
            self._summary.setText(f"Could not read session state:\n{exc}")
            set_plain_lines(self._flag_report, [])
            return

        try:
            report_text = self.controller.load_flag_report_text(folder)
        except Exception:
            report_text = ""
        report_lines = report_text.splitlines() if report_text else []
        set_plain_lines(self._flag_report, report_lines)
        row_count = max(len(report_lines), 5)
        row_height = self._flag_report.fontMetrics().lineSpacing()
        self._flag_report.setFixedHeight(
            row_height * min(row_count, 14) + 2 * self._flag_report.frameWidth() + 8
        )

        delivery = state.get("delivery") or {}
        output_dir = Path(str((state.get("paths") or {}).get("output") or folder / "Output"))
        video_path = output_dir / "Full Interview.mp4"
        episode_name = str(state.get("name") or folder.name)

        if delivery.get("enabled") and delivery.get("email"):
            self._delivery_email = str(delivery["email"])
            frameio = delivery.get("frameio") or {}
            email_status = (delivery.get("email_delivery") or {}).get("status", "")
            if frameio.get("status") == "completed" and frameio.get("short_url"):
                self._can_send_link = True
                lines = [
                    f"Your edited interview “{episode_name}” is ready.",
                    f"We emailed the video link to {self._delivery_email}.",
                ]
                if email_status == "sent":
                    lines.append("Check your inbox for the Frame.io link.")
                else:
                    lines.append(
                        f"Primary delivery status: {email_status or 'unknown'}."
                    )
                lines.append(f"\nSession files:\n{folder}")
                self._summary.setText("\n".join(lines))
                self._extra_box.show()
            else:
                lines = [
                    f"Your edited interview “{episode_name}” is ready on this machine.",
                    f"We could not confirm email delivery to {self._delivery_email}.",
                ]
                if video_path.is_file():
                    lines.append(f"\nLocal video:\n{video_path}")
                lines.append(f"\nSession folder:\n{folder}")
                self._summary.setText("\n".join(lines))
        else:
            lines = [
                f"Your edited interview “{episode_name}” is ready.",
                "Copy the video to a memory stick from the path below.",
            ]
            if video_path.is_file():
                lines.append(f"\nVideo file:\n{video_path}")
            lines.append(f"\nSession folder:\n{folder}")
            self._summary.setText("\n".join(lines))

    def _send_extra_email(self) -> None:
        if not self._can_send_link or self._session_folder is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return

        raw = self._extra_email.text().strip()
        if not raw:
            QMessageBox.warning(self, "Email required", "Enter an email address.")
            return

        ok, normalized = self.controller.validate_delivery_email(raw)
        if not ok:
            QMessageBox.warning(self, "Invalid email", normalized)
            return

        if self._delivery_email and normalized == self._delivery_email:
            QMessageBox.information(
                self,
                "Already sent",
                f"The link was already emailed to {normalized}.",
            )
            return

        self._send_extra.setEnabled(False)
        self._extra_status.setText(f"Sending link to {normalized}…")

        folder = self._session_folder
        self._worker = CallableWorker(
            self.controller.send_delivery_link_to_email,
            folder,
            normalized,
        )
        self._worker.finished_ok.connect(self._on_extra_sent)
        self._worker.failed.connect(self._on_extra_failed)
        self._worker.start()

    def _on_extra_sent(self, recipient: object) -> None:
        self._send_extra.setEnabled(True)
        self._extra_status.setText(f"Link sent to {recipient}.")
        self._extra_email.clear()

    def _on_extra_failed(self, message: str) -> None:
        self._send_extra.setEnabled(True)
        self._extra_status.setText("")
        QMessageBox.warning(self, "Could not send link", message)
