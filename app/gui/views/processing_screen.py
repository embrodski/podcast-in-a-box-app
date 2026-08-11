"""E1 — background prep progress and abort."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

from app.controller.prep_progress import clear_prep_failure, failure_summary, prep_needs_resume
from app.gui.dialogs import confirm_action
from app.gui.failure_context import navigate_to_failure
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
            self._retry.hide()
            self._home.show()
            return

        self._banner.set_path(folder)
        self._home.hide()
        self._retry.hide()
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
            self._retry.show()
            return

        progress = self.controller.read_prep_progress(folder)
        if progress.prep_complete:
            self.navigate.emit("F2")
            return

        existing = self.controller.find_running_prep_job(folder)
        if existing is not None:
            self._prep_job_id = existing.id
            self._refresh_ui(folder)
            self._poll.start()
            return

        self._start_prep()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._poll.stop()
        super().hideEvent(event)

    def _start_prep(self) -> None:
        folder = _session_folder(self)
        if folder is None or self._starting:
            return

        ctx = self.context()
        try:
            state = self.controller.load_session_state(folder)
        except Exception as exc:
            self._current.setText("Could not read session state.")
            self._detail.setText(str(exc))
            return

        resume = prep_needs_resume(state, folder)
        allow_overwrite = ctx.allow_overwrite if ctx is not None else False

        self._starting = True
        self._retry.hide()
        self._abort.show()
        self._current.setText("Starting prep…")
        self._detail.setText("This may take a while. Do not close the app.")
        clear_prep_failure(folder)

        try:
            job = self.controller.start_prep(
                folder,
                allow_overwrite=allow_overwrite,
                resume=resume,
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

        self._prep_job_id = job.id
        self._refresh_ui(folder)
        self._poll.start()

    def _confirm_abort(self) -> None:
        if self._prep_job_id is None:
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
        navigate_to_failure(
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
                navigate_to_failure(
                    self,
                    summary=summary,
                    retry_screen="E1",
                    detail=progress.failure.get("error_detail")
                    if isinstance(progress.failure.get("error_detail"), str)
                    else None,
                )
                return

        if progress.prep_complete:
            self._poll.stop()
            if progress.resume_at == "10a_sync_offset_approval":
                self.navigate.emit("F2a")
            else:
                self.navigate.emit("F2")
            return
