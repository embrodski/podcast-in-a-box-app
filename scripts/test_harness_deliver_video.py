"""Tests for harness delivery orchestration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frameio_client import FrameioDeliveryResult, FrameioShareResult, FrameioUploadResult
from harness_deliver_video import (
    FULL_INTERVIEW_DELIVERY_JSON,
    FULL_INTERVIEW_TRANSCRIPT_JSON,
    deliver_piab_full_interview,
    delivery_is_enabled,
    resolve_delivery_short_url,
    send_delivery_link_email,
)


class DeliverVideoTests(unittest.TestCase):
    def _state(self, tmp: Path) -> dict:
        transcript = tmp / "transcript.json"
        transcript.write_text("{}", encoding="utf-8")
        output = tmp / "Output"
        temp = tmp / "Temp"
        output.mkdir()
        temp.mkdir()
        video = output / "Full Interview.mp4"
        video.write_bytes(b"fake-video")
        return {
            "name": "Jessiah",
            "paths": {"output": str(output), "temp": str(temp)},
            "main_transcript_json": str(transcript),
            "delivery": {
                "enabled": True,
                "email": "guest@example.com",
                "email_confirmed_at": "2026-01-01T00:00:00+00:00",
            },
        }

    def test_delivery_disabled_skips(self) -> None:
        state = {"delivery": {"enabled": False}}
        result = deliver_piab_full_interview(
            state,
            video_path=Path("missing.mp4"),
        )
        self.assertFalse(delivery_is_enabled(state))
        self.assertEqual(result["frameio"]["status"], "pending")

    @patch("harness_deliver_video.SmtpConfig.from_env")
    @patch("harness_deliver_video.FrameioConfig.from_env")
    def test_dry_run(self, mock_frameio_env, mock_smtp_env) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(Path(tmp))
            result = deliver_piab_full_interview(
                state,
                video_path=Path(state["paths"]["output"]) / "Full Interview.mp4",
                dry_run=True,
                print_fn=lambda *_args, **_kwargs: None,
            )
            self.assertEqual(result["frameio"]["status"], "skipped")
            transcript_copy = Path(state["paths"]["output"]) / FULL_INTERVIEW_TRANSCRIPT_JSON
            self.assertTrue(transcript_copy.is_file())

    @patch("harness_deliver_video.send_delivery_success_email")
    @patch("harness_deliver_video.upload_file_and_create_share")
    @patch("harness_deliver_video.SmtpConfig.from_env")
    @patch("harness_deliver_video.FrameioConfig.from_env")
    def test_success_writes_output_json(
        self,
        mock_frameio_env,
        mock_smtp_env,
        mock_upload,
        mock_mail,
    ) -> None:
        mock_upload.return_value = FrameioDeliveryResult(
            upload=FrameioUploadResult(
                file_id="file-1",
                file_name="Full Interview.mp4",
                media_type="video/mp4",
            ),
            share=FrameioShareResult(
                share_id="share-1",
                short_url="https://f.io/abc",
                name="Jessiah — Full Interview",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(Path(tmp))
            video = Path(state["paths"]["output"]) / "Full Interview.mp4"
            result = deliver_piab_full_interview(
                state,
                video_path=video,
                print_fn=lambda *_args, **_kwargs: None,
            )
            delivery_json = Path(state["paths"]["output"]) / FULL_INTERVIEW_DELIVERY_JSON
            payload = json.loads(delivery_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["short_url"], "https://f.io/abc")
            self.assertEqual(payload["recipient_email"], "guest@example.com")
            self.assertEqual(result["frameio"]["status"], "completed")
            mock_mail.assert_called_once()

    @patch("harness_deliver_video.send_delivery_success_email")
    @patch("harness_deliver_video.SmtpConfig.from_env")
    def test_send_link_email_reuses_short_url(self, mock_smtp_env, mock_mail) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(Path(tmp))
            state["delivery"]["frameio"] = {
                "status": "completed",
                "short_url": "https://f.io/abc",
            }
            recipient = send_delivery_link_email(
                state,
                to_addr="Other@Example.com",
                print_fn=lambda *_args, **_kwargs: None,
            )
            self.assertEqual(recipient, "other@example.com")
            self.assertIn("other@example.com", state["delivery"]["additional_emails"])
            mock_mail.assert_called_once()
            _args, kwargs = mock_mail.call_args
            self.assertEqual(kwargs["to_addr"], "other@example.com")
            self.assertEqual(kwargs["short_url"], "https://f.io/abc")

    def test_resolve_short_url_from_output_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(Path(tmp))
            output = Path(state["paths"]["output"])
            record = {
                "short_url": "https://f.io/from-json",
                "recipient_email": "guest@example.com",
            }
            (output / FULL_INTERVIEW_DELIVERY_JSON).write_text(
                json.dumps(record),
                encoding="utf-8",
            )
            state["delivery"] = {"enabled": True, "email": "guest@example.com"}
            self.assertEqual(resolve_delivery_short_url(state), "https://f.io/from-json")


if __name__ == "__main__":
    unittest.main()
