#!/usr/bin/env python3
"""Launch the Podcast in a Box desktop app."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from app.controller import PiabController
from app.controller.paths import (
    APP_DISPLAY_NAME,
    APP_ORGANIZATION_NAME,
    ensure_scripts_path,
    migrate_legacy_work_files,
)
from app.gui.app_icon import apply_application_icon, apply_process_app_user_model_id
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

    apply_process_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_ORGANIZATION_NAME)
    apply_application_icon(app)

    controller = PiabController()
    ok, message = controller.acquire_app_lock(force=args.force_lock)
    if not ok:
        from PySide6.QtWidgets import QMessageBox

        from app.gui.dialogs import prompt_replace_running_instance

        existing = controller.lock.read()
        if prompt_replace_running_instance(
            None,
            message=message,
            recording_active=bool(existing and existing.recording_active),
        ):
            _killed, error = controller.lock.terminate_other_instances()
            if error:
                QMessageBox.critical(
                    None,
                    APP_DISPLAY_NAME,
                    f"Could not close the old copy.\n{error}",
                )
                return 1
            ok, message = controller.acquire_app_lock(force=True)
        if not ok:
            QMessageBox.warning(None, APP_DISPLAY_NAME, message)
            return 1

    manager = WindowManager(controller)
    manager.start()

    code = app.exec()
    controller.release_app_lock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
