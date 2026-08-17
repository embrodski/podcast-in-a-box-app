"""Subprocess job tracking and abort."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.controller.types import AbortResult, Job, JobKind, JobStatus


@dataclass
class _RunningJob:
    job: Job
    process: subprocess.Popen | None = None
    on_complete: Callable[[Job], None] | None = None
    abort_hook: Callable[[], None] | None = None
    reported: bool = False
    _wait_thread: threading.Thread | None = field(default=None, repr=False)


FAST_PREVIEW_KINDS = frozenset({"fast_preview"})
FULL_KINDS = frozenset({"prep", "render", "full_after_preview"})
PROCESSING_KINDS = FAST_PREVIEW_KINDS | FULL_KINDS


def kill_process_tree(pid: int | None) -> None:
    """Force-kill ``pid`` and all descendants (ffmpeg, nested python, etc.)."""
    if pid is None or pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        return

    try:
        import os
        import signal

        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            import os
            import signal

            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return

    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            import os

            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(0.1)
    try:
        import os
        import signal

        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            import os
            import signal

            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class JobRunner:
    def __init__(
        self,
        *,
        max_processing_jobs: int = 1,
        max_fast_preview_jobs: int = 1,
        max_full_jobs: int = 1,
    ) -> None:
        self.max_processing_jobs = max_processing_jobs
        self.max_fast_preview_jobs = max_fast_preview_jobs
        self.max_full_jobs = max_full_jobs
        self._jobs: dict[str, _RunningJob] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return [entry.job for entry in self._jobs.values()]

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            entry = self._jobs.get(job_id)
            return entry.job if entry else None

    def _count_running_locked(self, kinds: set[str]) -> int:
        return sum(
            1
            for entry in self._jobs.values()
            if entry.job.kind in kinds and entry.job.status == "running"
        )

    def _processing_running_locked(self) -> int:
        return self._count_running_locked(PROCESSING_KINDS)

    def running_processing_count(self) -> int:
        with self._lock:
            return self._processing_running_locked()

    def running_fast_preview_count(self) -> int:
        with self._lock:
            return self._count_running_locked(FAST_PREVIEW_KINDS)

    def running_full_count(self) -> int:
        with self._lock:
            return self._count_running_locked(FULL_KINDS)

    def lane_is_busy(self, kind: JobKind) -> bool:
        with self._lock:
            if kind in FAST_PREVIEW_KINDS:
                return self._count_running_locked(FAST_PREVIEW_KINDS) >= self.max_fast_preview_jobs
            if kind in FULL_KINDS:
                return self._count_running_locked(FULL_KINDS) >= self.max_full_jobs
            return False

    def register_job(
        self,
        kind: JobKind,
        *,
        session_folder: Path | None = None,
        abort_hook: Callable[[], None] | None = None,
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            session_folder=session_folder,
            status="running",
        )
        with self._lock:
            self._jobs[job.id] = _RunningJob(job=job, abort_hook=abort_hook)
        return job

    def start_script(
        self,
        kind: JobKind,
        script: Path,
        args: list[str],
        *,
        cwd: Path,
        session_folder: Path | None = None,
        on_complete: Callable[[Job], None] | None = None,
    ) -> Job:
        if kind in FAST_PREVIEW_KINDS:
            with self._lock:
                if self._count_running_locked(FAST_PREVIEW_KINDS) >= self.max_fast_preview_jobs:
                    raise RuntimeError(
                        "A Fast Preview job is already running "
                        f"(max {self.max_fast_preview_jobs})."
                    )
        elif kind in FULL_KINDS:
            with self._lock:
                if self._count_running_locked(FULL_KINDS) >= self.max_full_jobs:
                    raise RuntimeError(
                        "A full prep or render job is already running "
                        f"(max {self.max_full_jobs})."
                    )

        argv = [sys.executable, str(script), *args]
        popen_kwargs: dict = {
            "cwd": str(cwd),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(argv, **popen_kwargs)
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            session_folder=session_folder,
            status="running",
            pid=process.pid,
        )
        entry = _RunningJob(job=job, process=process, on_complete=on_complete)
        with self._lock:
            self._jobs[job.id] = entry
        self._spawn_waiter(entry)
        return job

    def _spawn_waiter(self, entry: _RunningJob) -> None:
        def _wait() -> None:
            proc = entry.process
            if proc is None:
                return
            try:
                proc.wait()
            except Exception:
                pass
            self._finalize_subprocess(entry, proc.returncode)

        thread = threading.Thread(target=_wait, daemon=True)
        entry._wait_thread = thread
        thread.start()

    def _apply_returncode_locked(self, entry: _RunningJob, returncode: int | None) -> None:
        if entry.job.status != "running":
            return
        if returncode == 0:
            entry.job.status = "completed"
        else:
            entry.job.status = "failed"
            entry.job.message = f"Process exited with code {returncode}"

    def _finalize_subprocess(self, entry: _RunningJob, returncode: int | None) -> None:
        with self._lock:
            self._apply_returncode_locked(entry, returncode)
        if entry.on_complete:
            entry.on_complete(entry.job)

    def poll(self) -> list[Job]:
        finished: list[Job] = []
        callbacks: list[tuple[Callable[[Job], None], Job]] = []
        with self._lock:
            for entry in self._jobs.values():
                proc = entry.process
                if entry.job.status == "running" and proc is not None:
                    code = proc.poll()
                    if code is not None:
                        self._apply_returncode_locked(entry, code)
                        if entry.on_complete:
                            callbacks.append((entry.on_complete, entry.job))
                if (
                    entry.job.status in {"completed", "failed", "aborted"}
                    and not entry.reported
                ):
                    entry.reported = True
                    finished.append(entry.job)
        for callback, job in callbacks:
            callback(job)
        return finished

    def abort_job(self, job_id: str, *, confirmed: bool) -> AbortResult:
        if not confirmed:
            return AbortResult(job_id, "failed", "Abort not confirmed.")

        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None:
                return AbortResult(job_id, "failed", f"Unknown job: {job_id}")
            if entry.job.status != "running":
                return AbortResult(
                    job_id,
                    entry.job.status,
                    "Job is not running.",
                )
            entry.job.status = "aborted"
            entry.job.message = "Aborted by user."

        if entry.abort_hook is not None:
            try:
                entry.abort_hook()
            except Exception as exc:
                with self._lock:
                    entry.job.status = "failed"
                    entry.job.message = str(exc)
                return AbortResult(job_id, "failed", str(exc))

        proc = entry.process
        pid = entry.job.pid or (proc.pid if proc is not None else None)
        if proc is not None and proc.poll() is None:
            kill_process_tree(pid)
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
                kill_process_tree(pid)

        return AbortResult(job_id, "aborted", entry.job.message)
