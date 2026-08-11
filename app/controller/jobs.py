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
    _wait_thread: threading.Thread | None = field(default=None, repr=False)


class JobRunner:
    def __init__(self, *, max_processing_jobs: int = 1) -> None:
        self.max_processing_jobs = max_processing_jobs
        self._jobs: dict[str, _RunningJob] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return [entry.job for entry in self._jobs.values()]

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            entry = self._jobs.get(job_id)
            return entry.job if entry else None

    def _processing_running_locked(self) -> int:
        return sum(
            1
            for entry in self._jobs.values()
            if entry.job.kind in {"prep", "render"} and entry.job.status == "running"
        )

    def running_processing_count(self) -> int:
        with self._lock:
            return self._processing_running_locked()

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
        if kind in {"prep", "render"}:
            with self._lock:
                if self._processing_running_locked() >= self.max_processing_jobs:
                    raise RuntimeError(
                        "A prep or render job is already running "
                        f"(max {self.max_processing_jobs})."
                    )

        argv = [sys.executable, str(script), *args]
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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

    def _finalize_subprocess(self, entry: _RunningJob, returncode: int | None) -> None:
        with self._lock:
            if entry.job.status != "running":
                return
            if returncode == 0:
                entry.job.status = "completed"
            else:
                entry.job.status = "failed"
                entry.job.message = f"Process exited with code {returncode}"
        if entry.on_complete:
            entry.on_complete(entry.job)

    def poll(self) -> list[Job]:
        finished: list[Job] = []
        with self._lock:
            for entry in self._jobs.values():
                proc = entry.process
                if proc is None or entry.job.status != "running":
                    continue
                code = proc.poll()
                if code is not None:
                    self._finalize_subprocess(entry, code)
                if entry.job.status in {"completed", "failed", "aborted"}:
                    finished.append(entry.job)
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
        if proc is not None and proc.poll() is None:
            proc.terminate()
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()

        return AbortResult(job_id, "aborted", entry.job.message)
