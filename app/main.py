#!/usr/bin/env python3
"""Launch the Podcast in a Box desktop app."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app.controller import PiabController
from app.controller.paths import ensure_scripts_path, migrate_legacy_work_files
from app.gui.window_manager import WindowManager


def _load_secrets() -> None:
    ensure_scripts_path()
    from harness_env import load_harness_env

    load_harness_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Podcast in a Box desktop app.")
    parser.add_argument(
        "--force-lock",
        action="store_true",
        help="Replace a stale singleton lock file.",
    )
    args = parser.parse_args(argv)

    _load_secrets()
    migrate_legacy_work_files()

    controller = PiabController()
    ok, message = controller.acquire_app_lock(force=args.force_lock)
    if not ok:
        print(message, file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Podcast in a Box")
    app.setOrganizationName("Lighthaven")

    manager = WindowManager(controller)
    manager.start()

    code = app.exec()
    controller.release_app_lock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
