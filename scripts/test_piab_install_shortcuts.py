"""Tests for PIAB Desktop / Start Menu shortcut install."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS.parent
for _path in (_SCRIPTS, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from piab_install_shortcuts import (
    APP_USER_MODEL_ID,
    create_shortcut,
    install_shortcuts,
    pythonw_path,
    read_shortcut_app_user_model_id,
    read_shortcut_info,
    shortcut_path,
    uninstall_shortcuts,
)

from app.controller.paths import APP_ICON_ICO, REPO_ROOT


@unittest.skipUnless(sys.platform == "win32", "Windows shortcuts only")
class PiabInstallShortcutsTests(unittest.TestCase):
    def test_pythonw_prefers_pythonw_next_to_interpreter(self) -> None:
        launcher = pythonw_path()
        self.assertTrue(launcher.is_file())
        self.assertIn(launcher.name.lower(), {"pythonw.exe", "python.exe"})

    def test_installs_desktop_and_start_menu_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            desktop = root / "Desktop"
            start_menu = root / "Start Menu"
            written = install_shortcuts(desktop_dir=desktop, start_menu_dir=start_menu)
            self.assertEqual(
                written,
                [shortcut_path(desktop), shortcut_path(start_menu)],
            )
            for dest in written:
                self.assertTrue(dest.is_file())
                info = read_shortcut_info(dest)
                self.assertEqual(Path(info["target"]), pythonw_path())
                self.assertEqual(info["arguments"], "-m app.main")
                self.assertEqual(Path(info["working_dir"]), REPO_ROOT)
                self.assertTrue(info["icon"].lower().startswith(str(APP_ICON_ICO).lower()))
                self.assertEqual(read_shortcut_app_user_model_id(dest), APP_USER_MODEL_ID)
            removed = uninstall_shortcuts(desktop_dir=desktop, start_menu_dir=start_menu)
            self.assertEqual(removed, written)
            for dest in written:
                self.assertFalse(dest.exists())

    def test_create_shortcut_writes_icon_location(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw) / "Podcast in a Box.lnk"
            create_shortcut(
                dest,
                target=pythonw_path(),
                arguments="-m app.main",
                working_dir=REPO_ROOT,
                icon=APP_ICON_ICO,
            )
            info = read_shortcut_info(dest)
            self.assertTrue(info["icon"].lower().startswith(str(APP_ICON_ICO).lower()))
