"""Warm DeckLink capture inputs in vMix before MultiCorder starts.

Cycling each Quad HDMI camera onto Preview forces vMix to open the capture
pin and start HDMI audio. That reduces the mux glitch where the first AAC
packet is stamped with encoder-delay milliseconds instead of samples.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Callable

from piab_vmix_api import (
    DEFAULT_VMIX_API_BASE,
    call_vmix_function,
    fetch_vmix_xml,
    is_multicorder_active,
)

DECKLINK_QUAD_MARKER = "DeckLink Quad HDMI"
PREVIEW_HOLD_SEC = 2.0
SETTLE_SEC = 6.0


def list_decklink_quad_input_numbers(xml_text: str) -> list[str]:
    """Return vMix input numbers for DeckLink Quad HDMI cameras, sorted.

    Skips Mini Recorder 4K and any non-Quad DeckLink titles.
    """
    root = ET.fromstring(xml_text)
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for inp in root.findall(".//input"):
        title = " ".join(
            part
            for part in (
                (inp.get("title") or "").strip(),
                (inp.get("shortTitle") or "").strip(),
                (inp.text or "").strip(),
            )
            if part
        )
        if DECKLINK_QUAD_MARKER not in title:
            continue
        number = (inp.get("number") or "").strip()
        if not number or number in seen:
            continue
        seen.add(number)
        try:
            sort_key = int(number)
        except ValueError:
            sort_key = 0
        found.append((sort_key, number))
    found.sort()
    return [number for _sort_key, number in found]


def warmup_decklink_cameras(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    fetch_xml=fetch_vmix_xml,
    request_fn=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    preview_hold_sec: float = PREVIEW_HOLD_SEC,
    settle_sec: float = SETTLE_SEC,
    skip_if_recording: bool = True,
    fetch_active=is_multicorder_active,
) -> dict:
    """Preview-cycle Quad HDMI cameras, then pause so HDMI audio can lock.

    Does not start or stop MultiCorder. If MultiCorder is already recording,
    skip the cycle so a live take is not disrupted.
    """
    if skip_if_recording:
        try:
            if fetch_active(api_base=api_base):
                return {
                    "status": "skipped",
                    "reason": "already_recording",
                    "warmed": [],
                }
        except Exception:
            pass

    xml_text = fetch_xml(api_base=api_base)
    numbers = list_decklink_quad_input_numbers(xml_text)
    if not numbers:
        return {
            "status": "skipped",
            "reason": "no_decklink_inputs",
            "warmed": [],
        }

    warmed: list[str] = []
    for number in numbers:
        call_vmix_function(
            "PreviewInput",
            input_ref=number,
            api_base=api_base,
            request_fn=request_fn,
        )
        warmed.append(number)
        if preview_hold_sec > 0:
            sleep_fn(preview_hold_sec)
    if settle_sec > 0:
        sleep_fn(settle_sec)
    return {"status": "ok", "reason": "", "warmed": warmed}
