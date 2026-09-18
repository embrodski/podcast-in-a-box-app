"""Hide Windows console windows spawned by ffmpeg, ffprobe, and helper scripts.

Console-subsystem tools (ffmpeg.exe, ffprobe.exe, tasklist, etc.) open a visible
console when launched from a GUI or pythonw parent. That steals focus. OR in
CREATE_NO_WINDOW so they stay in the background.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def hidden_console_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    return {"creationflags": CREATE_NO_WINDOW}


def merge_hidden_console_kwargs(kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(kwargs or {})
    extra = hidden_console_kwargs()
    if not extra:
        return merged
    existing = int(merged.get("creationflags", 0) or 0)
    merged["creationflags"] = existing | extra["creationflags"]
    return merged


def install_hidden_console() -> None:
    """Patch subprocess.run/Popen in this process so child consoles stay hidden."""
    if getattr(subprocess, "_piab_hidden_console", False):
        return
    if sys.platform != "win32":
        subprocess._piab_hidden_console = True  # type: ignore[attr-defined]
        return

    original_run = subprocess.run
    original_popen = subprocess.Popen

    def run(*args: Any, **kwargs: Any):
        return original_run(*args, **merge_hidden_console_kwargs(kwargs))

    class Popen(original_popen):  # type: ignore[valid-type,misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **merge_hidden_console_kwargs(kwargs))

    subprocess.run = run  # type: ignore[assignment]
    subprocess.Popen = Popen  # type: ignore[misc,assignment]
    subprocess._piab_hidden_console = True  # type: ignore[attr-defined]
