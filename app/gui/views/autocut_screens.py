"""Autocut session setup screens C1–C4."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.controller.paths import DEFAULT_SCAN_ROOT, DEFAULT_WORK_ROOT
from app.controller.session_store import (
    PIAB_STATE_FILENAME,
    existing_state_conflict,
    is_resumable_piab_session,
)
from app.gui.dialogs import choose_existing_session_action, confirm_action
from app.gui.session_context import SessionContext
from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import (
    body_label,
    heading_label,
    selectable_plain_text,
    set_plain_lines,
)
from app.gui.widgets.worker import CallableWorker

VISIBLE_LIST_ROWS = 4


def _format_scan_summary(data: dict) -> str:
    lines = [
        f"Scan folder: {data.get('scan_root', '')}",
        f"Selected session: {data.get('video_count', 0)} videos, "
        f"{data.get('audio_count', 0)} audio",
        f"Duration: {data.get('typical_duration_human', '?')}",
        f"Recorded: {data.get('typical_mtime_iso', '?')}",
    ]
    reqs = data.get("requirements") or {}
    for line in reqs.get("missing") or []:
        lines.append(f"Missing: {line}")
    return "\n".join(lines)


def _scanning_dialog(parent: QWidget) -> QProgressDialog:
    dialog = QProgressDialog("Scanning…", None, 0, 0, parent)
    dialog.setWindowTitle("Please wait")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setCancelButton(None)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.show()
    QApplication.processEvents()
    return dialog


_DEFAULT_SESSION_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}(?:_\d+)?$")


def _looks_like_default_session_name(name: str) -> bool:
    return bool(_DEFAULT_SESSION_NAME_RE.match(name))


def _cluster_label(option: dict) -> str:
    when = option.get("typical_mtime_iso", "?").replace("T", " ")
    duration = option.get("typical_duration_human", "?")
    videos = option.get("video_count", 0)
    audios = option.get("audio_count", 0)
    return f"{when} — {videos} videos, {audios} audio, {duration}"


class DeliveryScreen(ScreenWidget):
    """C1 — optional email delivery."""

    screen_id = "C1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(
            body_label(
                "When your edited interview is ready, would you like us to email it to you?"
            )
        )

        self._group = QButtonGroup(self)
        self._no_email = QRadioButton("No — I'll copy files to a memory stick")
        self._yes_email = QRadioButton("Yes — email the finished video")
        self._no_email.setChecked(True)
        self._group.addButton(self._no_email)
        self._group.addButton(self._yes_email)
        layout.addWidget(self._no_email)
        layout.addWidget(self._yes_email)

        email_box = QWidget()
        email_layout = QVBoxLayout(email_box)
        email_layout.setContentsMargins(24, 0, 0, 0)
        email_layout.addWidget(body_label("Email address", word_wrap=False))
        self._email = QLineEdit()
        self._email.setPlaceholderText("you@example.com")
        email_layout.addWidget(self._email)
        layout.addWidget(email_box)
        self._email_box = email_box

        self._yes_email.toggled.connect(self._sync_email_fields)
        self._sync_email_fields()

        layout.addStretch()

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self._go_back)
        row.addWidget(back)
        row.addStretch()
        cont = QPushButton("Continue")
        cont.setDefault(True)
        cont.setMinimumHeight(40)
        cont.clicked.connect(self._continue)
        row.addWidget(cont)
        layout.addLayout(row)

    def _sync_email_fields(self) -> None:
        enabled = self._yes_email.isChecked()
        self._email_box.setEnabled(enabled)
        self._email.setEnabled(enabled)

    def _go_back(self) -> None:
        ctx = self.context()
        if ctx is not None and ctx.entry_path == "record":
            self.navigate.emit("A3")
        elif ctx is not None and ctx.entry_path == "already_recorded":
            self.navigate.emit("A3")
        else:
            self.navigate.emit("A1")

    def _continue(self) -> None:
        ctx = self.context()
        if ctx is None:
            return

        if self._yes_email.isChecked():
            email = self._email.text().strip()
            if not email:
                QMessageBox.warning(self, "Email required", "Enter an email address.")
                return
            ok, message = self.controller.validate_delivery_email(email)
            if not ok:
                QMessageBox.warning(self, "Invalid email", message)
                return
            ctx.delivery_enabled = True
            ctx.delivery_email = message
        else:
            ctx.delivery_enabled = False
            ctx.delivery_email = None

        if ctx.entry_path == "record":
            self.navigate.emit("B1")
        elif ctx.entry_path == "already_recorded":
            self.navigate.emit("C2")
        else:
            self.navigate.emit("A1")

    def on_enter(self) -> None:
        ctx = self.context()
        if ctx is None:
            return
        if ctx.delivery_enabled and ctx.delivery_email:
            self._yes_email.setChecked(True)
            self._email.setText(ctx.delivery_email)
        else:
            self._no_email.setChecked(True)
        self._sync_email_fields()


class SourceLocationScreen(ScreenWidget):
    """C2 — pick default scan root or a special folder."""

    screen_id = "C2"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(
            body_label(
                "Where are your MultiCorder recording files?\n"
                f"Default looks for the newest cluster in {DEFAULT_SCAN_ROOT}."
            )
        )

        self._group = QButtonGroup(self)
        self._default = QRadioButton(f"Default — newest files in {DEFAULT_SCAN_ROOT}")
        self._special = QRadioButton("Special folder — files are already in one folder")
        self._default.setChecked(True)
        self._group.addButton(self._default)
        self._group.addButton(self._special)
        layout.addWidget(self._default)
        layout.addWidget(self._special)

        special_row = QHBoxLayout()
        special_row.setContentsMargins(28, 0, 0, 0)
        self._browse = QPushButton("Browse…")
        self._browse.clicked.connect(self._browse_folder)
        self._folder_label = body_label("(none selected)")
        special_row.addWidget(self._browse)
        special_row.addWidget(self._folder_label, stretch=1)
        layout.addLayout(special_row)

        self._special.toggled.connect(self._sync_browse_enabled)
        self._sync_browse_enabled()

        layout.addStretch()

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("C1"))
        row.addWidget(back)
        row.addStretch()
        cont = QPushButton("Continue")
        cont.setDefault(True)
        cont.setMinimumHeight(40)
        cont.clicked.connect(self._continue)
        row.addWidget(cont)
        layout.addLayout(row)

        self._special_folder: Path | None = None
        self._continue_btn = cont
        self._scan_worker: CallableWorker | None = None
        self._scan_dialog: QProgressDialog | None = None

    def _sync_browse_enabled(self) -> None:
        self._browse.setEnabled(self._special.isChecked())

    def _select_special_folder(self, folder: Path, *, force_new: bool = False) -> None:
        self._special.setChecked(True)
        self._special_folder = folder
        self._folder_label.setText(str(folder))
        ctx = self.context()
        if ctx is not None:
            ctx.source_mode = "special"
            ctx.special_folder = folder
            ctx.scan_data = None
            ctx.allow_overwrite = force_new

    def _folder_has_existing_state(self, folder: Path) -> bool:
        return (
            (folder / PIAB_STATE_FILENAME).is_file()
            or (folder / "cursor-podcast-in-a-box.json").is_file()
        )

    def _prompt_existing_session(self, folder: Path) -> None:
        resumable = is_resumable_piab_session(folder)
        conflict = existing_state_conflict(folder)
        if resumable:
            text = "This folder already has a PIAB session."
            detail = (
                "Resume continues where you left off. "
                "Force new session replaces podcast-in-a-box.json and starts "
                "labeling again (existing Raw/Output files may remain on disk)."
            )
        else:
            text = conflict or "This folder already has session state on disk."
            detail = (
                "Resume is not available. Choose a different folder, or force a "
                "new PIAB session here (overwrites podcast-in-a-box.json)."
            )
        action = choose_existing_session_action(
            self,
            title="Existing session",
            text=text,
            detail=detail,
            allow_resume=resumable,
        )
        if action == "resume":
            try:
                screen = self.controller.resume_screen_for(folder)
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot resume session", str(exc))
                return
            self.navigate_session.emit(screen, folder)
            return
        if action == "force_new":
            self._select_special_folder(folder, force_new=True)
            ctx = self.context()
            if ctx is not None:
                self._scan_and_go(ctx, folder)
            return
        # choose_different: stay on this screen; folder is not selected.

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select folder with MultiCorder files",
            str(self.controller.scan_root),
        )
        if not chosen:
            return
        folder = Path(chosen)
        if self._folder_has_existing_state(folder):
            self._prompt_existing_session(folder)
            return
        self._select_special_folder(folder, force_new=False)

    def _continue(self) -> None:
        ctx = self.context()
        if ctx is None:
            return

        if self._special.isChecked():
            if self._special_folder is None:
                QMessageBox.warning(self, "Choose a folder", "Browse to your recording folder.")
                return
            folder = self._special_folder
            if self._folder_has_existing_state(folder) and not ctx.allow_overwrite:
                self._prompt_existing_session(folder)
                return
            ctx.source_mode = "special"
            ctx.special_folder = folder
            scan_dir = folder
        else:
            ctx.source_mode = "default"
            ctx.special_folder = None
            scan_dir = self.controller.scan_root

        self._scan_and_go(ctx, scan_dir)

    def _scan_and_go(self, ctx: SessionContext, scan_dir: Path) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return

        self._continue_btn.setEnabled(False)
        self._scan_dialog = _scanning_dialog(self)

        self._scan_worker = CallableWorker(self.controller.scan_session, scan_dir)
        self._scan_worker.finished_ok.connect(
            lambda data: self._on_scan_ok(ctx, data)
        )
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.start()

    def _close_scan_dialog(self) -> None:
        if self._scan_dialog is not None:
            self._scan_dialog.close()
            self._scan_dialog = None

    def _on_scan_ok(self, ctx: SessionContext, data: object) -> None:
        self._close_scan_dialog()
        self._continue_btn.setEnabled(True)
        scan = dict(data) if isinstance(data, dict) else {}
        ctx.scan_data = scan
        reqs = scan.get("requirements") or {}
        if ctx.source_mode == "special" and not reqs.get("ok"):
            QMessageBox.warning(
                self,
                "Missing files",
                _format_scan_summary(scan),
            )
            return
        self.navigate.emit("C2a")

    def _on_scan_failed(self, message: str) -> None:
        self._close_scan_dialog()
        self._continue_btn.setEnabled(True)
        QMessageBox.warning(self, "Scan failed", message)

    def on_enter(self) -> None:
        ctx = self.context()
        if ctx is None:
            return
        if ctx.source_mode == "special" and ctx.special_folder:
            self._special.setChecked(True)
            self._special_folder = ctx.special_folder
            self._folder_label.setText(str(ctx.special_folder))
        else:
            self._default.setChecked(True)
            self._special_folder = None
            self._folder_label.setText("(none selected)")
        self._sync_browse_enabled()


class ConfirmSourceScreen(ScreenWidget):
    """C2a — review scanned MultiCorder files."""

    screen_id = "C2a"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._loading_cluster = False
        self._cluster_scan_worker: CallableWorker | None = None
        self._cluster_scan_dialog: QProgressDialog | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._summary = body_label("")
        layout.addWidget(self._summary)

        self._cluster_row = QWidget()
        cluster_layout = QHBoxLayout(self._cluster_row)
        cluster_layout.setContentsMargins(0, 0, 0, 0)
        cluster_layout.addWidget(body_label("Recording session:", word_wrap=False))
        self._cluster_picker = QComboBox()
        self._cluster_picker.setEditable(True)
        self._cluster_picker.lineEdit().setReadOnly(True)
        self._cluster_picker.currentIndexChanged.connect(self._cluster_changed)
        cluster_layout.addWidget(self._cluster_picker, stretch=1)
        layout.addWidget(self._cluster_row)

        layout.addWidget(body_label("Files in this session", word_wrap=False))
        self._selected_list = selectable_plain_text(visible_rows=VISIBLE_LIST_ROWS)
        layout.addWidget(self._selected_list)

        layout.addStretch()

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("C2"))
        row.addWidget(back)
        row.addStretch()
        cont = QPushButton("Continue")
        cont.setDefault(True)
        cont.setMinimumHeight(40)
        cont.clicked.connect(self._continue)
        row.addWidget(cont)
        layout.addLayout(row)

    def _continue(self) -> None:
        ctx = self.context()
        if ctx is None:
            return
        if ctx.source_mode == "special":
            self.navigate.emit("C3")
        else:
            self.navigate.emit("C2b")

    def _cluster_changed(self, index: int) -> None:
        if self._loading_cluster or index < 0:
            return
        ctx = self.context()
        if ctx is None or ctx.scan_data is None:
            return
        if index == ctx.scan_data.get("cluster_index", 0):
            return
        scan_dir = Path(str(ctx.scan_data.get("scan_root", self.controller.scan_root)))
        if self._cluster_scan_worker is not None and self._cluster_scan_worker.isRunning():
            return

        self._cluster_scan_dialog = _scanning_dialog(self)
        self._cluster_picker.setEnabled(False)

        self._cluster_scan_worker = CallableWorker(
            self.controller.scan_session,
            scan_dir,
            cluster_index=index,
        )
        self._cluster_scan_worker.finished_ok.connect(
            lambda data: self._on_cluster_scan_ok(ctx, data, index)
        )
        self._cluster_scan_worker.failed.connect(self._on_cluster_scan_failed)
        self._cluster_scan_worker.start()

    def _on_cluster_scan_ok(
        self,
        ctx: SessionContext,
        data: object,
        index: int,
    ) -> None:
        if self._cluster_scan_dialog is not None:
            self._cluster_scan_dialog.close()
            self._cluster_scan_dialog = None
        self._cluster_picker.setEnabled(True)
        ctx.scan_data = dict(data) if isinstance(data, dict) else {}
        self._loading_cluster = True
        self._cluster_picker.setCurrentIndex(index)
        self._loading_cluster = False
        self._render_scan(ctx.scan_data)

    def _on_cluster_scan_failed(self, message: str) -> None:
        if self._cluster_scan_dialog is not None:
            self._cluster_scan_dialog.close()
            self._cluster_scan_dialog = None
        self._cluster_picker.setEnabled(True)
        ctx = self.context()
        if ctx is not None and ctx.scan_data is not None:
            self._reload_cluster_picker(ctx.scan_data)
        QMessageBox.warning(self, "Could not switch session", message)

    def _reload_cluster_picker(self, data: dict) -> None:
        self._loading_cluster = True
        self._cluster_picker.clear()
        options = data.get("clusters") or []
        if not options:
            self._cluster_row.hide()
        else:
            self._cluster_row.show()
            for option in options:
                self._cluster_picker.addItem(_cluster_label(option))
            self._cluster_picker.setCurrentIndex(int(data.get("cluster_index", 0)))
        self._loading_cluster = False

    def _render_scan(self, data: dict) -> None:
        self._summary.setText(_format_scan_summary(data))
        self._reload_cluster_picker(data)

        selected_lines = [
            f"{item.get('kind', '?'):5}  {item.get('duration_human', '?'):>8}  "
            f"{item.get('name', '?')}"
            for item in data.get("files") or []
        ]
        set_plain_lines(self._selected_list, selected_lines)

    def on_enter(self) -> None:
        ctx = self.context()
        if ctx is None or ctx.scan_data is None:
            self._summary.setText("No scan data. Go back and choose a source location.")
            self._cluster_row.hide()
            set_plain_lines(self._selected_list, [])
            return

        self._render_scan(ctx.scan_data)


class SessionNameScreen(ScreenWidget):
    """C2b — choose session folder name (default source mode only)."""

    screen_id = "C2b"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._suggested_name = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("Name session folder"))
        layout.addWidget(
            body_label(
                f"A new folder will be created under:\n{DEFAULT_WORK_ROOT}"
            )
        )

        self._default_radio = QRadioButton("")
        self._custom_radio = QRadioButton("Enter a custom name:")
        self._custom_name = QLineEdit()
        self._custom_name.setPlaceholderText("e.g. Bayeswatch")
        self._custom_name.setEnabled(False)

        group = QButtonGroup(self)
        group.addButton(self._default_radio)
        group.addButton(self._custom_radio)
        self._default_radio.setChecked(True)
        self._custom_radio.toggled.connect(
            lambda checked: self._custom_name.setEnabled(checked)
        )

        layout.addWidget(self._default_radio)
        layout.addWidget(self._custom_radio)
        layout.addWidget(self._custom_name)
        layout.addStretch()

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self._go_back)
        row.addWidget(back)
        row.addStretch()
        cont = QPushButton("Continue")
        cont.setDefault(True)
        cont.setMinimumHeight(40)
        cont.clicked.connect(self._continue)
        row.addWidget(cont)
        layout.addLayout(row)

    def _go_back(self) -> None:
        ctx = self.context()
        if ctx is not None and ctx.entry_path == "record":
            self.navigate.emit("B5")
        else:
            self.navigate.emit("C2a")

    def _continue(self) -> None:
        ctx = self.context()
        if ctx is None:
            return

        if self._default_radio.isChecked():
            if not (
                ctx.session_name
                and _looks_like_default_session_name(ctx.session_name)
            ):
                ctx.session_name = self._suggested_name
        else:
            name = self._custom_name.text().strip()
            if not name:
                QMessageBox.warning(self, "Name required", "Enter a folder name.")
                return
            if "/" in name or "\\" in name:
                QMessageBox.warning(
                    self,
                    "Invalid name",
                    "Use a single folder name without path separators.",
                )
                return
            target = self.controller.work_root / name
            if target.is_dir() and (target / PIAB_STATE_FILENAME).is_file():
                if confirm_action(
                    self,
                    title="Existing session",
                    text=f"A session folder named {name!r} already exists.",
                    detail="Open it to resume instead of creating a new session?",
                ):
                    self.navigate_session.emit(
                        self.controller.resume_screen_for(target),
                        target,
                    )
                return
            ctx.session_name = name

        self.navigate.emit("C3")

    def on_enter(self) -> None:
        ctx = self.context()
        if ctx is not None and ctx.session_name:
            name = ctx.session_name
            if _looks_like_default_session_name(name):
                self._suggested_name = name
                self._default_radio.setText(f"Use default date and time ({name})")
                self._default_radio.setChecked(True)
                self._custom_name.clear()
            else:
                self._suggested_name = self.controller.generate_session_name()
                self._default_radio.setText(
                    f"Use default date and time ({self._suggested_name})"
                )
                self._custom_radio.setChecked(True)
                self._custom_name.setText(name)
        else:
            self._suggested_name = self.controller.generate_session_name()
            self._default_radio.setText(
                f"Use default date and time ({self._suggested_name})"
            )
            self._default_radio.setChecked(True)
            self._custom_name.clear()

        self._custom_name.setEnabled(self._custom_radio.isChecked())


class CreateSessionScreen(ScreenWidget):
    """C3 — create working folder and podcast-in-a-box.json."""

    screen_id = "C3"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._headline = heading_label("Creating session…")
        layout.addWidget(self._headline)

        self._detail = body_label("")
        layout.addWidget(self._detail)

        layout.addStretch()

        row = QHBoxLayout()
        self._back = QPushButton("Back")
        self._back.clicked.connect(self._go_back)
        row.addWidget(self._back)
        row.addStretch()
        self._retry = QPushButton("Retry")
        self._retry.clicked.connect(self.on_enter)
        self._retry.hide()
        row.addWidget(self._retry)
        layout.addLayout(row)

    def _go_back(self) -> None:
        ctx = self.context()
        if ctx is not None and ctx.source_mode == "special":
            self.navigate.emit("C2a")
        else:
            self.navigate.emit("C2b")

    def on_enter(self) -> None:
        ctx = self.context()
        if ctx is None:
            return

        self._headline.setText("Creating session…")
        self._detail.setText("Scanning files and writing session folder…")
        self._retry.hide()
        self._back.setEnabled(False)

        if self._worker is not None and self._worker.isRunning():
            return

        self._worker = CallableWorker(self._init_session, ctx)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _planned_folder(self, ctx: SessionContext) -> Path:
        if ctx.source_mode == "special" and ctx.special_folder:
            return ctx.special_folder.resolve()
        name = ctx.session_name or self.controller.generate_session_name()
        ctx.session_name = name
        return (self.controller.work_root / name).resolve()

    def _init_session(self, ctx: SessionContext) -> Path:
        folder = self._planned_folder(ctx)
        at_risk = self.controller.check_overwrite_risk("init_session", folder)
        if at_risk and not ctx.allow_overwrite:
            raise RuntimeError(
                "Existing session files would be overwritten:\n"
                + "\n".join(str(path) for path in at_risk[:5])
                + ("\n…" if len(at_risk) > 5 else "")
            )

        kwargs: dict = {
            "allow_overwrite": ctx.allow_overwrite,
        }
        if ctx.scan_data is not None:
            kwargs["from_scan_data"] = ctx.scan_data
        if ctx.delivery_enabled and ctx.delivery_email:
            kwargs["delivery_email"] = ctx.delivery_email
            kwargs["confirm_delivery_email"] = True

        if ctx.source_mode == "special" and ctx.special_folder:
            return self.controller.init_session(
                mode="special",
                working_folder=ctx.special_folder,
                **kwargs,
            )

        name = ctx.session_name or self.controller.generate_session_name()
        ctx.session_name = name
        return self.controller.init_session(mode="default", name=name, **kwargs)

    def _on_ok(self, folder: object) -> None:
        ctx = self.context()
        if ctx is not None:
            ctx.session_folder = Path(str(folder))
        self._back.setEnabled(True)
        self.navigate_session.emit("C4", folder)

    def _on_fail(self, message: str) -> None:
        self._headline.setText("Could not create session")
        self._detail.setText(message)
        self._retry.show()
        self._back.setEnabled(True)

        if "overwritten" in message.lower():
            ctx = self.context()
            if ctx is not None and confirm_action(
                self,
                title="Overwrite existing files?",
                text="This session folder already has PIAB state or outputs.",
                detail="Continue and replace them?",
            ):
                ctx.allow_overwrite = True
                self.on_enter()


class SessionReadyScreen(ScreenWidget):
    """C4 — show session path before labeling."""

    screen_id = "C4"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("Session ready"))

        layout.addWidget(
            body_label(
                "Your autocut session is set up. Next you'll label camera and microphone files."
            )
        )

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        layout.addStretch()

        cont = QPushButton("Continue to labeling")
        cont.setMinimumHeight(44)
        cont.setDefault(True)
        cont.clicked.connect(lambda: self.navigate.emit("D1"))
        layout.addWidget(cont)

    def on_enter(self) -> None:
        ctx = self.context()
        folder = ctx.session_folder if ctx else None
        self._banner.set_path(folder)
