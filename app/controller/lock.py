"""App singleton lock and recording lock."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.controller.paths import APP_LOCK_PATH


@dataclass
class LockState:
    pid: int
    started_at: str
    recording_active: bool = False
    processing_job_ids: list[str] | None = None

    def __post_init__(self) -> None:
        if self.processing_job_ids is None:
            self.processing_job_ids = []

    @classmethod
    def fresh(cls) -> LockState:
        return cls(
            pid=os.getpid(),
            started_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LockState:
        return cls(
            pid=int(data["pid"]),
            started_at=str(data["started_at"]),
            recording_active=bool(data.get("recording_active", False)),
            processing_job_ids=list(data.get("processing_job_ids") or []),
        )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import subprocess

        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout = (proc.stdout or "").lower()
        return str(pid) in stdout and "no tasks are running" not in stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class AppLock:
    def __init__(self, path: Path = APP_LOCK_PATH) -> None:
        self.path = path
        self._held = False

    def read(self) -> LockState | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            state = LockState.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if state.pid != os.getpid() and not _pid_alive(state.pid):
            return None
        return state

    def acquire(self, *, force: bool = False) -> tuple[bool, str]:
        existing = self.read()
        if existing is not None and existing.pid != os.getpid():
            if not force:
                return False, f"PIAB app already running (pid {existing.pid})."
            try:
                self.path.unlink()
            except OSError:
                pass
            existing = None

        if self.path.is_file() and existing is None:
            # Dead PID or corrupt lock left on disk — replace silently.
            try:
                self.path.unlink()
            except OSError:
                pass

        state = LockState.fresh()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        self._held = True
        return True, ""

    def release(self) -> None:
        if not self._held:
            return
        existing = self.read()
        if existing is not None and existing.pid == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
        self._held = False

    def update(self, **kwargs: object) -> LockState:
        state = self.read()
        if state is None or state.pid != os.getpid():
            state = LockState.fresh()
        for key, value in kwargs.items():
            setattr(state, key, value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        self._held = True
        return state

    def set_recording_active(self, active: bool) -> None:
        self.update(recording_active=active)

    def is_recording_active(self) -> bool:
        state = self.read()
        return bool(state and state.recording_active)

    def add_processing_job(self, job_id: str) -> None:
        state = self.read() or LockState.fresh()
        ids = list(state.processing_job_ids or [])
        if job_id not in ids:
            ids.append(job_id)
        self.update(processing_job_ids=ids)

    def remove_processing_job(self, job_id: str) -> None:
        state = self.read()
        if state is None:
            return
        ids = [item for item in (state.processing_job_ids or []) if item != job_id]
        self.update(processing_job_ids=ids)
