"""Tests for vMix DeckLink camera warm-up."""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from piab_vmix_warmup import (
    SETTLE_SEC,
    list_decklink_quad_input_numbers,
    warmup_decklink_cameras,
)

SAMPLE_XML = """
<vmix>
  <inputs>
    <input number="1" type="Capture" title="DeckLink Quad HDMI Recorder (2) 2" />
    <input number="2" type="Capture" title="DeckLink Quad HDMI Recorder (1) 1" />
    <input number="3" type="Capture" title="DeckLink Quad HDMI Recorder (3) 3" />
    <input number="4" type="Capture" title="DeckLink Quad HDMI Recorder (4) 4" />
    <input number="5" type="Capture" title="DeckLink Mini Recorder 4K 5" />
    <input number="6" type="Capture" title="Audio Input" />
  </inputs>
  <multiCorder>False</multiCorder>
</vmix>
"""


class PiabVmixWarmupTests(unittest.TestCase):
    def test_list_skips_mini_recorder_and_sorts_by_number(self) -> None:
        numbers = list_decklink_quad_input_numbers(SAMPLE_XML)
        self.assertEqual(numbers, ["1", "2", "3", "4"])

    def test_warmup_preview_cycles_quad_inputs_then_settles(self) -> None:
        calls: list[str] = []
        sleeps: list[float] = []

        def fake_request(url: str, *, timeout_sec: float) -> None:
            calls.append(url)

        result = warmup_decklink_cameras(
            fetch_xml=lambda **_kwargs: SAMPLE_XML,
            request_fn=fake_request,
            sleep_fn=sleeps.append,
            fetch_active=lambda **_kwargs: False,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["warmed"], ["1", "2", "3", "4"])
        self.assertEqual(len(calls), 4)
        inputs = []
        for url in calls:
            params = parse_qs(urlparse(url).query)
            self.assertEqual(params.get("Function"), ["PreviewInput"])
            self.assertNotIn("StartMultiCorder", url)
            inputs.extend(params.get("Input", []))
        self.assertEqual(inputs, ["1", "2", "3", "4"])
        self.assertEqual(SETTLE_SEC, 6.0)
        self.assertEqual(sleeps, [2.0, 2.0, 2.0, 2.0, SETTLE_SEC])

    def test_warmup_skips_when_multicorder_already_recording(self) -> None:
        calls: list[str] = []
        result = warmup_decklink_cameras(
            fetch_xml=lambda **_kwargs: SAMPLE_XML,
            request_fn=lambda url, **_kwargs: calls.append(url),
            sleep_fn=lambda _sec: None,
            fetch_active=lambda **_kwargs: True,
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_recording")
        self.assertEqual(calls, [])

    def test_warmup_skips_when_no_quad_inputs(self) -> None:
        xml = (
            "<vmix><inputs>"
            '<input number="5" title="DeckLink Mini Recorder 4K 5" />'
            "</inputs></vmix>"
        )
        result = warmup_decklink_cameras(
            fetch_xml=lambda **_kwargs: xml,
            request_fn=lambda url, **_kwargs: None,
            sleep_fn=lambda _sec: None,
            fetch_active=lambda **_kwargs: False,
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_decklink_inputs")


if __name__ == "__main__":
    unittest.main()
