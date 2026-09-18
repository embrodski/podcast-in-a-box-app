"""Tests for Frame.io OAuth helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from frameio_oauth import (
    KEEP_ALIVE_MIN_INTERVAL_SEC,
    build_authorization_request,
    keep_frameio_oauth_alive,
    last_refreshed_at,
    parse_authorization_response,
    save_token_data,
)
from frameio_oauth_windows import KEEPALIVE_TASK_NAME, keepalive_task_command, keepalive_task_xml


class FrameioOAuthTests(unittest.TestCase):
    def test_build_authorization_request_contains_pkce(self) -> None:
        req = build_authorization_request(
            client_id="abc",
            redirect_uri="adobe+callback://adobeid/abc",
        )
        self.assertIn("code_challenge=", req.url)
        self.assertIn("client_id=abc", req.url)
        self.assertTrue(req.code_verifier)
        self.assertTrue(req.state)

    def test_parse_authorization_response_from_full_url(self) -> None:
        code, state = parse_authorization_response(
            "adobe+callback://adobeid/abc?code=XYZ123&state=abc"
        )
        self.assertEqual(code, "XYZ123")
        self.assertEqual(state, "abc")

    def test_parse_authorization_response_from_code_only(self) -> None:
        code, state = parse_authorization_response("XYZ123")
        self.assertEqual(code, "XYZ123")
        self.assertIsNone(state)

    def test_keep_alive_skips_missing_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            result = keep_frameio_oauth_alive(path)
            self.assertEqual(result.status, "skipped")
            self.assertIn("No saved", result.message)

    def test_keep_alive_skips_fresh_valid_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            now = 1_000.0 + KEEP_ALIVE_MIN_INTERVAL_SEC - 60
            save_token_data(
                {
                    "client_id": "abc",
                    "redirect_uri": "x",
                    "access_token": "a",
                    "refresh_token": "r",
                    "expires_at": now + 3600,
                    "refreshed_at": 1_000.0,
                },
                path,
            )
            called = {"n": 0}

            def _refresh(data):
                called["n"] += 1
                return data

            result = keep_frameio_oauth_alive(
                path,
                now=now,
                refresh=_refresh,
            )
            self.assertEqual(result.status, "skipped")
            self.assertEqual(called["n"], 0)

    def test_keep_alive_refreshes_valid_but_old_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            now = 1_000.0 + KEEP_ALIVE_MIN_INTERVAL_SEC + 120
            save_token_data(
                {
                    "client_id": "abc",
                    "redirect_uri": "x",
                    "access_token": "old",
                    "refresh_token": "r",
                    "expires_at": now + 3600,
                    "refreshed_at": 1_000.0,
                },
                path,
            )

            def _refresh(data):
                out = dict(data)
                out["access_token"] = "new"
                out["refreshed_at"] = now
                return out

            result = keep_frameio_oauth_alive(path, now=now, refresh=_refresh)
            self.assertEqual(result.status, "refreshed")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["access_token"], "new")

    def test_keep_alive_refreshes_stale_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            save_token_data(
                {
                    "client_id": "abc",
                    "redirect_uri": "x",
                    "access_token": "old",
                    "refresh_token": "r",
                    "expires_at": 1_100.0,
                    "refreshed_at": 1_000.0,
                },
                path,
            )

            def _refresh(data):
                out = dict(data)
                out["access_token"] = "new"
                out["refreshed_at"] = 1_000.0 + KEEP_ALIVE_MIN_INTERVAL_SEC + 120
                out["expires_at"] = out["refreshed_at"] + 3600
                return out

            result = keep_frameio_oauth_alive(
                path,
                now=1_000.0 + KEEP_ALIVE_MIN_INTERVAL_SEC + 120,
                refresh=_refresh,
            )
            self.assertEqual(result.status, "refreshed")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["access_token"], "new")

    def test_keep_alive_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            save_token_data(
                {
                    "client_id": "abc",
                    "redirect_uri": "x",
                    "access_token": "old",
                    "refresh_token": "r",
                    "expires_at": 0.0,
                    "refreshed_at": 1.0,
                },
                path,
            )

            def _refresh(_data):
                raise RuntimeError("Adobe token request failed (400): access_denied")

            result = keep_frameio_oauth_alive(path, now=10_000.0, refresh=_refresh)
            self.assertEqual(result.status, "failed")
            self.assertIn("access_denied", result.message)

    def test_last_refreshed_at_falls_back_to_expires_at(self) -> None:
        self.assertAlmostEqual(
            last_refreshed_at({"expires_at": 86_400.0}),
            60.0,
        )

    def test_keepalive_task_xml_is_weekly_and_points_at_script(self) -> None:
        xml = keepalive_task_xml(
            python_exe=Path(r"C:\Python\pythonw.exe"),
            repo_root=Path(r"E:\PodcastRoom\PodcastInABox"),
        )
        self.assertIn(KEEPALIVE_TASK_NAME, xml)
        self.assertIn("ScheduleByWeek", xml)
        self.assertIn("<Sunday />", xml)
        self.assertIn("StartWhenAvailable>true", xml)
        self.assertIn("keep-alive", xml)
        command, args, working = keepalive_task_command(
            python_exe=Path(r"C:\Python\pythonw.exe"),
            repo_root=Path(r"E:\PodcastRoom\PodcastInABox"),
        )
        self.assertTrue(command.endswith("pythonw.exe"))
        self.assertIn("harness_frameio_oauth.py", args)
        self.assertIn("keep-alive", args)
        self.assertEqual(working, r"E:\PodcastRoom\PodcastInABox")


if __name__ == "__main__":
    unittest.main()
