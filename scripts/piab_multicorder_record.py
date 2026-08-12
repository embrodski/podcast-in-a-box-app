"""Start/stop vMix MultiCorder recording for PIAB."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from piab_vmix_api import (
    DEFAULT_VMIX_API_BASE,
    call_vmix_function,
    is_multicorder_active,
    wait_for_vmix_api,
)
from podcast_phrase_gates import (
    end_phrases_from_gates,
    load_phrase_gates,
    start_countdown_allow_in_from_gates,
    start_countdown_tokens_from_gates,
    start_trigger_phrase_from_gates,
)

# Future standalone PIAB app: set True (or PIAB_USE_CONTINUE_BUTTON=1) to replace
# the stdin prompt with a UI Continue button wired to ``continue_event``.
PIAB_USE_CONTINUE_BUTTON = False

_CONTINUE_REPLIES = frozenset(
    {
        "continue",
        "ready",
        "y",
        "yes",
        "ok",
        "done",
    }
)

_ALREADY_RECORDING_CONTINUE = frozenset(
    {
        "1",
        "c",
        "continue",
        "keep",
        "yes",
        "y",
    }
)
_ALREADY_RECORDING_RESTART = frozenset(
    {
        "2",
        "r",
        "restart",
        "stop",
    }
)

RESTART_PAUSE_SEC = 2.0

ALREADY_RECORDING_MESSAGE = (
    "MultiCorder is already recording."
)


@dataclass(frozen=True)
class MulticorderSessionResult:
    status: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"completed", "skipped"}


def use_continue_button() -> bool:
    env = str(os.environ.get("PIAB_USE_CONTINUE_BUTTON") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    return PIAB_USE_CONTINUE_BUTTON


def format_end_phrase_display(end_phrases: list[str]) -> str:
    cleaned = [str(p).strip() for p in end_phrases if str(p).strip()]
    if not cleaned:
        return "(not configured)"
    if len(cleaned) == 1:
        return cleaned[0]
    return '" or "'.join(cleaned)


def build_recording_message(
    *,
    trigger_phrase: str,
    end_phrases: list[str],
    countdown_tokens: list[str] | None = None,
    allow_in: bool = True,
) -> str:
    end_display = format_end_phrase_display(end_phrases)
    start_lines = [
        "Program is running. Please sit and activate with the Start Trigger "
        f'("{trigger_phrase}") whenever you are ready.'
    ]
    if countdown_tokens:
        from podcast_phrase_gates import format_countdown_hint

        hint = format_countdown_hint(countdown_tokens, allow_in=allow_in)
        start_lines.append(
            f"Optionally count down ({hint}) before you begin the interview."
        )
    start_lines.append(
        f'When you are finished, end with the Ending Phrase ("{end_display}") '
        "and then return here for final steps."
    )
    start_lines.append(
        "When you are done recording your podcast, type or press Continue. "
        "THIS WILL STOP RECORDING! DO NOT PUSH UNTIL YOU ARE DONE WITH THE PODCAST!"
    )
    return "\n".join(start_lines)


def build_recording_message_legacy(*, start_phrase: str, end_phrases: list[str]) -> str:
    """Deprecated: combined start_phrase string."""
    return build_recording_message(
        trigger_phrase=start_phrase,
        end_phrases=end_phrases,
        countdown_tokens=None,
    )


def wait_for_recording_continue(
    *,
    use_continue_button_flag: bool | None = None,
    continue_event: threading.Event | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> bool:
    enabled = use_continue_button() if use_continue_button_flag is None else use_continue_button_flag
    if enabled:
        if continue_event is None:
            raise RuntimeError(
                "PIAB_USE_CONTINUE_BUTTON is enabled but no continue_event was provided."
            )
        print_fn("Waiting for Continue button...")
        continue_event.wait()
        return True

    while True:
        answer = input_fn("Type or press Continue when recording is finished: ").strip().lower()
        if answer in _CONTINUE_REPLIES:
            return True
        print_fn("Please type 'continue' when you are done recording.")


def prompt_already_recording_choice(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> str:
    """
    Ask whether to keep the current MultiCorder session or stop and restart.

    Returns ``"continue"`` or ``"restart"``.
    """
    print_fn(ALREADY_RECORDING_MESSAGE)
    print_fn("  1 = Continue current recording")
    print_fn("  2 = Stop and restart recording")
    while True:
        answer = input_fn("Choose [1=continue, 2=restart]: ").strip().lower()
        if answer in _ALREADY_RECORDING_CONTINUE:
            return "continue"
        if answer in _ALREADY_RECORDING_RESTART:
            return "restart"
        print_fn("Please enter 1 to continue recording or 2 to stop and restart.")


def start_multicorder(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    request_fn=None,
    fetch_active=is_multicorder_active,
    force: bool = False,
) -> None:
    if not force and fetch_active(api_base=api_base):
        return
    call_vmix_function(
        "StartMultiCorder",
        api_base=api_base,
        request_fn=request_fn,
    )


def stop_multicorder(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    request_fn=None,
    fetch_active=is_multicorder_active,
    force: bool = False,
) -> None:
    if not force and not fetch_active(api_base=api_base):
        return
    call_vmix_function(
        "StopMultiCorder",
        api_base=api_base,
        request_fn=request_fn,
    )


def restart_multicorder(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    request_fn=None,
    fetch_active=is_multicorder_active,
    pause_sec: float = RESTART_PAUSE_SEC,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    stop_multicorder(
        api_base=api_base,
        request_fn=request_fn,
        fetch_active=fetch_active,
        force=True,
    )
    sleep_fn(pause_sec)
    start_multicorder(
        api_base=api_base,
        request_fn=request_fn,
        fetch_active=fetch_active,
        force=True,
    )


def prepare_multicorder_recording(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    request_fn=None,
    fetch_active=is_multicorder_active,
    already_recording_action: str | None = None,
    auto_continue: bool = False,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    if not fetch_active(api_base=api_base):
        start_multicorder(
            api_base=api_base,
            request_fn=request_fn,
            fetch_active=fetch_active,
        )
        return

    action = already_recording_action
    if action is None and auto_continue:
        action = "continue"
    action = action or prompt_already_recording_choice(
        input_fn=input_fn,
        print_fn=print_fn,
    )
    if action == "continue":
        return
    if action == "restart":
        restart_multicorder(
            api_base=api_base,
            request_fn=request_fn,
            fetch_active=fetch_active,
            sleep_fn=sleep_fn,
        )
        return
    raise ValueError(f"Unknown already-recording action: {action!r}")


def run_multicorder_session(    *,
    skip: bool = False,
    auto_continue: bool = False,
    api_base: str = DEFAULT_VMIX_API_BASE,
    api_wait_sec: float = 90.0,
    use_continue_button_flag: bool | None = None,
    continue_event: threading.Event | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    request_fn=None,
    fetch_active=is_multicorder_active,
    gates: dict | None = None,
    already_recording_action: str | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> MulticorderSessionResult:
    if skip:
        return MulticorderSessionResult(status="skipped")

    if sys.platform != "win32":
        return MulticorderSessionResult(
            status="skipped",
            message="MultiCorder session skipped (Windows only).",
        )

    if not wait_for_vmix_api(api_base=api_base, timeout_sec=api_wait_sec):
        return MulticorderSessionResult(
            status="failed",
            message="Timed out waiting for vMix HTTP API to become available.",
        )

    phrase_gates = gates if gates is not None else load_phrase_gates()
    trigger = start_trigger_phrase_from_gates(phrase_gates)
    end_phrases = end_phrases_from_gates(phrase_gates)
    countdown = start_countdown_tokens_from_gates(phrase_gates)
    allow_in = start_countdown_allow_in_from_gates(phrase_gates)
    if not trigger:
        return MulticorderSessionResult(
            status="failed",
            message="Start trigger phrase is not configured in podcast-phrase-gates.json.",
        )

    try:
        prepare_multicorder_recording(
            api_base=api_base,
            request_fn=request_fn,
            fetch_active=fetch_active,
            already_recording_action=already_recording_action,
            auto_continue=auto_continue,
            input_fn=input_fn,
            print_fn=print_fn,
            sleep_fn=sleep_fn,
        )
    except Exception as exc:
        return MulticorderSessionResult(
            status="failed",
            message=f"Failed to prepare MultiCorder recording: {exc}",
        )

    print_fn(
        build_recording_message(
            trigger_phrase=trigger,
            end_phrases=end_phrases,
            countdown_tokens=countdown or None,
            allow_in=allow_in,
        )
    )
    print_fn("")

    if auto_continue:
        stop_multicorder(
            api_base=api_base,
            request_fn=request_fn,
            fetch_active=fetch_active,
        )
        return MulticorderSessionResult(status="completed")

    if not wait_for_recording_continue(
        use_continue_button_flag=use_continue_button_flag,
        continue_event=continue_event,
        input_fn=input_fn,
        print_fn=print_fn,
    ):
        return MulticorderSessionResult(
            status="failed",
            message="Recording session was not confirmed complete.",
        )

    try:
        stop_multicorder(
            api_base=api_base,
            request_fn=request_fn,
            fetch_active=fetch_active,
        )
    except Exception as exc:
        return MulticorderSessionResult(
            status="failed",
            message=f"Failed to stop MultiCorder: {exc}",
        )

    return MulticorderSessionResult(status="completed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start vMix MultiCorder, wait for user, then stop recording.",
    )
    parser.add_argument(
        "--skip",
        action="store_true",
        help="Skip MultiCorder session (automation/CI).",
    )
    parser.add_argument(
        "--auto-continue",
        action="store_true",
        help="Non-interactive: start and immediately stop MultiCorder.",
    )
    parser.add_argument(
        "--already-recording",
        choices=("continue", "restart"),
        help="Non-interactive: action when MultiCorder is already recording.",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_VMIX_API_BASE,
    )
    args = parser.parse_args()

    result = run_multicorder_session(
        skip=args.skip,
        auto_continue=args.auto_continue,
        api_base=args.api_base,
        already_recording_action=args.already_recording,
    )
    if result.message:
        print(result.message, file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
