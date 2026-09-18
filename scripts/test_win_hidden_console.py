"""Tests for hidden Windows console flags."""

from __future__ import annotations

import subprocess
import sys
import unittest

from win_hidden_console import (
    CREATE_NO_WINDOW,
    hidden_console_kwargs,
    merge_hidden_console_kwargs,
)


class WinHiddenConsoleTests(unittest.TestCase):
    def test_hidden_kwargs_on_windows(self) -> None:
        kwargs = hidden_console_kwargs()
        if sys.platform == "win32":
            self.assertEqual(kwargs.get("creationflags"), CREATE_NO_WINDOW)
            self.assertTrue(CREATE_NO_WINDOW)
        else:
            self.assertEqual(kwargs, {})

    def test_merge_preserves_existing_flags(self) -> None:
        merged = merge_hidden_console_kwargs({"creationflags": 0x10, "cwd": "."})
        self.assertEqual(merged["cwd"], ".")
        if sys.platform == "win32":
            self.assertTrue(merged["creationflags"] & 0x10)
            self.assertTrue(merged["creationflags"] & CREATE_NO_WINDOW)
        else:
            self.assertEqual(merged["creationflags"], 0x10)

    def test_merge_adds_flag_when_missing(self) -> None:
        merged = merge_hidden_console_kwargs({})
        if sys.platform == "win32":
            self.assertTrue(merged["creationflags"] & CREATE_NO_WINDOW)
        else:
            self.assertNotIn("creationflags", merged)

    def test_create_no_window_matches_stdlib(self) -> None:
        if sys.platform == "win32":
            self.assertEqual(CREATE_NO_WINDOW, subprocess.CREATE_NO_WINDOW)

    def test_install_is_idempotent(self) -> None:
        from win_hidden_console import install_hidden_console

        install_hidden_console()
        first_run = subprocess.run
        first_popen = subprocess.Popen
        install_hidden_console()
        self.assertIs(subprocess.run, first_run)
        self.assertIs(subprocess.Popen, first_popen)


if __name__ == "__main__":
    unittest.main()
