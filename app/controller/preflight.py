"""Startup prerequisite checks."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from app.controller.paths import (
    DEFAULT_SCAN_ROOT,
    MIN_DISK_BYTES_FAIL,
    MIN_DISK_BYTES_WARN,
    ensure_scripts_path,
    find_vmix_preset,
)
from app.controller.types import PreflightCheck, PreflightReport


STORAGE_USAGE_HINT = "Need ~70GB per hour (~1.2GB/min)."


def _bytes_human(n: int) -> str:
    gib = n / (1024**3)
    return f"{gib:.1f} GB"


def check_ffmpeg(*, which: Callable[[str], str | None] = shutil.which) -> PreflightCheck:
    ffmpeg = which("ffmpeg")
    ffprobe = which("ffprobe")
    if ffmpeg and ffprobe:
        return PreflightCheck("ffmpeg", "ok", f"ffmpeg and ffprobe found ({ffmpeg}).")
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    return PreflightCheck(
        "ffmpeg",
        "fail",
        f"Missing on PATH: {', '.join(missing)}.",
        ("autocut",),
    )


def check_vmix_process(*, is_running: Callable[[], bool] | None = None) -> PreflightCheck:
    if sys.platform != "win32":
        return PreflightCheck("vmix", "ok", "vMix check skipped (non-Windows).")
    ensure_scripts_path()
    from piab_ensure_vmix import find_vmix_executable, is_vmix_running

    runner = is_running if is_running is not None else is_vmix_running
    if runner():
        return PreflightCheck("vmix", "ok", "vMix is running.")
    exe = find_vmix_executable()
    if exe is not None:
        return PreflightCheck(
            "vmix",
            "ok",
            f"vMix is not running; the app will launch it when you record.",
        )
    return PreflightCheck(
        "vmix",
        "fail",
        r"vMix not found under C:\Program Files (x86)\vMix (required for recording).",
        ("recording",),
    )


def check_vmix_api(
    *,
    ping: Callable[[], bool] | None = None,
    is_running: Callable[[], bool] | None = None,
) -> PreflightCheck:
    ensure_scripts_path()
    from piab_ensure_vmix import is_vmix_running as default_is_running

    runner = is_running if is_running is not None else default_is_running
    if not runner():
        return PreflightCheck(
            "vmix_api",
            "ok",
            "Not required until recording (vMix is not running).",
        )

    if ping is not None:
        ok = ping()
    else:
        from piab_vmix_api import wait_for_vmix_api

        ok = wait_for_vmix_api(timeout_sec=2.0)
    if ok:
        return PreflightCheck("vmix_api", "ok", "vMix HTTP API responded.")
    return PreflightCheck(
        "vmix_api",
        "fail",
        "vMix is running but the HTTP API is not reachable (127.0.0.1:8088).",
        ("recording",),
    )


def check_vmix_preset(*, preset_path: Path | None = None) -> PreflightCheck:
    path = preset_path if preset_path is not None else find_vmix_preset()
    if path is not None and path.is_file():
        return PreflightCheck("vmix_preset", "ok", f"Preset found: {path}")
    return PreflightCheck(
        "vmix_preset",
        "fail",
        f"PIAB vMix preset not found under {DEFAULT_SCAN_ROOT / 'vMix Configs'}.",
        ("recording",),
    )


def check_storage(
    root: Path = DEFAULT_SCAN_ROOT,
    *,
    disk_usage: Callable[[Path], tuple[int, int, int]] | None = None,
) -> PreflightCheck:
    usage_fn = disk_usage or shutil.disk_usage
    try:
        root.mkdir(parents=True, exist_ok=True)
        _total, _used, free = usage_fn(root)
    except OSError as exc:
        return PreflightCheck(
            "storage",
            "fail",
            f"Cannot access storage at {root}: {exc}",
            ("recording", "autocut"),
        )
    if free < MIN_DISK_BYTES_FAIL:
        return PreflightCheck(
            "storage",
            "fail",
            f"Critically low disk space at {root}: {_bytes_human(free)} free. {STORAGE_USAGE_HINT}",
            ("recording", "autocut"),
        )
    if free < MIN_DISK_BYTES_WARN:
        return PreflightCheck(
            "storage",
            "warn",
            f"Low disk space at {root}: {_bytes_human(free)} free (recommend ≥ 50 GB). {STORAGE_USAGE_HINT}",
            ("recording", "autocut"),
        )
    return PreflightCheck(
        "storage",
        "ok",
        f"{_bytes_human(free)} free at {root}. {STORAGE_USAGE_HINT}",
    )


def check_elevenlabs(*, read_key: Callable[[], str] | None = None) -> PreflightCheck:
    if read_key is not None:
        try:
            read_key()
            return PreflightCheck("elevenlabs", "ok", "ElevenLabs API key available.")
        except (FileNotFoundError, ValueError) as exc:
            return PreflightCheck(
                "elevenlabs",
                "fail",
                str(exc),
                ("autocut",),
            )

    ensure_scripts_path()
    from harness_env import load_harness_env
    from harness_episode_lib import find_elevenlabs_key_file, read_elevenlabs_api_key

    load_harness_env()

    env_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if env_key:
        return PreflightCheck("elevenlabs", "ok", "ELEVENLABS_API_KEY is set.")

    key_path = find_elevenlabs_key_file()
    if key_path is not None:
        try:
            read_elevenlabs_api_key()
        except ValueError as exc:
            return PreflightCheck("elevenlabs", "fail", str(exc), ("autocut",))
        return PreflightCheck(
            "elevenlabs",
            "ok",
            f"ElevenLabs key file found: {key_path}",
        )

    from harness_episode_lib import elevenlabs_key_file_candidates

    searched = "; ".join(str(path) for path in elevenlabs_key_file_candidates())
    return PreflightCheck(
        "elevenlabs",
        "fail",
        f"Missing ElevenLabs API key. Set ELEVENLABS_API_KEY or add a key file. Checked: {searched}",
        ("autocut",),
    )


def check_delivery_env(*, env: dict[str, str] | None = None) -> PreflightCheck:
    values = env if env is not None else dict(os.environ)
    smtp_ok = bool(values.get("HARNESS_SMTP_USER") and values.get("HARNESS_SMTP_PASSWORD"))
    frameio_ok = bool(
        values.get("FRAMEIO_ACCOUNT_ID")
        and values.get("FRAMEIO_PROJECT_ID")
        and values.get("FRAMEIO_UPLOAD_FOLDER_ID")
    )
    if smtp_ok and frameio_ok:
        return PreflightCheck("delivery", "ok", "SMTP and Frame.io env vars present.")
    missing = []
    if not smtp_ok:
        missing.append("Gmail SMTP (HARNESS_SMTP_*)")
    if not frameio_ok:
        missing.append("Frame.io IDs")
    return PreflightCheck(
        "delivery",
        "warn",
        f"Email delivery not fully configured: {', '.join(missing)}.",
        ("delivery",),
    )


def run_preflight(
    *,
    scan_root: Path = DEFAULT_SCAN_ROOT,
    check_vmix_api_enabled: bool = True,
    deps: dict | None = None,
) -> PreflightReport:
    ensure_scripts_path()
    from harness_env import load_harness_env

    load_harness_env()

    deps = deps or {}
    checks = [
        check_storage(scan_root, disk_usage=deps.get("disk_usage")),
        check_ffmpeg(which=deps.get("which", shutil.which)),
        check_vmix_process(is_running=deps.get("is_vmix_running")),
        check_vmix_preset(preset_path=deps.get("preset_path")),
        check_elevenlabs(read_key=deps.get("read_elevenlabs_key")),
        check_delivery_env(env=deps.get("env")),
    ]
    if check_vmix_api_enabled:
        checks.insert(
            3,
            check_vmix_api(
                ping=deps.get("vmix_api_ping"),
                is_running=deps.get("is_vmix_running"),
            ),
        )
    return PreflightReport(checks=checks)
