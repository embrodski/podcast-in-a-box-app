"""Clean old PIAB working subfolders (Raw/Input/Temp/Preview Files), keep Output."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from piab_process_log import (
    DEFAULT_PROCESS_LOG_PATH,
    DEFAULT_PROCESS_LOG_ROOT,
    _acquire_lock,
    _atomic_write_json,
    _release_lock,
    _utc_now_iso,
    list_project_subfolders,
    load_process_log,
    upsert_process_log_entry,
)

# Subfolders removed by Clean Old Working Files. Output is always kept.
CLEANABLE_SUBFOLDERS: tuple[str, ...] = (
    "Raw",
    "Input",
    "Preview Files",
    "Temp",
)

_KEEP_SUBFOLDER = "Output"
SKIP_SCAN_DIR_NAMES = frozenset(
    {
        "cursor",
        "logs",
        "recordings",
        "vmix configs",
    }
)


@dataclass
class CleanCandidate:
    project_folder: Path
    name: str
    email: str | None
    begun_at: str | None
    removable_subfolders: list[str] = field(default_factory=list)
    all_subfolders: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_folder": str(self.project_folder),
            "name": self.name,
            "email": self.email,
            "begun_at": self.begun_at,
            "removable_subfolders": list(self.removable_subfolders),
            "all_subfolders": list(self.all_subfolders),
        }


@dataclass
class CleanProjectResult:
    project_folder: Path
    deleted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log_updated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_folder": str(self.project_folder),
            "deleted": list(self.deleted),
            "missing": list(self.missing),
            "errors": list(self.errors),
            "log_updated": self.log_updated,
        }


def removable_working_subfolders(project_folder: Path) -> list[str]:
    """Return cleanable subfolder names that currently exist on disk."""
    folder = project_folder.resolve()
    found: list[str] = []
    for name in CLEANABLE_SUBFOLDERS:
        path = folder / name
        if path.is_dir():
            found.append(name)
    return found


def has_non_output_subfolders(project_folder: Path) -> bool:
    """True when any immediate child directory exists other than Output."""
    folder = project_folder.resolve()
    if not folder.is_dir():
        return False
    try:
        for child in folder.iterdir():
            if not child.is_dir():
                continue
            if child.name.casefold() != _KEEP_SUBFOLDER.casefold():
                return True
    except OSError:
        return False
    return False


def list_clean_candidates(
    *,
    log_path: Path | None = None,
) -> list[CleanCandidate]:
    """
    Projects from the process log that still have subfolders other than Output.

    Disk is the source of truth for what remains; the log supplies the project list.
    """
    log = load_process_log(log_path or DEFAULT_PROCESS_LOG_PATH)
    processes = log.get("processes")
    if not isinstance(processes, list):
        return []

    candidates: list[CleanCandidate] = []
    seen: set[Path] = set()
    for row in processes:
        if not isinstance(row, dict):
            continue
        raw = row.get("project_folder") or row.get("id")
        if not isinstance(raw, str) or not raw.strip():
            continue
        folder = Path(raw).resolve()
        if folder in seen:
            continue
        seen.add(folder)
        if not folder.is_dir():
            continue
        if not has_non_output_subfolders(folder):
            continue
        removable = removable_working_subfolders(folder)
        # Still show if other non-Output dirs exist (e.g. leftover files), but
        # Clean only removes the known working subfolders.
        if not removable and not has_non_output_subfolders(folder):
            continue
        if not removable:
            continue
        email = row.get("email")
        begun = row.get("begun_at")
        name = row.get("name")
        candidates.append(
            CleanCandidate(
                project_folder=folder,
                name=str(name) if name else folder.name,
                email=str(email).strip() if isinstance(email, str) and email.strip() else None,
                begun_at=str(begun) if isinstance(begun, str) else None,
                removable_subfolders=removable,
                all_subfolders=list_project_subfolders(folder),
            )
        )

    candidates.sort(key=lambda c: (c.begun_at or "", str(c.project_folder)), reverse=True)
    return candidates


def _logged_project_folders(log_path: Path) -> set[Path]:
    log = load_process_log(log_path)
    found: set[Path] = set()
    for row in log.get("processes") or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("project_folder") or row.get("id")
        if not isinstance(raw, str) or not raw.strip():
            continue
        found.add(Path(raw).resolve())
    return found


def _state_from_session_folder(folder: Path) -> dict[str, Any]:
    path = folder / "podcast-in-a-box.json"
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                return state
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return {"name": folder.name, "kind": "podcast_in_a_box"}


def candidate_from_disk_folder(folder: Path) -> CleanCandidate | None:
    """Build a clean candidate from a session folder on disk, or None."""
    project = folder.resolve()
    removable = removable_working_subfolders(project)
    if not removable:
        return None
    state = _state_from_session_folder(project)
    email = state.get("delivery") if isinstance(state.get("delivery"), dict) else {}
    raw_email = email.get("email") if isinstance(email, dict) else None
    begun = state.get("created_at")
    return CleanCandidate(
        project_folder=project,
        name=str(state.get("name") or project.name),
        email=str(raw_email).strip() if isinstance(raw_email, str) and raw_email.strip() else None,
        begun_at=str(begun) if isinstance(begun, str) else None,
        removable_subfolders=removable,
        all_subfolders=list_project_subfolders(project),
    )


def scan_lost_clean_candidates(
    *,
    root: Path | None = None,
    log_path: Path | None = None,
    record_in_log: bool = True,
) -> list[CleanCandidate]:
    """
    Scan the PIAB work root for session folders with working files not in the log.

    Does not run unless called. Found sessions are added to the process log so
    they appear on the normal Clean list afterward.
    """
    scan_root = (root or DEFAULT_PROCESS_LOG_ROOT).resolve()
    path = log_path or DEFAULT_PROCESS_LOG_PATH
    logged = _logged_project_folders(path)
    found: list[CleanCandidate] = []
    try:
        children = list(scan_root.iterdir())
    except OSError:
        return []

    for child in children:
        if not child.is_dir():
            continue
        if child.name.casefold() in SKIP_SCAN_DIR_NAMES:
            continue
        folder = child.resolve()
        if folder in logged:
            continue
        candidate = candidate_from_disk_folder(folder)
        if candidate is None:
            continue
        if record_in_log:
            upsert_process_log_entry(
                folder,
                _state_from_session_folder(folder),
                log_path=path,
            )
        found.append(candidate)

    found.sort(key=lambda c: (c.begun_at or "", str(c.project_folder)), reverse=True)
    return found


def send_directory_to_recycle_bin(path: Path) -> None:
    """Move a directory to the Windows Recycle Bin (FOF_ALLOWUNDO)."""
    target = path.resolve()
    if not target.exists():
        return
    if sys.platform != "win32":
        # Non-Windows fallback for tests/dev: permanent delete.
        import shutil

        shutil.rmtree(target)
        return

    from ctypes import Structure, byref, c_void_p, windll
    from ctypes.wintypes import BOOL, HWND, LPCWSTR, UINT, WORD

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(Structure):
        _fields_ = [
            ("hwnd", HWND),
            ("wFunc", UINT),
            ("pFrom", LPCWSTR),
            ("pTo", LPCWSTR),
            ("fFlags", WORD),
            ("fAnyOperationsAborted", BOOL),
            ("hNameMappings", c_void_p),
            ("lpszProgressTitle", LPCWSTR),
        ]

    # Double-null-terminated path list required by SHFileOperationW.
    from_buf = str(target) + "\0\0"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = from_buf
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None

    result = windll.shell32.SHFileOperationW(byref(op))
    if result != 0 or op.fAnyOperationsAborted:
        raise OSError(
            f"Failed to send to Recycle Bin (code {result}): {target}"
        )
    if target.exists():
        raise OSError(f"Path still exists after Recycle Bin move: {target}")


def mark_working_files_cleaned(
    project_folder: Path,
    *,
    deleted: list[str],
    log_path: Path | None = None,
) -> bool:
    """Update the process-log row after working subfolders are removed."""
    path = (log_path or DEFAULT_PROCESS_LOG_PATH).resolve()
    lock_path = path.with_suffix(path.suffix + ".lock")
    fd = _acquire_lock(lock_path)
    try:
        log = load_process_log(path)
        processes = log.get("processes")
        if not isinstance(processes, list):
            return False
        folder_key = str(project_folder.resolve())
        updated = False
        for i, row in enumerate(processes):
            if not isinstance(row, dict):
                continue
            if str(row.get("id") or row.get("project_folder")) != folder_key:
                continue
            row = dict(row)
            row["subfolders"] = list_project_subfolders(project_folder)
            row["working_files_cleaned_at"] = _utc_now_iso()
            row["cleaned_subfolders"] = list(deleted)
            row["updated_at"] = _utc_now_iso()
            processes[i] = row
            updated = True
            break
        if not updated:
            return False
        log["processes"] = processes
        log["updated_at"] = _utc_now_iso()
        _atomic_write_json(path, log)
        return True
    finally:
        _release_lock(lock_path, fd)


def clean_project_working_files(
    project_folder: Path,
    *,
    log_path: Path | None = None,
    recycle: Callable[[Path], None] | None = None,
) -> CleanProjectResult:
    """Delete cleanable subfolders for one project and update the process log."""
    folder = project_folder.resolve()
    result = CleanProjectResult(project_folder=folder)
    mover = recycle or send_directory_to_recycle_bin

    for name in CLEANABLE_SUBFOLDERS:
        target = folder / name
        if not target.is_dir():
            result.missing.append(name)
            continue
        try:
            mover(target)
            result.deleted.append(name)
        except Exception as exc:
            result.errors.append(f"{name}: {exc}")

    if result.deleted:
        try:
            result.log_updated = mark_working_files_cleaned(
                folder,
                deleted=result.deleted,
                log_path=log_path,
            )
        except Exception as exc:
            result.errors.append(f"log update: {exc}")
        try:
            mark_session_state_cleaned(folder, deleted=result.deleted)
        except Exception as exc:
            result.errors.append(f"state update: {exc}")

    return result


def mark_session_state_cleaned(project_folder: Path, *, deleted: list[str]) -> None:
    """Stamp podcast-in-a-box.json so Resume will not offer this folder."""
    path = project_folder.resolve() / "podcast-in-a-box.json"
    if not path.is_file():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return
    if not isinstance(state, dict) or state.get("kind") != "podcast_in_a_box":
        return
    state["working_files_cleaned_at"] = _utc_now_iso()
    state["cleaned_subfolders"] = list(deleted)
    state["resume_at"] = "cleaned"
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def clean_selected_projects(
    project_folders: list[Path],
    *,
    log_path: Path | None = None,
    recycle: Callable[[Path], None] | None = None,
) -> list[CleanProjectResult]:
    return [
        clean_project_working_files(path, log_path=log_path, recycle=recycle)
        for path in project_folders
    ]
