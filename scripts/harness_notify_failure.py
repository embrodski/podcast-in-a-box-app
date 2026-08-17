"""Write failure artifacts and show an immediate desktop alert for harness jobs."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from piab_disk_errors import summarize_disk_full_if_applicable, source_duration_sec_from_folder

FAILURE_JSON_NAME = "harness-FAILURE.json"
FAILURE_TXT_NAME = "harness-FAILURE.txt"
SESSION_LOG_DETAIL_LIMIT = 50_000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def summarize_error(
    exc: BaseException,
    *,
    working_folder: Path | None = None,
) -> str:
    """Return a short, user-facing summary of ``exc``."""
    disk_summary = summarize_disk_full_if_applicable(
        exc,
        working_folder=working_folder,
        source_duration_sec=source_duration_sec_from_folder(working_folder),
    )
    if disk_summary:
        return disk_summary

    text = str(exc).strip()
    if not text:
        return type(exc).__name__

    payment = re.search(
        r"ElevenLabs API HTTP 401:.*?(payment_required|payment_issue|Unauthorized)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if payment or "payment_issue" in text or "payment_required" in text:
        return (
            "ElevenLabs transcription failed: billing/payment issue on the API account. "
            "Fix the ElevenLabs subscription, then re-run prep."
        )

    unauthorized = re.search(r"ElevenLabs API HTTP 401", text, re.IGNORECASE)
    if unauthorized:
        return (
            "ElevenLabs transcription failed: unauthorized (check API key or billing), "
            "then re-run prep."
        )

    first_line = text.splitlines()[0]
    if len(first_line) > 240:
        return first_line[:237] + "..."
    return first_line


def collect_session_log(
    *,
    working_folder: Path | None,
    step_title: str,
    error_summary: str,
    error_detail: str = "",
    failure_txt: Path | None = None,
    process_log_path: Path | None = None,
) -> str:
    """Build the session log body for a PIAB error-report email."""
    parts = [
        "PIAB autocut error",
        "=" * 40,
    ]
    if working_folder is not None:
        folder = working_folder.resolve()
        parts.append(f"Session folder: {folder}")
        parts.append(f"Session name: {folder.name}")
    if step_title:
        parts.append(f"Failed step: {step_title}")
    if error_summary:
        parts.append(f"Summary: {error_summary}")
    parts.append("")

    if working_folder is not None:
        try:
            from piab_process_log import find_process_log_entry

            entry = find_process_log_entry(working_folder, log_path=process_log_path)
        except Exception:
            entry = None
        if entry:
            parts.append("--- Process log entry ---")
            parts.append(json.dumps(entry, indent=2, ensure_ascii=False))
            parts.append("")

        state_path = working_folder / "podcast-in-a-box.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = None
            if isinstance(state, dict):
                excerpt = {
                    "name": state.get("name"),
                    "resume_at": state.get("resume_at"),
                    "session_mode": state.get("session_mode"),
                    "created_at": state.get("created_at"),
                }
                parts.append("--- Session state excerpt ---")
                parts.append(json.dumps(excerpt, indent=2, ensure_ascii=False))
                parts.append("")

    if failure_txt is not None and failure_txt.is_file():
        try:
            text = failure_txt.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text:
            parts.append("--- Failure log ---")
            parts.append(text[:SESSION_LOG_DETAIL_LIMIT])
    elif error_detail:
        parts.append("--- Error detail ---")
        parts.append(error_detail[:SESSION_LOG_DETAIL_LIMIT])

    return "\n".join(parts).rstrip() + "\n"


def _mark_error_report_emailed(failure_json: Path) -> None:
    try:
        payload = json.loads(failure_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    payload["error_report_emailed_at"] = _utc_now_iso()
    try:
        failure_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return


def send_error_report_email(
    *,
    working_folder: Path | None,
    step_title: str,
    error_summary: str,
    error_detail: str = "",
    failure_json: Path | None = None,
    failure_txt: Path | None = None,
    process_log_path: Path | None = None,
    sender: Callable[..., Any] | None = None,
) -> bool:
    """
    Email the session log to the PIAB error inbox.

    Never raises — reporting must not break the pipeline. Returns True when sent
    or when a report was already sent for this failure marker.
    """
    if failure_json is not None and failure_json.is_file():
        try:
            payload = json.loads(failure_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("error_report_emailed_at"):
            return True

    try:
        from harness_env import load_harness_env
        from harness_email import SmtpConfig, send_piab_error_email

        load_harness_env()
        config = SmtpConfig.from_env()
        body = collect_session_log(
            working_folder=working_folder,
            step_title=step_title,
            error_summary=error_summary,
            error_detail=error_detail,
            failure_txt=failure_txt,
            process_log_path=process_log_path,
        )
        send_piab_error_email(config, session_log=body, sender=sender)
        if failure_json is not None and failure_json.is_file():
            _mark_error_report_emailed(failure_json)
        return True
    except Exception as exc:
        print(f"ERROR: could not send PIAB error report email: {exc}", file=sys.stderr)
        return False


def write_failure_artifacts(
    temp_dir: Path,
    *,
    pipeline: str,
    step_id: str,
    step_title: str,
    error_summary: str,
    error_detail: str,
    working_folder: Path | None = None,
) -> tuple[Path, Path]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "harness_failure",
        "pipeline": pipeline,
        "step_id": step_id,
        "step_title": step_title,
        "failed_at": _utc_now_iso(),
        "error_summary": error_summary,
        "error_detail": error_detail,
        "working_folder": str(working_folder.resolve()) if working_folder else None,
        "notify_immediately": True,
    }
    json_path = temp_dir / FAILURE_JSON_NAME
    txt_path = temp_dir / FAILURE_TXT_NAME
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(
        "\n".join(
            [
                "HARNESS JOB FAILED",
                "=" * 40,
                f"Pipeline: {pipeline}",
                f"Step: {step_title} ({step_id})",
                f"Time (UTC): {payload['failed_at']}",
                "",
                error_summary,
                "",
                "Details:",
                error_detail,
                "",
                f"Marker file: {json_path}",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, txt_path


def show_desktop_alert(*, title: str, message: str) -> bool:
    """
    Show a Windows toast notification with sound.

    Returns True when a toast was attempted successfully.
    """
    if sys.platform != "win32":
        print(f"\aALERT: {title}\n{message}", file=sys.stderr)
        return False

    safe_title = _xml_escape(title[:120])
    safe_message = _xml_escape(message[:240])
    ps_script = f"""
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$template = @"
<toast>
  <visual>
    <binding template="ToastText02">
      <text id="1">{safe_title}</text>
      <text id="2">{safe_message}</text>
    </binding>
  </visual>
  <audio src="ms-winsoundevent:Notification.Default"/>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Podcast In A Box").Show($toast)
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True

    # Fallback: audible bell + stderr banner.
    print(f"\aALERT: {title}\n{message}", file=sys.stderr)
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return False


