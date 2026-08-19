"""Review and full-render estimate screens F2–F3."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.controller.prep_progress import clear_prep_failure, failure_summary
from app.controller.storage_gate import assess_render_storage
from app.gui.dialogs import (
    REMOVE_FROM_QUEUE_TEXT,
    confirm_action,
    confirm_hold_outside_queue,
)
from app.gui.failure_context import navigate_to_failure
from app.gui.storage_prompts import gate_low_disk, maybe_offer_clean_on_disk_failure
from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label
from app.gui.widgets.video_playback import (
    MediaTimelineControls,
    SingleVideoReviewPane,
    close_media_player,
)
from app.gui.widgets.worker import CallableWorker


def _session_folder(screen: ScreenWidget) -> Path | None:
    ctx = screen.context()
    if ctx is None or ctx.session_folder is None:
        return None
    return ctx.session_folder


class SyncOffsetReviewScreen(ScreenWidget):
    """F2a — side-by-side A/B sync offset choice before general 1-min approval."""

    screen_id = "F2a"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._no_offset_path: Path | None = None
        self._forced_path: Path | None = None

        self._player_left = QMediaPlayer(self)
        self._audio_left = QAudioOutput(self)
        self._player_left.setAudioOutput(self._audio_left)

        self._player_right = QMediaPlayer(self)
        self._audio_right = QAudioOutput(self)
        self._player_right.setAudioOutput(self._audio_right)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(heading_label("Which sync sounds better?"))
        layout.addWidget(
            body_label(
                "Video sync was uncertain, so two 1-minute previews were rendered. "
                "They differ only in audio offset — watch both and pick the one with "
                "better lip sync. You will review cameras and speakers on the next screen."
            )
        )

        self._status = body_label("")
        layout.addWidget(self._status)

        row = QHBoxLayout()
        row.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.addWidget(body_label("Start-aligned (no offset)"))
        self._video_left = QVideoWidget()
        self._video_left.setMinimumHeight(320)
        self._player_left.setVideoOutput(self._video_left)
        left_col.addWidget(self._video_left, stretch=1)
        self._play_left = QPushButton("Play left")
        self._play_left.clicked.connect(lambda: self._toggle(self._player_left, self._play_left))
        left_col.addWidget(self._play_left)
        row.addLayout(left_col, stretch=1)

        right_col = QVBoxLayout()
        right_col.addWidget(body_label("Forced detected offset"))
        self._video_right = QVideoWidget()
        self._video_right.setMinimumHeight(320)
        self._player_right.setVideoOutput(self._video_right)
        right_col.addWidget(self._video_right, stretch=1)
        self._play_right = QPushButton("Play right")
        self._play_right.clicked.connect(lambda: self._toggle(self._player_right, self._play_right))
        right_col.addWidget(self._play_right)
        row.addLayout(right_col, stretch=1)

        layout.addLayout(row, stretch=1)

        self._timeline = MediaTimelineControls()
        self._timeline.bind_players(self._player_left, self._player_right)
        layout.addWidget(self._timeline)

        both_row = QHBoxLayout()
        self._play_both = QPushButton("Play both")
        self._play_both.clicked.connect(self._play_both_clips)
        both_row.addWidget(self._play_both)
        self._pause_both = QPushButton("Pause both")
        self._pause_both.clicked.connect(self._pause_both_clips)
        both_row.addWidget(self._pause_both)
        both_row.addStretch()
        layout.addLayout(both_row)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        actions = QHBoxLayout()
        start_btn = QPushButton("Use start-aligned (left)")
        start_btn.setMinimumHeight(44)
        start_btn.clicked.connect(lambda: self._choose("start_aligned"))
        actions.addWidget(start_btn)

        forced_btn = QPushButton("Use forced offset (right)")
        forced_btn.setMinimumHeight(44)
        forced_btn.setDefault(True)
        forced_btn.clicked.connect(lambda: self._choose("forced_offset"))
        actions.addWidget(forced_btn)
        layout.addLayout(actions)

    def on_enter(self) -> None:
        self._player_left.stop()
        self._player_right.stop()
        self._timeline.reset()
        self._play_left.setText("Play left")
        self._play_right.setText("Play right")

        folder = _session_folder(self)
        if folder is None:
            self._status.setText("No session folder.")
            self._banner.set_path(None)
            self.setEnabled(False)
            return

        self.setEnabled(True)
        self._banner.set_path(folder)

        try:
            state = self.controller.load_session_state(folder)
            no_path, forced_path = self.controller.resolve_ab_test_paths(state, folder)
        except Exception as exc:
            self._status.setText(str(exc))
            self.setEnabled(False)
            return

        self._no_offset_path = no_path
        self._forced_path = forced_path
        self._status.setText(
            f"Left: {no_path.name}  ·  Right: {forced_path.name}"
        )
        self._player_left.setSource(QUrl.fromLocalFile(str(no_path)))
        self._player_right.setSource(QUrl.fromLocalFile(str(forced_path)))

    def on_leave(self) -> None:
        close_media_player(self._player_left)
        close_media_player(self._player_right)
        self._timeline.reset()
        self._play_left.setText("Play left")
        self._play_right.setText("Play right")

    def _toggle(self, player: QMediaPlayer, button: QPushButton) -> None:
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
            button.setText(button.text().replace("Pause", "Play"))
        else:
            player.play()
            button.setText(button.text().replace("Play", "Pause"))

    def _play_both_clips(self) -> None:
        self._player_left.play()
        self._player_right.play()
        self._play_left.setText("Pause left")
        self._play_right.setText("Pause right")

    def _pause_both_clips(self) -> None:
        self._player_left.pause()
        self._player_right.pause()
        self._play_left.setText("Play left")
        self._play_right.setText("Play right")

    def _choose(self, choice: str) -> None:
        folder = _session_folder(self)
        if folder is None:
            return
        self._run_choice_worker(choice, folder)

    def _run_choice_worker(self, choice: str, folder: Path) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self.setEnabled(False)
        self._status.setText("Saving your choice…")

        def _task() -> dict:
            return self.controller.record_sync_offset_choice(folder, choice)

        self._worker = CallableWorker(_task)
        self._worker.finished_ok.connect(self._on_choice_saved)
        self._worker.failed.connect(self._on_choice_failed)
        self._worker.start()

    def _on_choice_saved(self, _state: object) -> None:
        self.setEnabled(True)
        self.navigate.emit("F2")

    def _on_choice_failed(self, message: str) -> None:
        self.setEnabled(True)
        self._status.setText(message)
        QMessageBox.warning(self, "Could not save choice", message)


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
        folder = _session_folder(self)
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
        folder = _session_folder(self)
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
        folder = _session_folder(self)
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
        folder = _session_folder(self)
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
        if self._worker is not None and self._worker.isRunning():
            return
        self._status.setText(status)
        self._set_actions_enabled(False)
        self._worker = CallableWorker(fn, *args, **kwargs)
        self._worker.finished_ok.connect(on_ok)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

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


class EstimateFullScreen(ScreenWidget):
    """F3 — show Estimate B before full interview render."""

    screen_id = "F3"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("Ready for full render"))
        self._summary = body_label("")
        layout.addWidget(self._summary)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        layout.addStretch()

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("F2"))
        row.addWidget(back)
        row.addStretch()
        start = QPushButton("Start full render")
        start.setMinimumHeight(44)
        start.setDefault(True)
        start.clicked.connect(lambda: self.navigate.emit("F4"))
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

        eta = state.get("estimate_full") or {}
        if not eta:
            self._summary.setText(
                "Full-render estimate is not available yet. "
                "Go back and approve the 1-minute preview first."
            )
            return

        summary = str(eta.get("summary") or "unknown duration")
        source_human = str(
            (eta.get("breakdown") or {}).get("source_duration_human") or "?"
        )
        lines = [
            "The 1-minute preview is approved.",
            "",
            f"Source recording length: {source_human}",
            f"Estimated full interview render time: {summary}",
            "",
            "The full render runs in the background. You can abort from the next screen.",
        ]
        self._summary.setText("\n".join(lines))


class FullRenderScreen(ScreenWidget):
    """F4 — run piab_run_full_render.py and poll session state."""

    screen_id = "F4"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._render_job_id: str | None = None
        self._starting = False
        self._local_step: str | None = None
        self._local_started: datetime | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._heading = heading_label("Rendering full interview…")
        layout.addWidget(self._heading)
        self._timing = body_label("")
        self._timing.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self._timing)
        self._pace = body_label(
            "Most projects take approximately 1 minute per minute of source video to complete."
        )
        self._pace.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self._pace.hide()
        layout.addWidget(self._pace)
        self._current = body_label("Starting…")
        layout.addWidget(self._current)

        self._steps = body_label("")
        layout.addWidget(self._steps)

        self._detail = body_label("")
        layout.addWidget(self._detail)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        layout.addStretch()

        self._home_hint = body_label(
            "You can return to Home Screen while this is rendering in order to "
            "start another recording"
        )
        layout.addWidget(self._home_hint)
        self._return_home = QPushButton("Return to Home")
        self._return_home.setMinimumHeight(40)
        self._return_home.clicked.connect(self._return_to_home)
        layout.addWidget(self._return_home)

        row = QHBoxLayout()
        self._home = QPushButton("Back to home")
        self._home.clicked.connect(self._return_to_home)
        self._home.hide()
        row.addWidget(self._home)

        self._retry = QPushButton("Retry")
        self._retry.clicked.connect(self._start_render)
        self._retry.hide()
        row.addWidget(self._retry)

        row.addStretch()

        self._hold = QPushButton("Hold Outside Queue")
        self._hold.clicked.connect(self._confirm_hold)
        self._hold.hide()
        row.addWidget(self._hold)

        self._abort = QPushButton("Abort")
        self._abort.clicked.connect(self._confirm_abort)
        row.addWidget(self._abort)

        layout.addLayout(row)

        self._poll = QTimer(self)
        self._poll.setInterval(2000)
        self._poll.timeout.connect(self._on_poll_tick)

    def on_enter(self) -> None:
        folder = _session_folder(self)
        if folder is None:
            self._current.setText("No session folder.")
            self._steps.setText("")
            self._detail.setText("Go back and create or open a session first.")
            self._banner.set_path(None)
            self._abort.hide()
            self._hold.hide()
            self._retry.hide()
            self._home.show()
            self._home_hint.show()
            self._return_home.show()
            return

        self._banner.set_path(folder)
        self._home.hide()
        self._retry.hide()
        self._hold.hide()
        self._abort.show()
        self._home_hint.show()
        self._return_home.show()
        self._render_job_id = None
        self._starting = False
        self._local_step = None
        self._local_started = None

        try:
            self.controller.load_session_state(folder)
        except Exception as exc:
            self._current.setText("Could not read session state.")
            self._detail.setText(str(exc))
            self._abort.hide()
            self._hold.hide()
            self._retry.show()
            return

        progress = self.controller.read_render_progress(folder)
        if progress.render_complete:
            self.navigate.emit("F5")
            return

        existing = self.controller.find_running_render_job(folder)
        if existing is not None:
            self._heading.setText("Rendering full interview…")
            self._render_job_id = existing.id
            self._refresh_ui(folder)
            self._poll.start()
            return

        entry = self.controller.job_queue.entry_for(folder, "full")
        if entry is not None and entry.status == "held":
            self._show_held_state()
            return
        if entry is not None and entry.status == "queued":
            self._show_queued_waiting()
            self._poll.start()
            return

        self._start_render()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._poll.stop()
        super().hideEvent(event)

    def _start_render(self) -> None:
        folder = _session_folder(self)
        if folder is None or self._starting:
            return
        held = self.controller.job_queue.entry_for(folder, "full")
        if held is not None and held.status == "held":
            self._show_held_state()
            return

        ctx = self.context()
        allow_overwrite = ctx.allow_overwrite if ctx is not None else False
        if not allow_overwrite and self.controller.resume_full_needs_overwrite(folder):
            allow_overwrite = True

        assessment = assess_render_storage(folder)
        action = gate_low_disk(self, assessment, return_screen="F4")
        if action == "go_clean":
            return
        if action == "abort":
            self._current.setText("Full render not started — low disk space.")
            self._detail.setText(assessment.message)
            self._retry.show()
            return

        self._starting = True
        self._retry.hide()
        self._hold.hide()
        self._abort.show()
        self._heading.setText("Rendering full interview…")
        self._current.setText("Starting full render…")
        self._detail.setText("This may take a while. Do not close the app.")
        clear_prep_failure(folder)

        try:
            job = self.controller.request_full_job(
                folder,
                allow_overwrite=allow_overwrite,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "overwrite" in message.lower():
                if ctx is not None and confirm_action(
                    self,
                    title="Overwrite existing outputs?",
                    text="Full render would replace files that already exist.",
                    detail="Continue and overwrite?",
                ):
                    ctx.allow_overwrite = True
                    self._starting = False
                    self._start_render()
                    return
                navigate_to_failure(
                    self,
                    summary="Render blocked — files already exist.",
                    retry_screen="F4",
                    detail=message,
                    report=False,
                )
            else:
                navigate_to_failure(
                    self,
                    summary="Could not start full render.",
                    retry_screen="F4",
                    detail=message,
                )
            self._starting = False
            return
        finally:
            self._starting = False

        if job is None:
            self._render_job_id = None
            self._show_queued_waiting()
            self._poll.start()
            return

        self._render_job_id = job.id
        self._local_started = datetime.now().astimezone()
        self._refresh_ui(folder)
        self._poll.start()

    def _show_queued_waiting(self) -> None:
        self._heading.setText("Final Render Window")
        self._current.setText(
            "This autocut job is in queue, and will be processed in the order it was received."
        )
        self._detail.setText(
            "The app will not create full-length prepped files until this job starts."
        )
        self._abort.setText("Abort")
        self._hold.show()
        self._abort.show()

    def _show_held_state(self) -> None:
        self._poll.stop()
        self._heading.setText("Final Render Window")
        self._current.setText("This job is on hold and will not start automatically.")
        self._detail.setText("Use Resume on the Home On hold list, or Resume session, to restart it.")
        self._hold.hide()
        self._abort.hide()
        self._home.show()

    def _confirm_hold(self) -> None:
        folder = _session_folder(self)
        if folder is None or self._render_job_id is not None:
            return
        if not confirm_hold_outside_queue(self):
            return
        self.controller.hold_queued_job(folder, "full")
        self._poll.stop()
        self._leave_after_cancel()

    def prepare_for_abort_close(self) -> None:
        self._poll.stop()
        self._render_job_id = None

    def _return_to_home(self) -> None:
        window = self.window()
        if hasattr(window, "focus_home_keep_final"):
            window.focus_home_keep_final()
            return
        self.navigate.emit("A1")

    def _leave_after_cancel(self) -> None:
        window = self.window()
        if hasattr(window, "close_final_render"):
            window.close_final_render()
            return
        self.navigate.emit("A1")

    def _confirm_abort(self) -> None:
        window = self.window()
        if hasattr(window, "request_abort_final"):
            window.request_abort_final()
            return

        folder = _session_folder(self)
        if self._render_job_id is None:
            if folder is None:
                return
            if not confirm_action(
                self,
                title="Remove from queue?",
                text=REMOVE_FROM_QUEUE_TEXT,
            ):
                return
            self.controller.cancel_queued_job(folder, "full")
            self._poll.stop()
            self._leave_after_cancel()
            return
        if not confirm_action(
            self,
            title="Abort full render?",
            text="Stop the current render?",
            detail=REMOVE_FROM_QUEUE_TEXT,
        ):
            return

        self.controller.abort_job(
            self._render_job_id, confirmed=True, advance_queue=True
        )
        self._poll.stop()
        self._render_job_id = None
        self._leave_after_cancel()

    def _on_poll_tick(self) -> None:
        if not self.isVisible():
            return
        folder = _session_folder(self)
        if folder is None:
            return

        if self._render_job_id is None:
            existing = self.controller.find_running_render_job(folder)
            if existing is not None:
                self._render_job_id = existing.id
                self._hold.hide()
                self._heading.setText("Rendering full interview…")
                self._local_started = datetime.now().astimezone()
            else:
                return

        finished = self.controller.poll_jobs()
        for job in finished:
            if job.id != self._render_job_id:
                continue
            self._poll.stop()
            if job.status == "completed":
                self._handle_render_finished(folder)
            elif job.status == "aborted":
                pass
            else:
                self._handle_render_failed(folder, job.message)
            return

        self._refresh_ui(folder)

    def _handle_render_finished(self, folder: Path) -> None:
        progress = self.controller.read_render_progress(folder)
        if progress.render_complete:
            self.navigate.emit("F5")
            return
        if progress.failure:
            self._handle_render_failed(
                folder,
                progress.failure.get("summary", "Full render failed."),
            )
            return
        self._current.setText("Render finished.")
        self._detail.setText("Waiting for session state to update…")
        self._retry.show()

    def _handle_render_failed(self, folder: Path, message: str) -> None:
        progress = self.controller.read_render_progress(folder)
        detail = message or "Full render failed."
        if progress.failure:
            detail = failure_summary(progress.failure) or detail
        file_detail = progress.failure.get("error_detail") if progress.failure else None
        maybe_offer_clean_on_disk_failure(
            self,
            summary=detail,
            retry_screen="F4",
            detail=file_detail if isinstance(file_detail, str) else None,
        )

    def _refresh_ui(self, folder: Path) -> None:
        progress = self.controller.read_render_progress(
            folder,
            fallback_started_at=self._local_started,
        )
        if progress.current_step and progress.current_step != self._local_step:
            self._local_step = progress.current_step
            self._local_started = datetime.now().astimezone()
            progress = self.controller.read_render_progress(
                folder,
                fallback_started_at=self._local_started,
            )

        self._current.setText(progress.current_label)
        self._steps.setText("\n".join(progress.step_lines))
        timing_parts: list[str] = []
        if progress.step_started_display:
            timing_parts.append(f"Started {progress.step_started_display}")
        if progress.step_eta_display:
            timing_parts.append(progress.step_eta_display)
        timing_text = " · ".join(timing_parts)
        self._timing.setText(timing_text)
        self._timing.setVisible(bool(timing_text))
        self._pace.setVisible(bool(progress.step_started_display))

        if (
            progress.failure
            and not progress.render_complete
            and self._render_job_id is None
        ):
            summary = failure_summary(progress.failure)
            if summary:
                self._poll.stop()
                maybe_offer_clean_on_disk_failure(
                    self,
                    summary=summary,
                    retry_screen="F4",
                    detail=progress.failure.get("error_detail")
                    if isinstance(progress.failure.get("error_detail"), str)
                    else None,
                )
                return

        if progress.render_complete:
            self._poll.stop()
            self.navigate.emit("F5")
