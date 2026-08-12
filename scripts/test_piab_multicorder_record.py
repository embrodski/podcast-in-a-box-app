"""Tests for PIAB MultiCorder recording session."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from piab_multicorder_record import (
    build_recording_message,
    format_end_phrase_display,
    prepare_multicorder_recording,
    run_multicorder_session,
    wait_for_recording_continue,
)


class PiabMulticorderRecordTests(unittest.TestCase):
    def test_build_recording_message_includes_phrases(self) -> None:
        message = build_recording_message(
            trigger_phrase="Start here",
            end_phrases=["End one", "End two"],
            countdown_tokens=["five", "four", "three", "two"],
        )
        self.assertIn('Start Trigger ("Start here")', message)
        self.assertIn("count down", message.lower())
        self.assertIn('Ending Phrase ("End one" or "End two")', message)
        self.assertIn("THIS WILL STOP RECORDING", message)

    def test_format_end_phrase_display_single(self) -> None:
        self.assertEqual(format_end_phrase_display(["Done"]), "Done")

    def test_wait_for_recording_continue_accepts_continue(self) -> None:
        confirmed = wait_for_recording_continue(
            use_continue_button_flag=False,
            input_fn=lambda _prompt: "continue",
            print_fn=lambda *_args, **_kwargs: None,
        )
        self.assertTrue(confirmed)

    @patch("piab_multicorder_record.wait_for_vmix_api", return_value=True)
    def test_run_multicorder_session_start_stop(self, _mock_wait) -> None:
        calls: list[str] = []
        active = {"value": False}

        def fake_request(url: str, *, timeout_sec: float) -> None:
            if "Function=StartMultiCorder" in url:
                calls.append("StartMultiCorder")
                active["value"] = True
            elif "Function=StopMultiCorder" in url:
                calls.append("StopMultiCorder")
                active["value"] = False

        def fake_active(**_kwargs) -> bool:
            return active["value"]

        result = run_multicorder_session(
            auto_continue=True,
            request_fn=fake_request,
            fetch_active=fake_active,
            print_fn=lambda *_args, **_kwargs: None,
            gates={
                "start_trigger_phrase": "Start phrase",
                "end_phrases": ["End phrase"],
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["StartMultiCorder", "StopMultiCorder"])

    @patch("piab_multicorder_record.wait_for_vmix_api", return_value=True)
    def test_prepare_already_recording_continue(self, _mock_wait) -> None:
        calls: list[str] = []

        def fake_request(url: str, *, timeout_sec: float) -> None:
            if "Function=StartMultiCorder" in url:
                calls.append("StartMultiCorder")
            elif "Function=StopMultiCorder" in url:
                calls.append("StopMultiCorder")

        prepare_multicorder_recording(
            already_recording_action="continue",
            request_fn=fake_request,
            fetch_active=lambda **_kwargs: True,
            print_fn=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(calls, [])

    @patch("piab_multicorder_record.wait_for_vmix_api", return_value=True)
    def test_prepare_already_recording_restart(self, _mock_wait) -> None:
        calls: list[str] = []
        sleeps: list[float] = []

        def fake_request(url: str, *, timeout_sec: float) -> None:
            if "Function=StartMultiCorder" in url:
                calls.append("StartMultiCorder")
            elif "Function=StopMultiCorder" in url:
                calls.append("StopMultiCorder")

        prepare_multicorder_recording(
            already_recording_action="restart",
            request_fn=fake_request,
            fetch_active=lambda **_kwargs: True,
            sleep_fn=lambda sec: sleeps.append(sec),
            print_fn=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(calls, ["StopMultiCorder", "StartMultiCorder"])
        self.assertEqual(sleeps, [2.0])

    def test_continue_button_flag(self) -> None:
        event = threading.Event()
        event.set()
        confirmed = wait_for_recording_continue(
            use_continue_button_flag=True,
            continue_event=event,
            print_fn=lambda *_args, **_kwargs: None,
        )
        self.assertTrue(confirmed)


if __name__ == "__main__":
    unittest.main()
