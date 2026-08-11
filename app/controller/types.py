"""Shared controller datatypes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BlockKind = Literal["recording", "autocut", "delivery"]
CheckStatus = Literal["ok", "warn", "fail"]
JobKind = Literal["recording", "prep", "render"]
JobStatus = Literal["running", "completed", "failed", "aborted"]


@dataclass(frozen=True)
class PreflightCheck:
    id: str
    status: CheckStatus
    message: str
    blocks: tuple[BlockKind, ...] = ()


@dataclass
class PreflightReport:
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def ok_for_recording(self) -> bool:
        return not any(c.status == "fail" and "recording" in c.blocks for c in self.checks)

    @property
    def ok_for_autocut(self) -> bool:
        return not any(c.status == "fail" and "autocut" in c.blocks for c in self.checks)

    @property
    def ok_for_delivery(self) -> bool:
        return not any(c.status == "fail" and "delivery" in c.blocks for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "checks": [
                {
                    "id": c.id,
                    "status": c.status,
                    "message": c.message,
                    "blocks": list(c.blocks),
                }
                for c in self.checks
            ],
            "ok_for_recording": self.ok_for_recording,
            "ok_for_autocut": self.ok_for_autocut,
            "ok_for_delivery": self.ok_for_delivery,
        }


@dataclass
class Job:
    id: str
    kind: JobKind
    session_folder: Path | None = None
    status: JobStatus = "running"
    message: str = ""
    pid: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "session_folder": str(self.session_folder) if self.session_folder else None,
            "status": self.status,
            "message": self.message,
            "pid": self.pid,
        }


@dataclass
class AbortResult:
    job_id: str
    status: JobStatus
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "message": self.message,
        }
