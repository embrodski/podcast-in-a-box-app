"""Interactive and CLI helpers for PIAB email delivery opt-in."""

from __future__ import annotations

import re
from typing import Callable

from harness_episode_lib import utc_now_iso

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str) -> str:
    return str(raw or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


def delivery_disabled() -> dict:
    return {"enabled": False}


def delivery_enabled(email: str, *, confirmed_at: str | None = None) -> dict:
    return {
        "enabled": True,
        "email": normalize_email(email),
        "email_confirmed_at": confirmed_at or utc_now_iso(),
        "deliverable": "full_interview",
    }


def delivery_already_confirmed(state: dict) -> bool:
    delivery = state.get("delivery") or {}
    return bool(
        delivery.get("enabled")
        and delivery.get("email")
        and delivery.get("email_confirmed_at")
    )


def prompt_yes_no(
    prompt: str,
    *,
    default: bool = False,
    input_fn: Callable[[str], str] = input,
) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = input_fn(f"{prompt} [{suffix}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter y or n.")


def prompt_email(*, input_fn: Callable[[str], str] = input) -> str:
    while True:
        raw = input_fn("Recipient email address: ").strip()
        if not raw:
            print("Email is required.")
            continue
        email = normalize_email(raw)
        if is_valid_email(email):
            return email
        print("That does not look like a valid email address.")


def confirm_delivery_email(
    email: str,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> dict | None:
    """
    Confirm ``email`` with Y/N/A.

    Returns a delivery block on success, ``None`` if the user aborts (same as
    opting out), or re-prompts on N.
    """
    while True:
        print_fn(f"\nDelivery email: {email}")
        answer = input_fn("Is this correct? [Y/N or A to abort]: ").strip().lower()
        if answer in {"", "y", "yes"}:
            return delivery_enabled(email)
        if answer in {"a", "abort"}:
            print_fn("Delivery aborted — continuing without email.")
            return None
        if answer in {"n", "no"}:
            email = prompt_email(input_fn=input_fn)
            continue
        print_fn("Please enter Y, N, or A.")


def prompt_delivery_opt_in(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> dict:
    """Ask whether to email the finished video; return a delivery state block."""
    if not prompt_yes_no(
        "Email the finished Full Interview video when the render completes?",
        default=False,
        input_fn=input_fn,
    ):
        return delivery_disabled()
    email = prompt_email(input_fn=input_fn)
    confirmed = confirm_delivery_email(email, input_fn=input_fn, print_fn=print_fn)
    if confirmed is None:
        return delivery_disabled()
    return confirmed


def delivery_from_cli(*, email: str, confirm: bool) -> dict:
    if not confirm:
        raise ValueError("--confirm-delivery-email is required with --delivery-email.")
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise ValueError(f"Invalid delivery email: {email!r}")
    return delivery_enabled(normalized)


def delivery_for_app(*, email: str) -> dict:
    """Non-interactive delivery opt-in from the GUI (single email field)."""
    return delivery_from_cli(email=email, confirm=True)


def merge_delivery_into_state(state: dict, delivery: dict) -> None:
    state["delivery"] = delivery
