"""App-wide PIAB process log: one entry per autocut session under Sessions."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

# Keep next to the PIAB work root / .piab-app.lock so operators can find it easily.
PROCESS_LOG_FILENAME = "piab-process-log.json"
DEFAULT_PROCESS_LOG_ROOT = Path(r"E:\PodcastRoom\PodcastInABox\Sessions")
DEFAULT_PROCESS_LOG_PATH = DEFAULT_PROCESS_LOG_ROOT / PROCESS_LOG_FILENAME
_LEGACY_PROCESS_LOG_PATHS = (
    Path(r"E:\PodcastRoom") / PROCESS_LOG_FILENAME,
    Path(r"E:\PodcastRoom\PodcastInABox") / PROCESS_LOG_FILENAME,
)

_DONE_STATUSES = frozenset({"completed", "skipped"})


def process_log_path(root: Path | None = None) -> Path:
    if root is None:
        return DEFAULT_PROCESS_LOG_PATH
    return Path(root).resolve() / PROCESS_LOG_FILENAME


def _utc_now_iso() -> str:
    from harness_episode_lib import utc_now_iso

    return utc_now_iso()


def list_project_subfolders(project_folder: Path) -> list[str]:
    """Immediate child directory names under the session folder (sorted)."""
    folder = project_folder.resolve()
    if not folder.is_dir():
        return []
    names: list[str] = []
    try:
        for child in folder.iterdir():
            if child.is_dir():
                names.append(child.name)
    except OSError:
        return names
    return sorted(names, key=str.lower)


def _steps_completed(state: dict) -> list[dict[str, Any]]:
    steps = state.get("steps")
    if not isinstance(steps, dict):
        return []
    completed: list[dict[str, Any]] = []
    for step_id, entry in steps.items():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status not in _DONE_STATUSES:
            continue
        item: dict[str, Any] = {
            "id": str(entry.get("id") or step_id),
            "title": str(entry.get("title") or step_id),
            "status": str(status),
        }
        completed_at = entry.get("completed_at")
        if isinstance(completed_at, str) and completed_at.strip():
            item["completed_at"] = completed_at.strip()
        if status == "skipped" or entry.get("skipped"):
            item["skipped"] = True
        completed.append(item)

    def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
        return (str(row.get("completed_at") or ""), str(row.get("id") or ""))

    completed.sort(key=_sort_key)
    return completed


def _delivery_block(state: dict) -> dict[str, Any]:
    delivery = state.get("delivery")
    return delivery if isinstance(delivery, dict) else {}


def _email_from_state(state: dict) -> str | None:
    delivery = _delivery_block(state)
    email = delivery.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _final_video_info(state: dict) -> dict[str, Any]:
    path = state.get("full_interview_mp4")
    path_str = str(path).strip() if path else ""
    render = (state.get("steps") or {}).get("13_full_render")
    render_done = isinstance(render, dict) and render.get("status") == "completed"
    completed_at = None
    if isinstance(render, dict):
        raw = render.get("completed_at")
        if isinstance(raw, str) and raw.strip():
            completed_at = raw.strip()
    if state.get("resume_at") == "14_done":
        render_done = True
    exists = bool(path_str) and Path(path_str).is_file()
    return {
        "completed": bool(render_done or exists),
        "path": path_str or None,
        "completed_at": completed_at,
    }


def _frameio_info(state: dict) -> dict[str, Any]:
    frameio = _delivery_block(state).get("frameio")
    if not isinstance(frameio, dict):
        frameio = {}
    status = str(frameio.get("status") or "not_started")
    completed_at = frameio.get("completed_at")
    return {
        "uploaded": status == "completed",
        "status": status,
        "completed_at": completed_at if isinstance(completed_at, str) else None,
        "short_url": frameio.get("short_url") or None,
    }


def _email_delivery_info(state: dict) -> dict[str, Any]:
    delivery = _delivery_block(state)
    mail = delivery.get("email_delivery")
    if not isinstance(mail, dict):
        mail = {}
    status = str(mail.get("status") or "not_started")
    sent_at = mail.get("sent_at")
    return {
        "sent": status == "sent",
        "status": status,
        "sent_at": sent_at if isinstance(sent_at, str) else None,
        "recipient": _email_from_state(state),
        "error": mail.get("error") or None,
    }


def build_process_entry(
    working_folder: Path,
    state: dict,
    *,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    folder = working_folder.resolve()
    prior = prior or {}
    begun_at = prior.get("begun_at")
    if not isinstance(begun_at, str) or not begun_at.strip():
        created = state.get("created_at")
        begun_at = created if isinstance(created, str) and created.strip() else _utc_now_iso()

    entry: dict[str, Any] = {
        "id": str(folder),
        "begun_at": begun_at,
        "updated_at": _utc_now_iso(),
        "name": state.get("name") or folder.name,
        "email": _email_from_state(state),
        "project_folder": str(folder),
        "subfolders": list_project_subfolders(folder),
        "session_mode": state.get("session_mode"),
        "resume_at": state.get("resume_at"),
        "steps_completed": _steps_completed(state),
        "final_video": _final_video_info(state),
        "frameio": _frameio_info(state),
        "email_delivery": _email_delivery_info(state),
    }
    # Preserve clean-history fields written by Clean Old Working Files.
    cleaned_at = prior.get("working_files_cleaned_at")
    if isinstance(cleaned_at, str) and cleaned_at.strip():
        entry["working_files_cleaned_at"] = cleaned_at.strip()
    cleaned_subs = prior.get("cleaned_subfolders")
    if isinstance(cleaned_subs, list) and cleaned_subs:
        entry["cleaned_subfolders"] = [str(x) for x in cleaned_subs]
    return entry


def _empty_log() -> dict[str, Any]:
    return {
        "kind": "piab_process_log",
        "updated_at": _utc_now_iso(),
        "processes": [],
    }


def find_process_log_entry(
    working_folder: Path,
    *,
    log_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return this session's process-log row, or None if missing."""
    folder_key = str(working_folder.resolve())
    log = load_process_log(log_path)
    for row in log.get("processes") or []:
        if isinstance(row, dict) and str(row.get("id") or row.get("project_folder")) == folder_key:
            return row
    return None


