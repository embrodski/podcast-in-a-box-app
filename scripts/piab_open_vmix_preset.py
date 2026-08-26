"""Open the standard PIAB vMix preset."""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VMIX_PRESET_NAME = "4 People - 5 Cameras - Default.vmix"
DEFAULT_VMIX_PRESET_DIRS = (
    Path(r"E:\PodcastRoom\vMix Configs"),
)
DEFAULT_VMIX_API_BASE = "http://127.0.0.1:8088/api/"
DEFAULT_API_WAIT_SEC = 90.0
DEFAULT_PRESET_WAIT_SEC = 45.0


@dataclass(frozen=True)
class VmixPresetResult:
    status: str
    preset_path: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"opened", "already_open", "skipped"}


def normalize_preset_name(name: str) -> tuple[str, ...]:
    text = str(name or "").strip()
    if not text:
        return ()
    candidates = [text]
    if text.endswith(" .vmix"):
        candidates.append(text.replace(" .vmix", ".vmix"))
    elif not text.lower().endswith(".vmix"):
        candidates.append(f"{text}.vmix")
    deduped: list[str] = []
    for item in candidates:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def find_vmix_preset(
    preset_name: str,
    *,
    search_dirs: tuple[Path, ...] = DEFAULT_VMIX_PRESET_DIRS,
    preset_path: Path | None = None,
) -> Path | None:
    if preset_path is not None:
        path = preset_path.resolve()
        return path if path.is_file() else None

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for candidate in normalize_preset_name(preset_name):
            path = directory / candidate
            if path.is_file():
                return path.resolve()
    return None