def notify_harness_failure(
    *,
    temp_dir: Path,
    pipeline: str,
    step_id: str,
    step_title: str,
    exc: BaseException,
    working_folder: Path | None = None,
    alert_title: str | None = None,
    notify: bool = True,
) -> dict:
    """
    Write failure marker files under ``temp_dir``, email a bug report, and
    show a desktop toast.
    """
    error_detail = str(exc).strip() or repr(exc)
    error_summary = summarize_error(exc, working_folder=working_folder)
    json_path, txt_path = write_failure_artifacts(
        temp_dir,
        pipeline=pipeline,
        step_id=step_id,
        step_title=step_title,
        error_summary=error_summary,
        error_detail=error_detail,
        working_folder=working_folder,
    )
    title = alert_title or f"{pipeline} failed"
    user_message = f"{step_title}: {error_summary}"
    emailed = send_error_report_email(
        working_folder=working_folder,
        step_title=step_title,
        error_summary=error_summary,
        error_detail=error_detail,
        failure_json=json_path,
        failure_txt=txt_path,
    )
    if notify:
        show_desktop_alert(title=title, message=user_message)

    return {
        "failed": True,
        "pipeline": pipeline,
        "step_id": step_id,
        "step_title": step_title,
        "error_summary": error_summary,
        "failure_json": str(json_path),
        "failure_txt": str(txt_path),
        "user_message": user_message,
        "notify_immediately": notify,
        "error_report_emailed": emailed,
    }
