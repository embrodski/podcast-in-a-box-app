"""Windows app identity and window icon for the PIAB desktop app."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.controller.paths import APP_ICON_ICO, APP_USER_MODEL_ID


def apply_process_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """Tell Windows this process is PIAB, not python.exe. Call before QApplication."""
    if sys.platform != "win32":
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def apply_application_icon(
    app: QApplication,
    icon_path: Path = APP_ICON_ICO,
) -> bool:
    """Set the title-bar / Alt+Tab / taskbar icon for every window."""
    if not icon_path.is_file():
        return False
    app.setWindowIcon(QIcon(str(icon_path)))
    return True
