"""F4 — full interview render progress."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

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
        folder = self.session_folder()
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

    def release_idle_resources(self) -> None:
        self._poll.stop()
        super().release_idle_resources()

    def _start_render(self) -> None:
        folder = self.session_folder()
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
        folder = self.session_folder()
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

        folder = self.session_folder()
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
        folder = self.session_folder()
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
