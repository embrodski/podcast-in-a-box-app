"""Application-modal popup + bug-report email when an autocut step fails."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from app.controller.paths import ensure_scripts_path

FAILURE_POPUP_TITLE = "Autocut failed"
FAILURE_POPUP_TEXT = "The autocut failed, and a bug report has been submitted."
FAILURE_POPUP_INFO = (
    "Your original files are safe, and can still be exported or used for manual editing."
)

_shown_keys: set[str] = set()


def reset_failure_alerts_for_tests() -> None:
    _shown_keys.clear()


def failure_alert_key(
    working_folder: Path | None,
    *,
    summary: str,
    failed_at: str | None = None,
) -> str:
    folder = str(working_folder.resolve()) if working_folder is not None else ""
    if folder and failed_at:
        return f"{folder}|{failed_at}"
    return f"{folder}|{summary[:160]}"


def _failure_marker_payload(working_folder: Path | None) -> dict | None:
    if working_folder is None:
        return None
    marker = working_folder / "Temp" / "harness-FAILURE.json"
    if not marker.is_file():
        return None
    try:
        import json

        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _failed_at_from_folder(working_folder: Path | None) -> str | None:
    payload = _failure_marker_payload(working_folder)
    if payload is None:
        return None
    raw = payload.get("failed_at")
    return raw if isinstance(raw, str) and raw.strip() else None


def _popup_already_shown(working_folder: Path | None) -> bool:
    payload = _failure_marker_payload(working_folder)
    return bool(payload and payload.get("error_popup_shown_at"))


def _mark_popup_shown(working_folder: Path | None) -> None:
    if working_folder is None:
        return
    marker = working_folder / "Temp" / "harness-FAILURE.json"
    payload = _failure_marker_payload(working_folder)
    if payload is None:
        return
    from datetime import datetime, timezone

    payload["error_popup_shown_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    try:
        import json

        marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return


def show_autocut_failure_popup(parent: QWidget | None) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(FAILURE_POPUP_TITLE)
    box.setText(FAILURE_POPUP_TEXT)
    box.setInformativeText(FAILURE_POPUP_INFO)
    box.setStandardButtons(QMessageBox.Ok)
    box.setWindowModality(Qt.ApplicationModal)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.exec()


def alert_workflow_failure(
    parent: QWidget | None,
    *,
    working_folder: Path | None,
    summary: str,
    detail: str | None = None,
    step_title: str | None = None,
    aborted: bool = False,
    report: bool = True,
) -> bool:
    """
    Email a bug report (if needed) and show the failure popup.

    Dedupes on session folder + failure timestamp so Home poll and F1
    navigation do not stack two dialogs for the same failure.
    """
    if aborted or not report:
        return False

    failed_at = _failed_at_from_folder(working_folder)
    key = failure_alert_key(working_folder, summary=summary, failed_at=failed_at)
    if key in _shown_keys or _popup_already_shown(working_folder):
        _shown_keys.add(key)
        return False
    _shown_keys.add(key)

    ensure_scripts_path()
    from harness_notify_failure import (
        FAILURE_JSON_NAME,
        FAILURE_TXT_NAME,
        send_error_report_email,
    )

    failure_json = None
    failure_txt = None
    if working_folder is not None:
        temp = working_folder / "Temp"
        json_path = temp / FAILURE_JSON_NAME
        txt_path = temp / FAILURE_TXT_NAME
        if json_path.is_file():
            failure_json = json_path
        if txt_path.is_file():
            failure_txt = txt_path

    send_error_report_email(
        working_folder=working_folder,
        step_title=step_title or summary,
        error_summary=summary,
        error_detail=detail or "",
        failure_json=failure_json,
        failure_txt=failure_txt,
    )
    show_autocut_failure_popup(parent)
    _mark_popup_shown(working_folder)
    return True
