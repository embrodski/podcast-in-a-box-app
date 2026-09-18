"""F2 — 1-minute preview review."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.dialogs import confirm_action
from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label
from app.gui.widgets.video_playback import SingleVideoReviewPane
from app.gui.widgets.worker import CallableWorker, start_callable_worker


class OneMinReviewScreen(ScreenWidget):
    """F2 — play 1 Min Test.mp4 and approve or fix labeling."""

    screen_id = "F2"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._video_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(heading_label("Review 1-minute preview"))
        self._intro = body_label(
            "Watch the short autocut preview. If Host and Guest look correct, "
            "continue. If Host and Guest sound or look swapped, use "
            "Host/Guest swapped to fix speaker mapping (Raw files stay unchanged)."
        )
        layout.addWidget(self._intro)

        self._status = body_label("")
        layout.addWidget(self._status)

        self._playback = SingleVideoReviewPane(min_video_height=280)
        layout.addWidget(self._playback, stretch=1)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        self._actions = QWidget()
        actions_layout = QVBoxLayout(self._actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)

        good = QPushButton("Looks good")
        good.setMinimumHeight(44)
        good.setDefault(True)
        good.clicked.connect(self._looks_good)
        actions_layout.addWidget(good)

        swapped = QPushButton("Host/Guest swapped (fix speaker mapping)")
        swapped.setMinimumHeight(40)
        swapped.clicked.connect(self._host_guest_swapped_in_edit)
        actions_layout.addWidget(swapped)

        relabel = QPushButton("Re-label cameras/mics…")
        relabel.setMinimumHeight(40)
        relabel.clicked.connect(self._relabel_cameras_mics)
        actions_layout.addWidget(relabel)

        layout.addWidget(self._actions)

    def on_enter(self) -> None:
        self._playback.stop()
        folder = self.session_folder()
        if folder is None:
            self._status.setText("No session folder.")
            self._banner.set_path(None)
            self._set_actions_enabled(False)
            return

        self._banner.set_path(folder)
        self._set_actions_enabled(True)

        try:
            state = self.controller.load_session_state(folder)
            self._video_path = self.controller.resolve_one_min_test_path(state, folder)
        except Exception as exc:
            self._status.setText(str(exc))
            self._set_actions_enabled(False)
            return

        self._status.setText(f"Preview: {self._video_path.name}")
        self._playback.set_source(self._video_path)

    def on_leave(self) -> None:
        self._playback.close_source()

    def _set_actions_enabled(self, enabled: bool) -> None:
        self._actions.setEnabled(enabled)
        self._playback.set_controls_enabled(enabled)

    def _looks_good(self) -> None:
        folder = self.session_folder()
        if folder is None:
            return

        def _on_ok(state: dict) -> None:
            folder_local = folder
            try:
                self.controller.request_full_job(folder_local)
            except Exception as exc:
                QMessageBox.warning(self, "Could not queue full render", str(exc))
                return
            window = self.window()
            if hasattr(window, "handoff_to_final_render"):
                window.handoff_to_final_render(folder_local)
                return
            self.navigate.emit("F4")

        self._run_worker(
            "Saving approval…",
            self.controller.approve_one_min_test,
            folder,
            on_ok=_on_ok,
        )

    def _host_guest_swapped_in_edit(self) -> None:
        folder = self.session_folder()
        if folder is None:
            return
        if not confirm_action(
            self,
            title="Fix Host/Guest speaker mapping?",
            text=(
                "This assumes Raw and Input files are labeled correctly. "
                "It toggles transcript speaker IDs, regenerates the edit, and "
                "re-renders the 1-minute preview."
            ),
            detail="Prepped video/audio files and the detail transcript stay unchanged.",
        ):
            return

        allow_overwrite = self._confirm_overwrite_if_needed(folder, "rerun_one_min")
        if allow_overwrite is None:
            return

        def _fix() -> dict:
            return self.controller.fix_audio_speaker_swap(
                folder,
                allow_overwrite=allow_overwrite,
            )

        def _on_fix_ok(state: dict) -> None:
            if self.controller.needs_sync_offset_choice(state):
                self.navigate.emit("F2a")
                return
            self._video_path = self.controller.resolve_one_min_test_path(state, folder)
            self._status.setText(f"Preview: {self._video_path.name}")
            self._playback.set_source(self._video_path)

        self._run_worker(
            "Updating preview…",
            _fix,
            on_ok=_on_fix_ok,
        )

    def _relabel_cameras_mics(self) -> None:
        folder = self.session_folder()
        if folder is None:
            return
        if not confirm_action(
            self,
            title="Re-label cameras and mics?",
            text=(
                "Re-labeling sends you back to camera labeling and clears preview work. "
                "This takes a long time."
            ),
            detail=(
                "Use Host/Guest swapped if only speaker mapping is wrong in the edit. "
                "Continue to re-label?"
            ),
        ):
            return

        def _clear() -> None:
            self.controller.clear_preview_for_relabel(folder)

        self._run_worker(
            "Preparing to re-label…",
            _clear,
            on_ok=lambda _r: self._after_relabel(),
        )

    def _after_relabel(self) -> None:
        ctx = self.context()
        if ctx is not None:
            ctx.video_labels = None
            ctx.audio_labels = None
        self._playback.stop()
        self.navigate.emit("D1")

    def _confirm_overwrite_if_needed(
        self,
        folder: Path,
        action: str,
    ) -> bool | None:
        at_risk = self.controller.check_overwrite_risk(action, folder)
        if not at_risk:
            return False
        if confirm_action(
            self,
            title="Overwrite existing preview?",
            text="Re-rendering will replace the current 1-minute test file.",
            detail="Continue?",
        ):
            return True
        return None

    def _run_worker(
        self,
        status: str,
        fn,
        *args,
        on_ok,
        **kwargs,
    ) -> None:
        self._status.setText(status)
        self._set_actions_enabled(False)
        if start_callable_worker(
            self,
            fn,
            *args,
            on_ok=on_ok,
            on_fail=self._on_worker_failed,
            **kwargs,
        ) is None:
            self._set_actions_enabled(True)

    def _on_worker_failed(self, message: str) -> None:
        self._status.setText(message)
        self._set_actions_enabled(True)
        QMessageBox.warning(self, "Could not continue", message)

    def _reload_video(self, path: object) -> None:
        self._video_path = Path(str(path))
        self._playback.set_source(self._video_path)
        self._status.setText(
            f"Updated preview: {self._video_path.name}. Watch again before continuing."
        )
        self._set_actions_enabled(True)
