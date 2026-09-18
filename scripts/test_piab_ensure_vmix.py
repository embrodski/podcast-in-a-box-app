"""Tests for PIAB vMix startup helper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piab_ensure_vmix import (
    ensure_vmix_running,
    find_vmix_executable,
    is_vmix_running,
    launch_vmix,
)


class PiabEnsureVmixTests(unittest.TestCase):
    def test_find_vmix_executable_prefers_vmix64(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            (install / "vMix.exe").write_text("", encoding="utf-8")
            (install / "vMix64.exe").write_text("", encoding="utf-8")
            found = find_vmix_executable(install)
            self.assertEqual(found, install / "vMix64.exe")

    def test_ensure_already_running(self) -> None:
        result = ensure_vmix_running(skip=False, is_running_fn=lambda: True)
        self.assertEqual(result.status, "already_running")
        self.assertTrue(result.ok)

    def test_ensure_launches_and_waits(self) -> None:
        calls = {"running": False}

        def fake_running() -> bool:
            return calls["running"]

        def fake_launch(_exe: Path) -> None:
            calls["running"] = True

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            exe = install / "vMix64.exe"
            exe.write_text("", encoding="utf-8")
            result = ensure_vmix_running(
                install_dir=install,
                help_image=install / "missing.png",
                startup_wait_sec=1.0,
                poll_sec=0.01,
                is_running_fn=fake_running,
                launch_fn=fake_launch,
                print_fn=lambda *_args, **_kwargs: None,
            )
        self.assertEqual(result.status, "launched")
        self.assertTrue(result.ok)

    def test_ensure_manual_required_when_launch_fails(self) -> None:
        opened: list[Path] = []

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            exe = install / "vMix64.exe"
            exe.write_text("", encoding="utf-8")
            image = install / "help.png"
            image.write_text("", encoding="utf-8")
            messages: list[str] = []

            def capture(msg: str) -> None:
                messages.append(msg)

            result = ensure_vmix_running(
                install_dir=install,
                help_image=image,
                startup_wait_sec=0.05,
                poll_sec=0.01,
                is_running_fn=lambda: False,
                launch_fn=lambda _exe: None,
                open_image_fn=lambda path: opened.append(path),
                print_fn=capture,
            )

        self.assertEqual(result.status, "manual_required")
        self.assertFalse(result.ok)
        self.assertIn("Please open vMix", messages)
        self.assertEqual(opened, [image])

    @patch("piab_ensure_vmix.subprocess.run")
    def test_is_vmix_running_detects_process(self, mock_run) -> None:
        mock_run.return_value.stdout = "vMix64.exe   123 Console"
        self.assertTrue(is_vmix_running())

    @patch("piab_ensure_vmix.sys.platform", "win32")
    def test_default_launch_uses_startfile(self) -> None:
        from piab_ensure_vmix import _default_launch

        exe = Path(r"C:\Program Files (x86)\vMix\vMix64.exe")
        with patch("os.startfile") as startfile:
            _default_launch(exe)
        startfile.assert_called_once_with(str(exe))

    def test_launch_vmix_is_noop_when_already_running(self) -> None:
        launched: list[Path] = []
        exe = Path(r"C:\Program Files (x86)\vMix\vMix64.exe")
        launch_vmix(
            exe,
            launch_fn=lambda path: launched.append(path),
            is_running_fn=lambda: True,
        )
        self.assertEqual(launched, [])


if __name__ == "__main__":
    unittest.main()
