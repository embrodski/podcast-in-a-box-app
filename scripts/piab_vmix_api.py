"""Shared vMix HTTP API helpers for PIAB."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_VMIX_API_BASE = "http://127.0.0.1:8088/api/"
DEFAULT_API_WAIT_SEC = 90.0


def fetch_vmix_xml(*, api_base: str = DEFAULT_VMIX_API_BASE, timeout_sec: float = 10.0) -> str:
    request = urllib.request.Request(api_base, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return response.read().decode("utf-8", errors="replace")


def wait_for_vmix_api(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    timeout_sec: float = DEFAULT_API_WAIT_SEC,
    poll_sec: float = 1.0,
    fetch_xml=fetch_vmix_xml,
) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            fetch_vmix_xml(api_base=api_base, timeout_sec=3.0)
            return True
        except (urllib.error.URLError, TimeoutError, ET.ParseError):
            time.sleep(poll_sec)
    return False


def call_vmix_function(
    function: str,
    *,
    value: str | None = None,
    input_ref: str | None = None,
    api_base: str = DEFAULT_VMIX_API_BASE,
    request_fn=None,
    timeout_sec: float = 30.0,
) -> None:
    params = {"Function": function}
    if value is not None:
        params["Value"] = value
    if input_ref is not None:
        params["Input"] = input_ref
    url = api_base + "?" + urllib.parse.urlencode(params)
    caller = request_fn or _default_api_request
    caller(url, timeout_sec=timeout_sec)


def _default_api_request(url: str, *, timeout_sec: float) -> None:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        response.read()


def is_multicorder_active(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    fetch_xml=fetch_vmix_xml,
) -> bool:
    xml_text = fetch_xml(api_base=api_base)
    root = ET.fromstring(xml_text)
    return (root.findtext("multiCorder") or "").strip().lower() == "true"