def _migrate_legacy_process_log(log_path: Path) -> None:
    dest = log_path.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        return
    for src in _LEGACY_PROCESS_LOG_PATHS:
        try:
            src_resolved = src.resolve()
        except OSError:
            continue
        if not src.is_file() or src_resolved == dest:
            continue
        try:
            src.replace(dest)
            return
        except OSError:
            continue


def load_process_log(path: Path | None = None) -> dict[str, Any]:
    log_path = path or DEFAULT_PROCESS_LOG_PATH
    if path is None:
        _migrate_legacy_process_log(log_path)
    if not log_path.is_file():
        return _empty_log()
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_log()
    if not isinstance(data, dict):
        return _empty_log()
    processes = data.get("processes")
    if not isinstance(processes, list):
        data["processes"] = []
    data.setdefault("kind", "piab_process_log")
    return data


def _acquire_lock(lock_path: Path, *, timeout_sec: float = 5.0) -> int | None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            time.sleep(0.05)
        except OSError:
            return None
    return None


def _release_lock(lock_path: Path, fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def upsert_process_log_entry(
    working_folder: Path,
    state: dict,
    *,
    log_path: Path | None = None,
) -> Path | None:
    """
    Insert or update this session's row in the app-wide process log.

    Never raises — logging must not break the autocut pipeline.
    """
    try:
        path = (log_path or DEFAULT_PROCESS_LOG_PATH).resolve()
        lock_path = path.with_suffix(path.suffix + ".lock")
        fd = _acquire_lock(lock_path)
        try:
            log = load_process_log(path)
            processes = log.get("processes")
            if not isinstance(processes, list):
                processes = []
            folder_key = str(working_folder.resolve())
            prior = None
            index = None
            for i, row in enumerate(processes):
                if isinstance(row, dict) and str(row.get("id") or row.get("project_folder")) == folder_key:
                    prior = row
                    index = i
                    break
            entry = build_process_entry(working_folder, state, prior=prior)
            if index is None:
                processes.append(entry)
            else:
                processes[index] = entry
            log["kind"] = "piab_process_log"
            log["updated_at"] = _utc_now_iso()
            log["processes"] = processes
            _atomic_write_json(path, log)
            return path
        finally:
            _release_lock(lock_path, fd)
    except Exception:
        return None


def _is_ephemeral_working_folder(working_folder: Path) -> bool:
    """True for unittest/pytest temp dirs so they cannot rewrite the live log."""
    try:
        folder = working_folder.resolve()
        tmp = Path(tempfile.gettempdir()).resolve()
        folder.relative_to(tmp)
        return True
    except (OSError, ValueError):
        return False


def sync_process_log_from_state(working_folder: Path, state: dict) -> Path | None:
    """Public alias used by save_piab_state."""
    if _is_ephemeral_working_folder(working_folder):
        return None
    return upsert_process_log_entry(working_folder, state)
