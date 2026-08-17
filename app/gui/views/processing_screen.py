"""E1 — background prep progress and abort."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

from app.controller.prep_progress import clear_prep_failure, failure_summary
from app.controller.storage_gate import assess_prep_storage
from app.gui.dialogs import confirm_action, confirm_hold_outside_queue
from app.gui.failure_context import navigate_to_failure
from app.gui.storage_prompts import gate_low_disk, maybe_offer_clean_on_disk_failure
from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label


def _session_folder(screen: ScreenWidget) -> Path | None:
    ctx = screen.context()
    if ctx is None or ctx.session_folder is None:
        return None
    return ctx.session_folder


class ProcessingScreen(ScreenWidget):
    """E1 — run piab_run_prep.py and poll session state."""

    screen_id = "E1"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._prep_job_id: str | None = None
        self._starting = False
        self._local_step: str | None = None
        self._local_started: datetime | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("Processing…"))
        self._phase = body_label("")
        self._phase.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self._phase)
        self._timing = body_label("")
        self._timing.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self._timing)
        self._current = body_label("Starting…")
        layout.addWidget(self._current)

        self._steps = body_label("")
        layout.addWidget(self._steps)

        self._detail = body_label("")
        layout.addWidget(self._detail)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        layout.addStretch()

        row = QHBoxLayout()
        self._home = QPushButton("Back to home")
        self._home.clicked.connect(lambda: self.navigate.emit("A1"))
        self._home.hide()
        row.addWidget(self._home)

        self._retry = QPushButton("Retry")
        self._retry.clicked.connect(self._start_prep)
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
            return

        self._banner.set_path(folder)
        self._home.hide()
        self._retry.hide()
        self._hold.hide()
        self._abort.show()
        self._prep_job_id = None
        self._starting = False
        self._local_step = None
        self._local_started = None

        try:
            state = self.controller.load_session_state(folder)
        except Exception as exc:
            self._current.setText("Could not read session state.")
            self._detail.setText(str(exc))
            self._abort.hide()
            self._hold.hide()
            self._retry.show()
            return

        progress = self.controller.read_prep_progress(folder)
        if progress.resume_at == "14_done":
            self.navigate.emit("F5")
            return
        if self.controller.full_after_preview_pending(state):
            window = self.window()
            folder_ref = folder

            def _handoff_final() -> None:
                if hasattr(window, "handoff_to_final_render"):
                    window.handoff_to_final_render(folder_ref)
                    return
                self.navigate.emit("F4")

            QTimer.singleShot(0, _handoff_final)
            return
        if progress.prep_complete:
            if progress.resume_at == "10a_sync_offset_approval":
                self.navigate.emit("F2a")
            else:
                self.navigate.emit("F2")
            return

        existing = self.controller.find_running_prep_job(folder)
        if existing is not None:
            self._prep_job_id = existing.id
            self._refresh_ui(folder)
            self._poll.start()
            return

        entry = self.controller.job_queue.entry_for(folder, "fast_preview")
        if entry is not None and entry.status == "held":
            self._show_held_state()
            return

        self._start_prep()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._poll.stop()
        super().hideEvent(event)

    def _start_processing_job(
        self,
        folder: Path,
        state: dict,
        *,
        allow_overwrite: bool,
        resume: bool = False,
    ):
        return self.controller.request_fast_preview(
            folder,
            allow_overwrite=allow_overwrite,
        )

    def _start_prep(self) -> None:
        folder = _session_folder(self)
        if folder is None or self._starting:
            return
        held = self.controller.job_queue.entry_for(folder, "fast_preview")
        if held is not None and held.status == "held":
            self._show_held_state()
            return

        ctx = self.context()
        try:
            state = self.controller.load_session_state(folder)
        except Exception as exc:
            self._current.setText("Could not read session state.")
            self._detail.setText(str(exc))
            return

        assessment = assess_prep_storage(folder)
        action = gate_low_disk(self, assessment, return_screen="E1")
        if action == "go_clean":
            return
        if action == "abort":
            self._current.setText("Prep not started — low disk space.")
            self._detail.setText(assessment.message)
            self._retry.show()
            return

        self._phase.setText("Fast Preview (short clips, then 1-minute review)")

        allow_overwrite = ctx.allow_overwrite if ctx is not None else False

        self._starting = True
        self._retry.hide()
        self._hold.hide()
        self._abort.show()
        self._current.setText("Starting Fast Preview…")
        self._detail.setText("This may take a while. Do not close the app.")
        clear_prep_failure(folder)

        try:
            job = self._start_processing_job(
                folder,
                state,
                allow_overwrite=allow_overwrite,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "overwrite" in message.lower():
                if ctx is not None and confirm_action(
                    self,
                    title="Overwrite existing outputs?",
                    text="Prep would replace files that already exist.",
                    detail="Continue and overwrite?",
                ):
                    ctx.allow_overwrite = True
                    self._starting = False
                    self._start_prep()
                    return
                navigate_to_failure(
                    self,
                    summary="Prep blocked — files already exist.",
                    retry_screen="E1",
                    detail=message,
                    report=False,
                )
            else:
                navigate_to_failure(
                    self,
                    summary="Could not start prep.",
                    retry_screen="E1",
                    detail=message,
                )
            self._starting = False
            return
        finally:
            self._starting = False

        if job is None:
            self._prep_job_id = None
            self._show_queued_waiting()
            self._poll.start()
            return

        self._prep_job_id = job.id
        self._refresh_ui(folder)
        self._poll.start()

    def _show_queued_waiting(self) -> None:
        self._current.setText(
            "This autocut job is in queue, and will be processed in the order it was received."
        )
        self._detail.setText("Fast Preview will start when the current preview job finishes.")
        self._hold.show()
        self._abort.show()

    def _show_held_state(self) -> None:
        self._poll.stop()
        self._current.setText("This job is on hold and will not start automatically.")
        self._detail.setText("Use Resume on the Home On hold list, or Resume session, to restart it.")
        self._hold.hide()
        self._abort.hide()
        self._home.show()

    def _confirm_hold(self) -> None:
        folder = _session_folder(self)
        if folder is None or self._prep_job_id is not None:
            return
        if not confirm_hold_outside_queue(self):
            return
        self.controller.hold_queued_job(folder, "fast_preview")
        self._poll.stop()
        window = self.window()
        if hasattr(window, "close_flow_to_home"):
            window.close_flow_to_home()
            return
        self.navigate.emit("A1")

    def _confirm_abort(self) -> None:
        folder = _session_folder(self)
        if self._prep_job_id is None:
            if folder is None:
                return
            if not confirm_action(
                self,
                title="Remove from queue?",
                text=(
                    "This will cancel the autocut completely, and remove this job "
                    "from the queue. Your video and audio files will remain untouched "
                    "where they are."
                ),
            ):
                return
            self.controller.cancel_queued_job(folder, "fast_preview")
            self.navigate.emit("A1")
            return
        if not confirm_action(
            self,
            title="Abort processing?",
            text="Stop the current prep run?",
            detail="Partial outputs are kept. You can resume this session later.",
        ):
            return

        result = self.controller.abort_job(self._prep_job_id, confirmed=True)
        self._poll.stop()
        self._prep_job_id = None
        navigate_to_failure(
            self,
            summary="Processing was stopped.",
            retry_screen="E1",
            detail=result.message or "You can try again or resume this session later.",
            aborted=True,
        )

    def _on_poll_tick(self) -> None:
        if not self.isVisible():
            return
        folder = _session_folder(self)
        if folder is None:
            return

        if self._prep_job_id is None:
            existing = self.controller.find_running_prep_job(folder)
            if existing is not None:
                self._prep_job_id = existing.id
                self._hold.hide()
                self._current.setText("Starting Fast Preview…")
            else:
                self._show_queued_waiting()
                return

        finished = self.controller.poll_jobs()
        for job in finished:
            if job.id != self._prep_job_id:
                continue
            self._poll.stop()
            if job.status == "completed":
                self._handle_prep_finished(folder)
            elif job.status == "aborted":
                pass
            else:
                self._handle_prep_failed(folder, job.message)
            return

        self._refresh_ui(folder)

    def _handle_prep_finished(self, folder: Path) -> None:
        try:
            state = self.controller.load_session_state(folder)
        except Exception:
            state = {}
        if state.get("resume_at") == "14_done":
            self.navigate.emit("F5")
            return

        progress = self.controller.read_prep_progress(folder)
        if progress.prep_complete:
            if progress.resume_at == "10a_sync_offset_approval":
                self.navigate.emit("F2a")
            else:
                self.navigate.emit("F2")
            return
        if progress.failure:
            self._handle_prep_failed(folder, progress.failure.get("summary", "Prep failed."))
            return
        self._current.setText("Prep finished.")
        self._detail.setText("Waiting for session state to update…")
        self._retry.show()

    def _prep_failure_summary(self, folder: Path, failure: dict | None, fallback: str) -> str:
        if not failure:
            return fallback
        try:
            state = self.controller.load_session_state(folder)
        except Exception:
            state = None
        return (
            failure_summary(failure, state=state, working_folder=folder)
            or fallback
        )

    def _handle_prep_failed(self, folder: Path, message: str) -> None:
        progress = self.controller.read_prep_progress(folder)
        fallback = message or "Prep failed."
        summary = self._prep_failure_summary(folder, progress.failure, fallback)
        file_detail = progress.failure.get("error_detail") if progress.failure else None
        maybe_offer_clean_on_disk_failure(
            self,
            summary=summary,
            retry_screen="E1",
            detail=file_detail if isinstance(file_detail, str) else None,
        )

    def _refresh_ui(self, folder: Path) -> None:
        progress = self.controller.read_prep_progress(
            folder,
            fallback_started_at=self._local_started,
        )
        if progress.current_step and progress.current_step != self._local_step:
            self._local_step = progress.current_step
            self._local_started = datetime.now().astimezone()
            progress = self.controller.read_prep_progress(
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

        if progress.failure and not progress.prep_complete:
            if self._prep_job_id is not None:
                job = self.controller.get_job(self._prep_job_id)
                if job is not None and job.status == "running":
                    return
            summary = self._prep_failure_summary(folder, progress.failure, "Prep failed.")
            if summary:
                self._poll.stop()
                maybe_offer_clean_on_disk_failure(
                    self,
                    summary=summary,
                    retry_screen="E1",
                    detail=progress.failure.get("error_detail")
                    if isinstance(progress.failure.get("error_detail"), str)
                    else None,
                )
                return

        if progress.resume_at == "14_done":
            self._poll.stop()
            self.navigate.emit("F5")
            return

        if progress.prep_complete:
            self._poll.stop()
            if progress.resume_at == "10a_sync_offset_approval":
                self.navigate.emit("F2a")
            else:
                self.navigate.emit("F2")
            return
