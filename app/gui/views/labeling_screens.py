"""Labeling flow screens D1–D4."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gui.dialogs import confirm_action
from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.video_playback import close_media_player
from app.gui.widgets.selectable_text import body_label, heading_label, selectable_plain_text
from app.gui.widgets.worker import CallableWorker


def _session_folder(screen: ScreenWidget) -> Path | None:
    ctx = screen.context()
    if ctx is None or ctx.session_folder is None:
        return None
    return ctx.session_folder


def _audio_previews_need_refresh(previews: list) -> bool:
    """True when cached A/B clips for a mic start too close together."""
    from piab_lib import MIN_PREVIEW_CLIP_SEPARATION_SEC

    by_source: dict[str, list[dict]] = defaultdict(list)
    for item in previews:
        by_source[str(item.get("source", ""))].append(item)

    min_sep = MIN_PREVIEW_CLIP_SEPARATION_SEC
    for clips in by_source.values():
        if len(clips) < 2:
            continue
        starts = sorted(float(c.get("start_sec") or 0) for c in clips)
        for prev, nxt in zip(starts, starts[1:]):
            if nxt - prev < min_sep - 0.05:
                return True
    return False


def _selected_role(group: QButtonGroup) -> str | None:
    btn = group.checkedButton()
    if btn is None:
        return None
    return str(btn.property("role"))


class LabelCamerasScreen(ScreenWidget):
    """D1 — assign Host / Guest / Wide to each camera preview."""

    screen_id = "D1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._previews: list[dict] = []
        self._role_groups: dict[str, QButtonGroup] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(heading_label("Label cameras"))
        self._intro = body_label(
            "For each camera preview, choose Host, Guest, Wide, or Do not use. "
            "You need exactly one of each role (Host, Guest, Wide)."
        )
        layout.addWidget(self._intro)

        self._status = body_label("")
        layout.addWidget(self._status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._cards_host = QWidget()
        self._cards = QHBoxLayout(self._cards_host)
        self._cards.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._cards_host)
        layout.addWidget(scroll, stretch=1)

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("C4"))
        row.addWidget(back)
        row.addStretch()
        self._continue = QPushButton("Continue")
        self._continue.setDefault(True)
        self._continue.clicked.connect(self._go_next)
        row.addWidget(self._continue)
        layout.addLayout(row)

    def on_enter(self) -> None:
        folder = _session_folder(self)
        if folder is None:
            self._status.setText("No session folder. Go back and create a session first.")
            return

        self._status.setText("Extracting camera preview frames…")
        self._clear_cards()
        self._continue.setEnabled(False)

        if self._worker is not None and self._worker.isRunning():
            return

        self._worker = CallableWorker(self._load_previews, folder)
        self._worker.finished_ok.connect(self._on_previews)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _load_previews(self, folder: Path) -> dict:
        state = self.controller.load_session_state(folder)
        existing = state.get("video_previews")
        if existing:
            return {
                "video_previews": existing,
                "preview_folder": str((state.get("paths") or {}).get("previews", "")),
            }
        return self.controller.extract_video_previews(folder)

    def _on_fail(self, message: str) -> None:
        self._status.setText(message)
        self._continue.setEnabled(True)

    def _on_previews(self, payload: object) -> None:
        data = dict(payload) if isinstance(payload, dict) else {}
        self._previews = list(data.get("video_previews") or [])
        self._status.setText(
            f"Previewing {len(self._previews)} camera(s). "
            "Choose a role for each camera below."
        )
        self._build_cards()
        self._continue.setEnabled(True)

    def _clear_cards(self) -> None:
        self._role_groups.clear()
        while self._cards.count():
            item = self._cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _build_cards(self) -> None:
        self._clear_cards()
        ctx = self.context()
        saved = (ctx.video_labels if ctx else None) or {}

        for preview in self._previews:
            source = str(preview.get("source", ""))
            card = QVBoxLayout()
            wrap = QWidget()
            wrap.setLayout(card)

            title = body_label(str(preview.get("camera", "Camera")), word_wrap=False)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.addWidget(title)

            img = QLabel()
            img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            path = Path(str(preview.get("preview", "")))
            if path.is_file():
                pix = QPixmap(str(path))
                img.setPixmap(
                    pix.scaled(240, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            else:
                img.setText("(preview missing)")
            card.addWidget(img)

            name = body_label(str(preview.get("source_name", "")), word_wrap=True)
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.addWidget(name)

            group = QButtonGroup(wrap)
            self._role_groups[source] = group
            for label, role in (
                ("Host", "host"),
                ("Guest", "guest"),
                ("Wide", "wide"),
                ("Do not use", "do_not_use"),
            ):
                radio = QRadioButton(label)
                radio.setProperty("role", role)
                group.addButton(radio)
                card.addWidget(radio)
                if saved.get(source) == role:
                    radio.setChecked(True)

            self._cards.addWidget(wrap)

    def _collect_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for source, group in self._role_groups.items():
            role = _selected_role(group)
            if role:
                labels[source] = role
        return labels

    def _go_next(self) -> None:
        labels = self._collect_labels()
        if len(labels) != len(self._role_groups):
            QMessageBox.warning(
                self,
                "Incomplete labels",
                "Choose a role for every camera.",
            )
            return
        try:
            self.controller.validate_video_labels(labels)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid camera labels", str(exc))
            return

        ctx = self.context()
        if ctx is not None:
            ctx.video_labels = labels
        self.navigate.emit("D2")


class LabelMicrophonesScreen(ScreenWidget):
    """D2 — assign Host / Guest to each microphone."""

    screen_id = "D2"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._mic_groups: dict[str, QButtonGroup] = {}
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(heading_label("Label microphones"))
        layout.addWidget(
            body_label(
                "Listen to each mic preview and choose Host, Guest, or Do not use. "
                "You need exactly one Host and one Guest."
            )
        )
        self._status = body_label("")
        layout.addWidget(self._status)

        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        layout.addWidget(self._list_host, stretch=1)

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("D1"))
        row.addWidget(back)
        row.addStretch()
        cont = QPushButton("Continue")
        cont.setDefault(True)
        cont.clicked.connect(self._go_next)
        row.addWidget(cont)
        layout.addLayout(row)

    def on_enter(self) -> None:
        folder = _session_folder(self)
        if folder is None:
            self._status.setText("No session folder.")
            return

        self._status.setText("Extracting audio preview clips…")
        self._clear_rows()

        if self._worker is not None and self._worker.isRunning():
            return

        self._worker = CallableWorker(self._load_previews, folder)
        self._worker.finished_ok.connect(self._on_previews)
        self._worker.failed.connect(lambda msg: self._status.setText(msg))
        self._worker.start()

    def on_leave(self) -> None:
        close_media_player(self._player)

    def _load_previews(self, folder: Path) -> dict:
        state = self.controller.load_session_state(folder)
        existing = state.get("audio_previews")
        if existing and not _audio_previews_need_refresh(existing):
            return {
                "audio_previews": existing,
                "skipped_silent": state.get("audio_previews_skipped_silent") or [],
            }
        return self.controller.extract_audio_previews(folder)

    def _clear_rows(self) -> None:
        self._mic_groups.clear()
        while self._list.count():
            item = self._list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_previews(self, payload: object) -> None:
        data = dict(payload) if isinstance(payload, dict) else {}
        previews = list(data.get("audio_previews") or [])
        skipped = list(data.get("skipped_silent") or [])
        by_source: dict[str, list[dict]] = defaultdict(list)
        for item in previews:
            by_source[str(item.get("source", ""))].append(item)

        lines = [f"Label {len(by_source)} microphone(s)."]
        if skipped:
            lines.append(f"Skipped {len(skipped)} silent track(s).")
        self._status.setText(" ".join(lines))
        self._build_rows(by_source)

    def _build_rows(self, by_source: dict[str, list[dict]]) -> None:
        self._clear_rows()
        ctx = self.context()
        saved = (ctx.audio_labels if ctx else None) or {}

        for mic_index, (source, clips) in enumerate(
            sorted(by_source.items(), key=lambda x: x[1][0].get("mic", ""))
        ):
            if mic_index > 0:
                divider = QFrame()
                divider.setFrameShape(QFrame.Shape.HLine)
                divider.setFrameShadow(QFrame.Shadow.Sunken)
                divider.setStyleSheet("margin: 8px 0;")
                self._list.addWidget(divider)

            mic_name = str(clips[0].get("mic", "Mic"))
            row = QVBoxLayout()
            row.setContentsMargins(0, 4, 0, 6)
            row.setSpacing(4)
            wrap = QWidget()
            wrap.setLayout(row)

            header = body_label(f"{mic_name} — {clips[0].get('source_name', '')}")
            header.setStyleSheet("margin: 0; padding: 0;")
            row.addWidget(header)

            clip_row = QHBoxLayout()
            clip_row.setContentsMargins(0, 0, 0, 0)
            clip_row.setSpacing(8)
            for clip in sorted(clips, key=lambda c: str(c.get("clip", ""))):
                label = f"Play {clip.get('clip', '?')}"
                btn = QPushButton(label)
                path = str(clip.get("preview", ""))
                btn.clicked.connect(lambda _checked=False, p=path: self._play(p))
                clip_row.addWidget(btn)
            clip_row.addStretch()
            row.addLayout(clip_row)

            group = QButtonGroup(wrap)
            self._mic_groups[source] = group
            roles = QHBoxLayout()
            roles.setContentsMargins(0, 0, 0, 0)
            roles.setSpacing(12)
            for label, role in (
                ("Host", "host"),
                ("Guest", "guest"),
                ("Do not use", "do_not_use"),
            ):
                radio = QRadioButton(label)
                radio.setProperty("role", role)
                group.addButton(radio)
                roles.addWidget(radio)
                if saved.get(source) == role:
                    radio.setChecked(True)
            row.addLayout(roles)
            self._list.addWidget(wrap)

    def _play(self, path: str) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            QMessageBox.warning(self, "Missing preview", f"Preview not found:\n{path}")
            return
        self._player.setSource(QUrl.fromLocalFile(str(file_path.resolve())))
        self._player.play()

    def _collect_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for source, group in self._mic_groups.items():
            role = _selected_role(group)
            if role:
                labels[source] = role
        return labels

    def _go_next(self) -> None:
        labels = self._collect_labels()
        if len(labels) != len(self._mic_groups):
            QMessageBox.warning(
                self,
                "Incomplete labels",
                "Choose a role for every microphone.",
            )
            return
        try:
            self.controller.validate_audio_labels(labels)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid microphone labels", str(exc))
            return

        ctx = self.context()
        if ctx is not None:
            ctx.audio_labels = labels
        self.navigate.emit("D3")


class ApplyLabelsScreen(ScreenWidget):
    """D3 — move labeled files into Raw."""

    screen_id = "D3"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._log_lines: list[str] = []

        layout = QVBoxLayout(self)
        self._headline = heading_label("Applying labels…")
        layout.addWidget(self._headline)
        self._detail = body_label("Moving files into the Raw folder…")
        layout.addWidget(self._detail)
        self._log = selectable_plain_text(visible_rows=10)
        layout.addWidget(self._log, stretch=1)

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("D2"))
        row.addWidget(back)
        row.addStretch()
        self._retry = QPushButton("Retry")
        self._retry.clicked.connect(self.on_enter)
        self._retry.hide()
        row.addWidget(self._retry)
        layout.addLayout(row)

    def on_enter(self) -> None:
        ctx = self.context()
        folder = _session_folder(self)
        if ctx is None or folder is None:
            self._detail.setText("Missing session or label data.")
            return
        if not ctx.video_labels or not ctx.audio_labels:
            self._detail.setText("Go back and complete camera and microphone labels.")
            return

        self._headline.setText("Applying labels…")
        self._detail.setText("Copying labeled files into Raw…")
        self._log_lines = ["Preparing to copy files…"]
        self._log.setPlainText(self._log_lines[0])
        self._retry.hide()

        if self._worker is not None and self._worker.isRunning():
            return

        self._worker = CallableWorker(self._apply, ctx, folder)
        self._worker.progress.connect(self._on_copy_progress)
        self._worker.finished_ok.connect(lambda _r: self.navigate.emit("D4"))
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _append_log(self, line: str) -> None:
        self._log_lines.append(line)
        self._log.setPlainText("\n".join(self._log_lines))
        scrollbar = self._log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_copy_progress(self, event: dict) -> None:
        phase = event.get("phase")
        src = event.get("src", "")
        dest = event.get("dest", "")
        index = event.get("index", 0)
        total = event.get("total", 0)
        if phase == "start":
            if self._log_lines == ["Preparing to copy files…"]:
                self._log_lines = []
                self._log.setPlainText("")
            self._append_log(f"[{index}/{total}] Copying…")
            self._append_log(f"  from: {src}")
            self._append_log(f"  to:   {dest}")
        elif phase == "done":
            self._append_log("  ✓ done")
        elif phase == "skipped":
            if self._log_lines == ["Preparing to copy files…"]:
                self._log_lines = []
                self._log.setPlainText("")
            self._append_log(f"[{index}/{total}] Already in Raw (skipped)")
            self._append_log(f"  {dest}")

    def _apply(self, ctx, folder: Path, progress=None) -> dict:
        def on_copy(src: Path, dest: Path, index: int, total: int, phase: str) -> None:
            if progress is not None:
                progress(
                    {
                        "phase": phase,
                        "src": str(src),
                        "dest": str(dest),
                        "index": index,
                        "total": total,
                    }
                )

        return self.controller.apply_labels(
            folder,
            video_labels=ctx.video_labels or {},
            audio_labels=ctx.audio_labels or {},
            allow_overwrite=ctx.allow_overwrite,
            on_copy=on_copy,
        )

    def _on_fail(self, message: str) -> None:
        self._headline.setText("Could not apply labels")
        self._detail.setText(message)
        self._append_log(f"ERROR: {message}")
        self._retry.show()
        if "overwrite" in message.lower():
            ctx = self.context()
            if ctx is not None and confirm_action(
                self,
                title="Overwrite Raw files?",
                text="Labeled Raw files already exist.",
                detail="Replace them and continue?",
            ):
                ctx.allow_overwrite = True
                self.on_enter()


class EstimatePrepScreen(ScreenWidget):
    """D4 — show Estimate A and offer to start prep."""

    screen_id = "D4"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("Ready to process"))
        self._summary = body_label("")
        layout.addWidget(self._summary)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        layout.addStretch()

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("D2"))
        row.addWidget(back)
        row.addStretch()
        start = QPushButton("Start processing")
        start.setMinimumHeight(44)
        start.setDefault(True)
        start.clicked.connect(lambda: self.navigate.emit("E1"))
        row.addWidget(start)
        layout.addLayout(row)

    def on_enter(self) -> None:
        folder = _session_folder(self)
        if folder is None:
            self._summary.setText("No session folder.")
            self._banner.set_path(None)
            return

        self._banner.set_path(folder)
        try:
            state = self.controller.load_session_state(folder)
        except Exception as exc:
            self._summary.setText(f"Could not read session state:\n{exc}")
            return

        eta = state.get("estimate_prep") or {}
        from app.controller.paths import ensure_scripts_path

        ensure_scripts_path()
        from piab_fast_preview_lib import estimate_fast_preview_prep

        eta_fast = estimate_fast_preview_prep()
        if not eta and not eta_fast:
            self._summary.setText(
                "Prep estimate is not available yet. Go back and apply labels first."
            )
            return

        source_human = str(
            (eta.get("breakdown") or {}).get("source_duration_human") or "?"
        )
        fast_summary = str(eta_fast.get("summary") or "a few minutes")
        lines = [
            "Labeling is complete. Files are in the session Raw folder.",
            "",
            f"Source recording length: {source_human}",
            "",
            "Next: Fast Preview (a short 1-minute review from preview clips).",
            f"Estimated Fast Preview time: {fast_summary}",
            "",
            "Full-length files are not created until you approve the preview.",
        ]
        self._summary.setText("\n".join(lines))