def fetch_vmix_xml(*, api_base: str = DEFAULT_VMIX_API_BASE, timeout_sec: float = 10.0) -> str:
    request = urllib.request.Request(api_base, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return response.read().decode("utf-8", errors="replace")


def current_vmix_preset_path(
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    fetch_xml=fetch_vmix_xml,
) -> str | None:
    try:
        xml_text = fetch_xml(api_base=api_base)
    except (urllib.error.URLError, TimeoutError):
        return None
    root = ET.fromstring(xml_text)
    preset = root.findtext("preset")
    if preset is None:
        return None
    text = preset.strip()
    return text or None


def _same_preset_path(left: Path, right: str | None) -> bool:
    if not right:
        return False
    try:
        return left.resolve() == Path(right).resolve()
    except OSError:
        return str(left).casefold() == right.casefold()


def open_vmix_preset_via_api(
    preset_path: Path,
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    request_fn=None,
    timeout_sec: float = 30.0,
) -> None:
    caller = request_fn or _default_api_request
    params = urllib.parse.urlencode(
        {
            "Function": "OpenPreset",
            "Value": str(preset_path.resolve()),
        }
    )
    caller(f"{api_base}?{params}", timeout_sec=timeout_sec)


def _default_api_request(url: str, *, timeout_sec: float) -> None:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        response.read()


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


def wait_for_vmix_preset(
    preset_path: Path,
    *,
    api_base: str = DEFAULT_VMIX_API_BASE,
    timeout_sec: float = DEFAULT_PRESET_WAIT_SEC,
    poll_sec: float = 0.5,
    fetch_xml=fetch_vmix_xml,
) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        current = current_vmix_preset_path(api_base=api_base, fetch_xml=fetch_xml)
        if _same_preset_path(preset_path, current):
            return True
        time.sleep(poll_sec)
    return False


def open_vmix_preset(
    *,
    preset_name: str = DEFAULT_VMIX_PRESET_NAME,
    preset_path: Path | None = None,
    search_dirs: tuple[Path, ...] = DEFAULT_VMIX_PRESET_DIRS,
    api_base: str = DEFAULT_VMIX_API_BASE,
    api_wait_sec: float = DEFAULT_API_WAIT_SEC,
    wait_sec: float = DEFAULT_PRESET_WAIT_SEC,
    skip: bool = False,
    request_fn=None,
    fetch_xml=fetch_vmix_xml,
    print_fn=print,
) -> VmixPresetResult:
    if skip:
        return VmixPresetResult(status="skipped")

    if sys.platform != "win32":
        return VmixPresetResult(
            status="skipped",
            message="vMix preset load skipped (Windows only).",
        )

    resolved = find_vmix_preset(
        preset_name,
        search_dirs=search_dirs,
        preset_path=preset_path,
    )
    if resolved is None:
        dirs = ", ".join(str(path) for path in search_dirs)
        return VmixPresetResult(
            status="missing",
            message=(
                f"vMix preset not found: {preset_name!r}. "
                f"Searched: {dirs}. Use --preset-path to override."
            ),
        )

    current = current_vmix_preset_path(api_base=api_base, fetch_xml=fetch_xml)
    if _same_preset_path(resolved, current):
        return VmixPresetResult(
            status="already_open",
            preset_path=str(resolved),
        )

    print_fn(f"Opening vMix preset: {resolved.name}")
    from piab_ensure_vmix import (
        ensure_vmix_running,
        find_vmix_executable,
        launch_vmix,
    )

    started = ensure_vmix_running(print_fn=print_fn)
    if not started.ok:
        return VmixPresetResult(
            status="failed",
            preset_path=str(resolved),
            message=started.message or "vMix could not be started.",
        )

    if not wait_for_vmix_api(
        api_base=api_base,
        timeout_sec=min(3.0, api_wait_sec),
        fetch_xml=fetch_xml,
    ):
        executable = find_vmix_executable()
        if executable is not None:
            launch_vmix(executable)
    if not wait_for_vmix_api(
        api_base=api_base,
        timeout_sec=api_wait_sec,
        fetch_xml=fetch_xml,
    ):
        return VmixPresetResult(
            status="failed",
            preset_path=str(resolved),
            message="Timed out waiting for vMix HTTP API to become available.",
        )

    try:
        open_vmix_preset_via_api(
            resolved,
            api_base=api_base,
            request_fn=request_fn,
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        return VmixPresetResult(
            status="failed",
            preset_path=str(resolved),
            message=f"vMix API OpenPreset failed: {exc}",
        )

    if wait_for_vmix_preset(
        resolved,
        api_base=api_base,
        timeout_sec=wait_sec,
        fetch_xml=fetch_xml,
    ):
        return VmixPresetResult(
            status="opened",
            preset_path=str(resolved),
        )

    return VmixPresetResult(
        status="failed",
        preset_path=str(resolved),
        message="Timed out waiting for vMix to load the preset.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open the standard PIAB vMix preset.",
    )
    parser.add_argument(
        "--preset-name",
        default=DEFAULT_VMIX_PRESET_NAME,
    )
    parser.add_argument(
        "--preset-path",
        type=Path,
        default=None,
        help="Exact preset .vmix path (overrides --preset-name search).",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_VMIX_API_BASE,
    )
    parser.add_argument(
        "--api-wait-sec",
        type=float,
        default=DEFAULT_API_WAIT_SEC,
        help="Seconds to wait for vMix HTTP API after launch.",
    )
    parser.add_argument(
        "--wait-sec",
        type=float,
        default=DEFAULT_PRESET_WAIT_SEC,
        help="Seconds to wait for preset load after OpenPreset.",
    )
    parser.add_argument(
        "--skip",
        action="store_true",
        help="Skip preset load (automation/CI).",
    )
    args = parser.parse_args()

    result = open_vmix_preset(
        preset_name=args.preset_name,
        preset_path=args.preset_path,
        api_base=args.api_base,
        api_wait_sec=args.api_wait_sec,
        wait_sec=args.wait_sec,
        skip=args.skip,
    )
    if result.preset_path:
        print(result.preset_path)
    if result.message:
        print(result.message, file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
