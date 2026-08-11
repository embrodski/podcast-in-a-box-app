"""Orchestrate Frame.io upload, share creation, email, and Output artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from harness_env import load_harness_env

load_harness_env()

from frameio_client import (
    FrameioConfig,
    FrameioDeliveryResult,
    sanitize_frameio_error,
    upload_file_and_create_share,
)
from harness_email import (
    SmtpConfig,
    send_delivery_failure_email,
    send_delivery_success_email,
)
from harness_episode_lib import utc_now_iso

FULL_INTERVIEW_MP4 = "Full Interview.mp4"
FULL_INTERVIEW_DELIVERY_JSON = "Full Interview.delivery.json"
FULL_INTERVIEW_TRANSCRIPT_JSON = "Full Interview Transcript.json"
DELIVERY_SUMMARY_JSON = "delivery-summary.json"


def delivery_is_enabled(state: dict) -> bool:
    delivery = state.get("delivery") or {}
    return bool(delivery.get("enabled") and delivery.get("email"))


def transcript_source_path(state: dict) -> Path | None:
    raw = state.get("main_transcript_json")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_file() else None


def copy_transcript_to_output(state: dict, output_dir: Path) -> Path | None:
    source = transcript_source_path(state)
    if source is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / FULL_INTERVIEW_TRANSCRIPT_JSON
    shutil.copy2(source, dest)
    return dest


def build_output_delivery_record(
    *,
    recipient_email: str,
    local_video_path: Path,
    transcript_path: Path | None,
    frameio: FrameioDeliveryResult,
) -> dict[str, Any]:
    return {
        "recipient_email": recipient_email,
        "local_video_path": str(local_video_path.resolve()),
        "transcript_path": str(transcript_path.resolve()) if transcript_path else None,
        "file_id": frameio.upload.file_id,
        "share_id": frameio.share.share_id,
        "short_url": frameio.share.short_url,
        "completed_at": utc_now_iso(),
    }


def write_output_delivery_json(output_dir: Path, record: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / FULL_INTERVIEW_DELIVERY_JSON
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def write_delivery_summary(temp_dir: Path, summary: dict[str, Any]) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / DELIVERY_SUMMARY_JSON
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return path


def _initial_delivery_status(state: dict) -> dict[str, Any]:
    delivery = dict(state.get("delivery") or {})
    delivery.setdefault("deliverable", "full_interview")
    delivery.setdefault(
        "frameio",
        {
            "status": "pending",
            "file_id": "",
            "share_id": "",
            "short_url": "",
            "error": "",
            "completed_at": "",
        },
    )
    delivery.setdefault(
        "email_delivery",
        {
            "status": "pending",
            "sent_at": "",
            "error": "",
        },
    )
    return delivery


def deliver_piab_full_interview(
    state: dict,
    *,
    video_path: Path,
    dry_run: bool = False,
    print_fn=print,
) -> dict[str, Any]:
    """
    Upload ``video_path`` to Frame.io, create a public share, email the link,
    and write Output/Temp delivery artifacts.

    Returns an updated ``delivery`` block for session state. Render success is
    independent; delivery failures are recorded but not raised unless config is
    missing in non-dry-run mode and caller wants strict behavior — here we
    catch and record failures.
    """
    if not delivery_is_enabled(state):
        return _initial_delivery_status(state)

    delivery = _initial_delivery_status(state)
    recipient = str(delivery["email"])
    episode_name = str(state.get("name") or "Podcast Interview")
    output_dir = Path(state["paths"]["output"])
    temp_dir = Path(state["paths"]["temp"])
    video_path = video_path.resolve()

    transcript_path = copy_transcript_to_output(state, output_dir)
    summary: dict[str, Any] = {
        "episode_name": episode_name,
        "recipient_email": recipient,
        "local_video_path": str(video_path),
        "transcript_path": str(transcript_path) if transcript_path else None,
        "dry_run": dry_run,
    }

    try:
        frameio_config = FrameioConfig.from_env()
        smtp_config = SmtpConfig.from_env()
        if dry_run:
            delivery["frameio"]["status"] = "skipped"
            delivery["email_delivery"]["status"] = "skipped"
            summary["status"] = "dry_run_ok"
            write_delivery_summary(temp_dir, summary)
            print_fn(
                f"Delivery dry-run OK for {recipient} "
                f"(Frame.io + SMTP configuration present)."
            )
            return delivery

        print_fn(
            f"Uploading {video_path.name} to Frame.io for delivery to {recipient}..."
        )
        frameio_result = upload_file_and_create_share(
            frameio_config,
            file_path=video_path,
            share_name=f"{episode_name} — Full Interview",
        )
        record = build_output_delivery_record(
            recipient_email=recipient,
            local_video_path=video_path,
            transcript_path=transcript_path,
            frameio=frameio_result,
        )
        output_record_path = write_output_delivery_json(output_dir, record)

        delivery["frameio"].update(
            {
                "status": "completed",
                "file_id": frameio_result.upload.file_id,
                "share_id": frameio_result.share.share_id,
                "short_url": frameio_result.share.short_url,
                "error": "",
                "completed_at": utc_now_iso(),
            }
        )
        summary.update(
            {
                "status": "completed",
                "file_id": frameio_result.upload.file_id,
                "share_id": frameio_result.share.share_id,
                "short_url": frameio_result.share.short_url,
                "output_delivery_json": str(output_record_path),
            }
        )

        send_delivery_success_email(
            smtp_config,
            to_addr=recipient,
            episode_name=episode_name,
            short_url=frameio_result.share.short_url,
        )
        delivery["email_delivery"].update(
            {
                "status": "sent",
                "sent_at": utc_now_iso(),
                "error": "",
            }
        )
        summary["email_delivery"] = {"status": "sent"}
        write_delivery_summary(temp_dir, summary)

        print_fn("Delivery complete.")
        print_fn(f"  Recipient: {recipient}")
        print_fn(f"  File ID:   {frameio_result.upload.file_id}")
        print_fn(f"  Share ID:  {frameio_result.share.share_id}")
        print_fn(f"  Link:      {frameio_result.share.short_url}")
        print_fn(f"  Saved:     {output_record_path}")
        if transcript_path is not None:
            print_fn(f"  Transcript: {transcript_path}")
        return delivery
    except Exception as exc:
        error_summary = sanitize_frameio_error(str(exc))
        delivery["frameio"]["status"] = "failed"
        delivery["frameio"]["error"] = error_summary
        delivery["email_delivery"]["status"] = "failed"
        delivery["email_delivery"]["error"] = error_summary
        summary.update({"status": "failed", "error": error_summary})
        write_delivery_summary(temp_dir, summary)

        try:
            smtp_config = SmtpConfig.from_env()
            send_delivery_failure_email(
                smtp_config,
                to_addr=recipient,
                episode_name=episode_name,
                local_path=str(video_path),
                error_summary=error_summary,
            )
            delivery["email_delivery"]["status"] = "sent"
            delivery["email_delivery"]["sent_at"] = utc_now_iso()
            delivery["email_delivery"]["error"] = ""
        except Exception as mail_exc:
            delivery["email_delivery"]["status"] = "failed"
            delivery["email_delivery"]["error"] = sanitize_frameio_error(str(mail_exc))

        print_fn("Delivery failed (local render is still complete).")
        print_fn(f"  Recipient: {recipient}")
        print_fn(f"  Local file: {video_path}")
        print_fn(f"  Error: {error_summary}")
        return delivery


def resolve_delivery_short_url(state: dict) -> str:
    """Return the Frame.io share link from session state or Output delivery JSON."""
    delivery = state.get("delivery") or {}
    url = str((delivery.get("frameio") or {}).get("short_url") or "").strip()
    if url:
        return url

    paths = state.get("paths") or {}
    output_dir = Path(str(paths.get("output") or ""))
    if output_dir.is_dir():
        record_path = output_dir / FULL_INTERVIEW_DELIVERY_JSON
        if record_path.is_file():
            record = json.loads(record_path.read_text(encoding="utf-8"))
            url = str(record.get("short_url") or "").strip()
            if url:
                return url

    raise RuntimeError(
        "No delivery link is available for this session. "
        "Email delivery may not have completed successfully."
    )


def send_delivery_link_email(
    state: dict,
    *,
    to_addr: str,
    print_fn=print,
) -> str:
    """
    Email an existing Frame.io share link without re-uploading the video.

    Returns the normalized recipient address.
    """
    from harness_delivery_prompt import is_valid_email, normalize_email

    recipient = normalize_email(to_addr)
    if not is_valid_email(recipient):
        raise ValueError(f"Invalid email address: {to_addr!r}")

    short_url = resolve_delivery_short_url(state)
    episode_name = str(state.get("name") or "Podcast Interview")
    smtp_config = SmtpConfig.from_env()
    send_delivery_success_email(
        smtp_config,
        to_addr=recipient,
        episode_name=episode_name,
        short_url=short_url,
    )

    delivery = state.setdefault("delivery", {})
    extras = delivery.setdefault("additional_emails", [])
    if recipient not in extras:
        extras.append(recipient)

    print_fn(f"Delivery link emailed to {recipient}.")
    return recipient
