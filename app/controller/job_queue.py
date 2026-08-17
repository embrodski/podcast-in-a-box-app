"""Persistent Fast Preview / Full job queues under the scan root."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.controller.paths import JOB_QUEUE_PATH

Lane = Literal["fast_preview", "full"]
QueueStatus = Literal[
    "queued", "running", "interrupted", "failed", "cancelled", "completed", "held"
]
VALID_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "interrupted", "failed", "cancelled", "completed", "held"}
)
LANES: tuple[Lane, ...] = ("fast_preview", "full")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _folder_key(folder: Path) -> str:
    return str(folder.resolve())


@dataclass
class QueueEntry:
    folder: str
    lane: Lane
    enqueued_at: str
    status: QueueStatus
    name: str = ""
    interrupted_at: str | None = None
    held_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "folder": self.folder,
            "lane": self.lane,
            "enqueued_at": self.enqueued_at,
            "status": self.status,
            "name": self.name,
        }
        if self.interrupted_at:
            payload["interrupted_at"] = self.interrupted_at
        if self.held_at:
            payload["held_at"] = self.held_at
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueEntry:
        raw_status = data.get("status")
        status: QueueStatus = raw_status if raw_status in VALID_STATUSES else "queued"
        return cls(
            folder=str(data.get("folder") or ""),
            lane=data.get("lane") if data.get("lane") in LANES else "full",
            enqueued_at=str(data.get("enqueued_at") or ""),
            status=status,
            name=str(data.get("name") or ""),
            interrupted_at=str(data["interrupted_at"]) if data.get("interrupted_at") else None,
            held_at=str(data["held_at"]) if data.get("held_at") else None,
        )

    @property
    def folder_path(self) -> Path:
        return Path(self.folder)


class JobQueueStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or JOB_QUEUE_PATH

    def _empty(self) -> dict[str, list[dict[str, Any]]]:
        return {"fast_preview": [], "full": []}

    def load(self) -> dict[str, list[QueueEntry]]:
        if not self.path.is_file():
            return {"fast_preview": [], "full": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"fast_preview": [], "full": []}
        result: dict[str, list[QueueEntry]] = {"fast_preview": [], "full": []}
        for lane in LANES:
            rows = raw.get(lane) if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                continue
            for item in rows:
                if isinstance(item, dict) and item.get("folder"):
                    result[lane].append(QueueEntry.from_dict({**item, "lane": lane}))
        return result

    def save(self, data: dict[str, list[QueueEntry]]) -> None:
        payload = {
            lane: [entry.to_dict() for entry in data.get(lane, [])]
            for lane in LANES
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=".piab-job-queue-",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _mutate(self, fn) -> dict[str, list[QueueEntry]]:
        data = self.load()
        fn(data)
        self.save(data)
        return data

    def enqueue(
        self,
        folder: Path,
        lane: Lane,
        *,
        name: str = "",
    ) -> QueueEntry:
        key = _folder_key(folder)
        created: list[QueueEntry] = []

        def _add(data: dict[str, list[QueueEntry]]) -> None:
            rows = data[lane]
            for entry in rows:
                if _folder_key(Path(entry.folder)) != key:
                    continue
                if entry.status == "failed":
                    entry.status = "queued"
                    entry.held_at = None
                    created.append(entry)
                    return
                if entry.status == "held":
                    entry.status = "queued"
                    entry.held_at = None
                    entry.enqueued_at = _utc_now_iso()
                    created.append(entry)
                    return
                if entry.status in {"queued", "running", "interrupted"}:
                    created.append(entry)
                    return
            entry = QueueEntry(
                folder=key,
                lane=lane,
                enqueued_at=_utc_now_iso(),
                status="queued",
                name=name or Path(folder).name,
            )
            rows.append(entry)
            created.append(entry)

        self._mutate(_add)
        return created[0]

    def requeue_stale_running(self, lane: Lane) -> list[QueueEntry]:
        """Turn orphaned ``running`` rows back into ``queued`` so they can start."""
        found: list[QueueEntry] = []

        def _requeue(data: dict[str, list[QueueEntry]]) -> None:
            for entry in data[lane]:
                if entry.status == "running":
                    entry.status = "queued"
                    entry.interrupted_at = None
                    found.append(entry)

        self._mutate(_requeue)
        return found

    def mark_running(self, folder: Path, lane: Lane) -> QueueEntry | None:
        key = _folder_key(folder)
        found: list[QueueEntry] = []

        def _run(data: dict[str, list[QueueEntry]]) -> None:
            for entry in data[lane]:
                if _folder_key(Path(entry.folder)) == key:
                    entry.status = "running"
                    entry.interrupted_at = None
                    found.append(entry)
                    return

        self._mutate(_run)
        return found[0] if found else None

    def mark_failed(self, folder: Path, lane: Lane) -> QueueEntry | None:
        key = _folder_key(folder)
        found: list[QueueEntry] = []

        def _fail(data: dict[str, list[QueueEntry]]) -> None:
            for entry in data[lane]:
                if _folder_key(Path(entry.folder)) == key:
                    entry.status = "failed"
                    found.append(entry)
                    return

        self._mutate(_fail)
        return found[0] if found else None

    def mark_interrupted(self, folder: Path, lane: Lane) -> QueueEntry | None:
        key = _folder_key(folder)
        found: list[QueueEntry] = []

        def _irq(data: dict[str, list[QueueEntry]]) -> None:
            for entry in data[lane]:
                if _folder_key(Path(entry.folder)) == key and entry.status == "running":
                    entry.status = "interrupted"
                    entry.interrupted_at = _utc_now_iso()
                    found.append(entry)

        self._mutate(_irq)
        return found[0] if found else None

    def interrupt_running(self) -> list[QueueEntry]:
        found: list[QueueEntry] = []

        def _all(data: dict[str, list[QueueEntry]]) -> None:
            stamp = _utc_now_iso()
            for lane in LANES:
                for entry in data[lane]:
                    if entry.status == "running":
                        entry.status = "interrupted"
                        entry.interrupted_at = stamp
                        found.append(entry)

        self._mutate(_all)
        return found

    def complete(self, folder: Path, lane: Lane) -> None:
        key = _folder_key(folder)

        def _done(data: dict[str, list[QueueEntry]]) -> None:
            data[lane] = [
                entry
                for entry in data[lane]
                if _folder_key(Path(entry.folder)) != key
            ]

        self._mutate(_done)

    def hold(self, folder: Path, lane: Lane) -> QueueEntry | None:
        """Move a waiting job off the auto-process queue without cancelling it."""
        key = _folder_key(folder)
        found: list[QueueEntry] = []

        def _hold(data: dict[str, list[QueueEntry]]) -> None:
            for entry in data[lane]:
                if _folder_key(Path(entry.folder)) != key:
                    continue
                if entry.status != "queued":
                    return
                entry.status = "held"
                entry.held_at = _utc_now_iso()
                found.append(entry)
                return

        self._mutate(_hold)
        return found[0] if found else None

    def held(self) -> list[QueueEntry]:
        found: list[QueueEntry] = []
        data = self.load()
        for lane in LANES:
            for entry in data[lane]:
                if entry.status == "held":
                    found.append(entry)
        found.sort(key=lambda e: e.held_at or e.enqueued_at)
        return found

    def cancel(self, folder: Path, lane: Lane) -> bool:
        key = _folder_key(folder)
        removed = []

        def _cancel(data: dict[str, list[QueueEntry]]) -> None:
            before = len(data[lane])
            data[lane] = [
                entry
                for entry in data[lane]
                if _folder_key(Path(entry.folder)) != key
            ]
            if len(data[lane]) < before:
                removed.append(True)

        self._mutate(_cancel)
        return bool(removed)

    def next_queued(self, lane: Lane) -> QueueEntry | None:
        for entry in self.load()[lane]:
            if entry.status == "queued":
                return entry
        return None

    def running(self, lane: Lane) -> QueueEntry | None:
        for entry in self.load()[lane]:
            if entry.status == "running":
                return entry
        return None

    def interrupted(self) -> list[QueueEntry]:
        found: list[QueueEntry] = []
        data = self.load()
        for lane in ("full", "fast_preview"):
            for entry in data[lane]:
                if entry.status == "interrupted":
                    found.append(entry)
        return found

    def full_current_and_waiting(self) -> tuple[QueueEntry | None, list[QueueEntry]]:
        rows = self.load()["full"]
        current = next((e for e in rows if e.status in {"running", "failed"}), None)
        waiting = [e for e in rows if e.status in {"queued", "interrupted"}]
        waiting.sort(key=lambda e: e.enqueued_at)
        return current, waiting

    def entry_for(self, folder: Path, lane: Lane) -> QueueEntry | None:
        key = _folder_key(folder)
        for entry in self.load()[lane]:
            if _folder_key(Path(entry.folder)) == key:
                return entry
        return None

    def protected_folders(self) -> set[Path]:
        folders: set[Path] = set()
        data = self.load()
        for lane in LANES:
            for entry in data[lane]:
                if entry.status in {"queued", "running", "interrupted", "failed", "held"}:
                    folders.add(Path(entry.folder).resolve())
        return folders

    def has_active_work(self) -> bool:
        data = self.load()
        for lane in LANES:
            for entry in data[lane]:
                if entry.status in {"queued", "running", "interrupted", "failed"}:
                    return True
        return False

    def has_running_or_queued(self) -> bool:
        """True when an autocut is in progress or waiting — not failed/interrupted leftovers."""
        data = self.load()
        for lane in LANES:
            for entry in data[lane]:
                if entry.status in {"queued", "running"}:
                    return True
        return False
