"""Tests for PIAB vMix preset opener."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piab_open_vmix_preset import (
    current_vmix_preset_path,
    find_vmix_preset,
    normalize_preset_name,
    open_vmix_preset,
)


class PiabOpenVmixPresetTests(unittest.TestCase):
    def test_normalize_preset_name_fixes_space_before_extension(self) -> None:
        names = normalize_preset_name("4 People - 5 Cameras - Default .vmix")
        self.assertIn("4 People - 5 Cameras - Default.vmix", names)

    def test_find_vmix_preset_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preset = root / "4 People - 5 Cameras - Default.vmix"
            preset.write_text("preset", encoding="utf-8")
            found = find_vmix_preset(
                "4 People - 5 Cameras - Default .vmix",
                search_dirs=(root,),
            )
            self.assertEqual(found, preset.resolve())

    def test_current_vmix_preset_path_parses_xml(self) -> None:
        xml = (
            "<vmix><preset>E:\\PodcastRoom\\vMix Configs\\"
            "4 People - 5 Cameras - Default.vmix</preset></vmix>"
        )
        path = current_vmix_preset_path(fetch_xml=lambda **_kwargs: xml)
        self.assertTrue(path.endswith("Default.vmix"))

    def test_open_vmix_preset_already_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preset = Path(tmp) / "4 People - 5 Cameras - Default.vmix"
            preset.write_text("preset", encoding="utf-8")
            result = open_vmix_preset(
                preset_path=preset,
                fetch_xml=lambda **_kwargs: (
                    f"<vmix><preset>{preset.resolve()}</preset></vmix>"
                ),
                print_fn=lambda *_args, **_kwargs: None,
            )
        self.assertEqual(result.status, "already_open")
        self.assertTrue(result.ok)

    @patch("piab_ensure_vmix.ensure_vmix_running")
    @patch("piab_open_vmix_preset.wait_for_vmix_api", return_value=True)
    @patch("piab_open_vmix_preset.wait_for_vmix_preset", return_value=True)
    def test_open_vmix_preset_calls_api(
        self, _mock_preset_wait, _mock_api_wait, mock_ensure
    ) -> None:
        mock_ensure.return_value.ok = True
        mock_ensure.return_value.message = ""
        with tempfile.TemporaryDirectory() as tmp:
            preset = Path(tmp) / "4 People - 5 Cameras - Default.vmix"
            preset.write_text("preset", encoding="utf-8")
            calls: list[str] = []

            def fake_request(url: str, *, timeout_sec: float) -> None:
                calls.append(url)

            result = open_vmix_preset(
                preset_path=preset,
                fetch_xml=lambda **_kwargs: "<vmix><preset></preset></vmix>",
                request_fn=fake_request,
                print_fn=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(result.status, "opened")
        self.assertTrue(calls)
        self.assertIn("Function=OpenPreset", calls[0])
        mock_ensure.assert_called()

    @patch("piab_ensure_vmix.launch_vmix")
    @patch("piab_ensure_vmix.find_vmix_executable")
    @patch("piab_ensure_vmix.ensure_vmix_running")
    def test_open_vmix_preset_relaunches_when_api_is_down(
        self, mock_ensure, mock_find, mock_launch
    ) -> None:
        mock_ensure.return_value.ok = True
        mock_ensure.return_value.message = ""
        mock_find.return_value = Path(r"C:\Program Files (x86)\vMix\vMix64.exe")
        api_calls = {"n": 0}

        def fake_wait(*, api_base, timeout_sec, fetch_xml):
            api_calls["n"] += 1
            return api_calls["n"] >= 2

        with tempfile.TemporaryDirectory() as tmp:
            preset = Path(tmp) / "4 People - 5 Cameras - Default.vmix"
            preset.write_text("preset", encoding="utf-8")
            with (
                patch("piab_open_vmix_preset.wait_for_vmix_api", side_effect=fake_wait),
                patch("piab_open_vmix_preset.wait_for_vmix_preset", return_value=True),
            ):
                result = open_vmix_preset(
                    preset_path=preset,
                    fetch_xml=lambda **_kwargs: "<vmix><preset></preset></vmix>",
                    request_fn=lambda *_args, **_kwargs: None,
                    print_fn=lambda *_args, **_kwargs: None,
                )

        self.assertEqual(result.status, "opened")
        mock_launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
