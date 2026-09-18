"""Ensure vMix is running before Podcast In A Box starts."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VMIX_INSTALL_DIR = Path(r"C:\Program Files (x86)\vMix")
DEFAULT_VMIX_EXECUTABLES = ("vMix64.exe", "vMix.exe")
DEFAULT_VMIX_PROCESS_NAMES = ("vMix64.exe", "vMix.exe")
DEFAULT_HELP_IMAGE = REPO_ROOT / "assets" / "piab-vmix-icon-help.png"
DEFAULT_STARTUP_WAIT_SEC = 90.0


@dataclass(frozen=True)
class VmixEnsureResult:
    status: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"already_running", "launched", "skipped"}


def is_vmix_running(*, process_names: tuple[str, ...] = DEFAULT_VMIX_PROCESS_NAMES) -> bool:
    if sys.platform != "win32":
        return True
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in process_names:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=creationflags,
        )
        stdout = proc.stdout or ""
        if name.lower() in stdout.lower() and "no tasks are running" not in stdout.lower():
            return True
    return False


def find_vmix_executable(
    install_dir: Path = DEFAULT_VMIX_INSTALL_DIR,
    *,
    executable_names: tuple[str, ...] = DEFAULT_VMIX_EXECUTABLES,
) -> Path | None:
    for name in executable_names:
        path = install_dir / name
        if path.is_file():
            return path
    return None


def launch_vmix(
    executable: Path,
    *,
    launch_fn=None,
    is_running_fn=None,
) -> None:
    # vMix is not single-instance. startfile/exe again opens a second window
    # that does not share the first copy's preset or (sometimes) HTTP API.
    checker = is_running_fn if is_running_fn is not None else is_vmix_running
    if checker():
        return
    launcher = launch_fn or _default_launch
    launcher(executable)


def _default_launch(executable: Path) -> None:
    # Explorer-style launch so vMix gets a normal visible window. subprocess.Popen
    # from pythonw (desktop shortcut) can start a process with no UI / no HTTP API.
    if sys.platform == "win32":
        import os

        os.startfile(str(executable))  # noqa: S606
        return
    subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
    )


def show_vmix_help_image(image_path: Path, *, open_fn=None) -> None:
    if not image_path.is_file():
        return
    opener = open_fn or _default_open_image
    opener(image_path)


def _default_open_image(image_path: Path) -> None:
    if sys.platform == "win32":
        import os

        os.startfile(str(image_path))  # noqa: S606
        return
    subprocess.run(["xdg-open", str(image_path)], check=False)


def ensure_vmix_running(
    *,
    install_dir: Path = DEFAULT_VMIX_INSTALL_DIR,
    help_image: Path = DEFAULT_HELP_IMAGE,
    startup_wait_sec: float = DEFAULT_STARTUP_WAIT_SEC,
    poll_sec: float = 1.0,
    skip: bool = False,
    is_running_fn=is_vmix_running,
    launch_fn=None,
    open_image_fn=None,
    print_fn=print,
) -> VmixEnsureResult:
    if skip:
        return VmixEnsureResult(status="skipped")

    if sys.platform != "win32":
        return VmixEnsureResult(
            status="skipped",
            message="vMix check skipped (Windows only).",
        )

    if is_running_fn():
        return VmixEnsureResult(status="already_running")

    executable = find_vmix_executable(install_dir)
    if executable is None:
        print_fn("Please open vMix")
        show_vmix_help_image(help_image, open_fn=open_image_fn)
        return VmixEnsureResult(
            status="manual_required",
            message=f"vMix executable not found in {install_dir}",
        )

    print_fn("Opening vMix")
    launch_vmix(executable, launch_fn=launch_fn, is_running_fn=is_running_fn)

    deadline = time.time() + startup_wait_sec
    while time.time() < deadline:
        if is_running_fn():
            return VmixEnsureResult(status="launched")
        time.sleep(poll_sec)

    print_fn("Please open vMix")
    show_vmix_help_image(help_image, open_fn=open_image_fn)
    return VmixEnsureResult(
        status="manual_required",
        message="vMix did not start within the wait window.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ensure vMix is running before Podcast In A Box.",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=DEFAULT_VMIX_INSTALL_DIR,
    )
    parser.add_argument(
        "--help-image",
        type=Path,
        default=DEFAULT_HELP_IMAGE,
    )
    parser.add_argument(
        "--startup-wait-sec",
        type=float,
        default=DEFAULT_STARTUP_WAIT_SEC,
    )
    parser.add_argument(
        "--skip",
        action="store_true",
        help="Skip the vMix check (automation/CI).",
    )
    args = parser.parse_args()

    result = ensure_vmix_running(
        install_dir=args.install_dir,
        help_image=args.help_image,
        startup_wait_sec=args.startup_wait_sec,
        skip=args.skip,
    )
    if result.message:
        print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
