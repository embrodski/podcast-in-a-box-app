"""SMTP email helpers for harness video delivery."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

PIAB_ERROR_REPORT_TO = "embrodski@gmail.com"
PIAB_ERROR_REPORT_SUBJECT = "PIAB autocut error"


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    use_tls: bool = True

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> SmtpConfig:
        env = environ if environ is not None else os.environ
        missing = [
            name
            for name in (
                "HARNESS_SMTP_HOST",
                "HARNESS_SMTP_USER",
                "HARNESS_SMTP_PASSWORD",
                "HARNESS_SMTP_FROM",
            )
            if not str(env.get(name, "")).strip()
        ]
        if missing:
            raise ValueError("Missing SMTP configuration: " + ", ".join(missing))
        port_raw = str(env.get("HARNESS_SMTP_PORT", "587")).strip()
        use_tls_raw = str(env.get("HARNESS_SMTP_USE_TLS", "true")).strip().lower()
        return cls(
            host=str(env["HARNESS_SMTP_HOST"]).strip(),
            port=int(port_raw),
            user=str(env["HARNESS_SMTP_USER"]).strip(),
            password=str(env["HARNESS_SMTP_PASSWORD"]).strip(),
            sender=str(env["HARNESS_SMTP_FROM"]).strip(),
            use_tls=use_tls_raw not in {"0", "false", "no"},
        )


def send_email(
    config: SmtpConfig,
    *,
    to_addr: str,
    subject: str,
    body: str,
    sender: Callable[[SmtpConfig, EmailMessage], None] | None = None,
) -> None:
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = to_addr
    message["Subject"] = subject
    message.set_content(body)
    deliver = sender or _default_send
    deliver(config, message)


def _default_send(config: SmtpConfig, message: EmailMessage) -> None:
    if config.use_tls:
        with smtplib.SMTP(config.host, config.port, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(config.user, config.password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(config.host, config.port, timeout=60) as smtp:
        if config.user:
            smtp.login(config.user, config.password)
        smtp.send_message(message)


def send_delivery_success_email(
    config: SmtpConfig,
    *,
    to_addr: str,
    episode_name: str,
    short_url: str,
    flag_report: str | None = None,
    sender: Callable[[SmtpConfig, EmailMessage], None] | None = None,
) -> None:
    subject = f"Your podcast interview is ready — {episode_name}"
    body = (
        f"Your Full Interview video for {episode_name} is ready.\n\n"
        f"View and download:\n{short_url}\n\n"
        "The share includes the interview video and a human-readable transcript.\n"
        "This link does not expire."
    )
    if flag_report and flag_report.strip():
        body += f"\n\nFlags in the final edit:\n{flag_report.strip()}"
    send_email(
        config,
        to_addr=to_addr,
        subject=subject,
        body=body,
        sender=sender,
    )


def send_piab_error_email(
    config: SmtpConfig,
    *,
    session_log: str,
    sender: Callable[[SmtpConfig, EmailMessage], None] | None = None,
) -> None:
    send_email(
        config,
        to_addr=PIAB_ERROR_REPORT_TO,
        subject=PIAB_ERROR_REPORT_SUBJECT,
        body=session_log,
        sender=sender,
    )


def send_delivery_failure_email(
    config: SmtpConfig,
    *,
    to_addr: str,
    episode_name: str,
    local_path: str,
    error_summary: str,
    sender: Callable[[SmtpConfig, EmailMessage], None] | None = None,
) -> None:
    subject = f"Podcast delivery failed — {episode_name}"
    body = (
        f"We finished rendering {episode_name}, but uploading or sharing the video failed.\n\n"
        f"Local file:\n{local_path}\n\n"
        f"Error:\n{error_summary}\n"
    )
    send_email(
        config,
        to_addr=to_addr,
        subject=subject,
        body=body,
        sender=sender,
    )
