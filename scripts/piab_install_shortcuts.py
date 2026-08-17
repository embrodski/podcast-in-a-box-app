#!/usr/bin/env python3
"""Create Desktop and Start Menu shortcuts for the PIAB desktop app.

Shortcuts launch pythonw.exe (no console) with assets/piab.ico.
Keep run_piab_app.bat for debugging; it is not replaced.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.controller.paths import (  # noqa: E402
    APP_DISPLAY_NAME,
    APP_ICON_ICO,
    APP_USER_MODEL_ID,
    REPO_ROOT as APP_REPO_ROOT,
)

SHORTCUT_FILENAME = f"{APP_DISPLAY_NAME}.lnk"
APP_ID_CS = Path(__file__).resolve().parent / "piab_shortcut_appid.cs"


def default_desktop_dir() -> Path:
    return Path.home() / "Desktop"


def default_start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot find the Start Menu folder.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def pythonw_path(executable: Path | None = None) -> Path:
    exe = Path(executable or sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.is_file():
        return candidate
    return exe


def shortcut_path(folder: Path) -> Path:
    return folder / SHORTCUT_FILENAME


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_powershell(command: str) -> str:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"PowerShell failed ({completed.returncode}): {detail}")
    return (completed.stdout or "").strip()


def create_shortcut(
    dest: Path,
    *,
    target: Path,
    arguments: str,
    working_dir: Path,
    icon: Path,
    description: str = APP_DISPLAY_NAME,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(dest))}); "
        f"$s.TargetPath = {_ps_quote(str(target))}; "
        f"$s.Arguments = {_ps_quote(arguments)}; "
        f"$s.WorkingDirectory = {_ps_quote(str(working_dir))}; "
        f"$s.IconLocation = {_ps_quote(f'{icon},0')}; "
        f"$s.Description = {_ps_quote(description)}; "
        f"$s.WindowStyle = 1; "
        f"$s.Save()"
    )
    _run_powershell(command)
    return dest


def _app_id_command(method: str, dest: Path, app_id: str | None = None) -> str:
    args = _ps_quote(str(dest))
    if app_id is not None:
        args += f", {_ps_quote(app_id)}"
    return (
        f"Add-Type -TypeDefinition (Get-Content -Raw {_ps_quote(str(APP_ID_CS))}); "
        f"[PiabShortcutAppId]::{method}({args})"
    )


def set_shortcut_app_user_model_id(
    dest: Path,
    app_id: str = APP_USER_MODEL_ID,
) -> None:
    _run_powershell(_app_id_command("Set", dest, app_id))


def read_shortcut_app_user_model_id(dest: Path) -> str:
    return _run_powershell(_app_id_command("Get", dest))


def read_shortcut_info(dest: Path) -> dict[str, str]:
    command = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(dest))}); "
        "$s.TargetPath; $s.Arguments; $s.WorkingDirectory; $s.IconLocation"
    )
    lines = [line.strip() for line in _run_powershell(command).splitlines() if line.strip()]
    if len(lines) < 4:
        raise RuntimeError(f"Could not read shortcut properties from {dest}")
    return {
        "target": lines[0],
        "arguments": lines[1],
        "working_dir": lines[2],
        "icon": lines[3],
    }


def install_shortcuts(
    *,
    desktop_dir: Path | None = None,
    start_menu_dir: Path | None = None,
    pythonw: Path | None = None,
    repo_root: Path = APP_REPO_ROOT,
    icon: Path = APP_ICON_ICO,
    app_id: str = APP_USER_MODEL_ID,
) -> list[Path]:
    if sys.platform != "win32":
        raise RuntimeError("PIAB shortcuts are Windows-only.")
    if not icon.is_file():
        raise FileNotFoundError(f"App icon not found: {icon}")
    launcher = pythonw_path(pythonw)
    written: list[Path] = []
    for folder in (desktop_dir or default_desktop_dir(), start_menu_dir or default_start_menu_dir()):
        dest = shortcut_path(folder)
        create_shortcut(
            dest,
            target=launcher,
            arguments="-m app.main",
            working_dir=repo_root,
            icon=icon,
        )
        set_shortcut_app_user_model_id(dest, app_id)
        written.append(dest)
    return written


def uninstall_shortcuts(
    *,
    desktop_dir: Path | None = None,
    start_menu_dir: Path | None = None,
) -> list[Path]:
    removed: list[Path] = []
    for folder in (desktop_dir or default_desktop_dir(), start_menu_dir or default_start_menu_dir()):
        dest = shortcut_path(folder)
        if dest.is_file():
            dest.unlink()
            removed.append(dest)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the Desktop and Start Menu shortcuts.",
    )
    args = parser.parse_args(argv)
    if args.uninstall:
        removed = uninstall_shortcuts()
        if not removed:
            print("No PIAB shortcuts found.")
            return 0
        for path in removed:
            print(f"removed: {path}")
        return 0
    written = install_shortcuts()
    for path in written:
        print(f"installed: {path}")
    print(f"launcher: {pythonw_path()}")
    print(f"icon: {APP_ICON_ICO}")
    print("run_piab_app.bat is unchanged (console debug launcher).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
