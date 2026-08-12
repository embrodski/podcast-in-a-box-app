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
    write_piab_output_transcripts,
)
from harness_email import SmtpConfig, send_delivery_success_email
from harness_piab_transcript import FULL_INTERVIEW_TRANSCRIPT_TXT


class DeliverVideoTests(unittest.TestCase):
    def _state(self, tmp: Path) -> dict:
        input_dir = tmp / "Input"
        input_dir.mkdir()
        transcript = input_dir / "Host Clean Audio-prepped Transcript.json"
        transcript.write_text("{}", encoding="utf-8")
        (input_dir / "Host Clean Audio-prepped Text.txt").write_text(
            "00:00:01,000 --> 00:00:02,000 [Speaker 0]\nHello.\n",
            encoding="utf-8",
        )
        output = tmp / "Output"
        temp = tmp / "Temp"
        output.mkdir()
        temp.mkdir()
        video = output / "Full Interview.mp4"
        video.write_bytes(b"fake-video")
        state = {
            "name": "Jessiah",
            "paths": {"output": str(output), "temp": str(temp)},
            "main_transcript_json": str(transcript),
            "delivery": {
                "enabled": True,
                "email": "guest@example.com",
                "email_confirmed_at": "2026-01-01T00:00:00+00:00",
            },
        }
        state.update(write_piab_output_transcripts(state, output))
        return state

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
            human_copy = Path(state["paths"]["output"]) / FULL_INTERVIEW_TRANSCRIPT_TXT
            self.assertTrue(transcript_copy.is_file())
            self.assertTrue(human_copy.is_file())

    @patch("harness_deliver_video.send_delivery_success_email")
    @patch("harness_deliver_video.upload_files_and_create_share")
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
            uploads=(
                FrameioUploadResult(
                    file_id="file-1",
                    file_name="Full Interview.mp4",
                    media_type="video/mp4",
                ),
                FrameioUploadResult(
                    file_id="file-2",
                    file_name="Full Interview Transcript.txt",
                    media_type="text/plain",
                ),
            ),
            share=FrameioShareResult(
                share_id="share-1",
                short_url="https://f.io/abc",
                name="Jessiah — Full Interview",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(Path(tmp))
            state["flag_timestamps"] = {
                "flag_report": (
                    "Flags Dropped At These Timestamps:\n00:06:23\n\n"
                    "Pause Flags At These Timestamps:\n-none-"
                )
            }
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
            self.assertEqual(payload["transcript_file_id"], "file-2")
            self.assertEqual(result["frameio"]["status"], "completed")
            mock_mail.assert_called_once()
            upload_paths = mock_upload.call_args.kwargs["file_paths"]
            self.assertEqual(len(upload_paths), 2)
            self.assertEqual(upload_paths[1].name, FULL_INTERVIEW_TRANSCRIPT_TXT)
            self.assertIn("00:06:23", mock_mail.call_args.kwargs["flag_report"])

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

    def test_success_email_includes_flag_report(self) -> None:
        captured: list = []

        def capture(_config: SmtpConfig, message) -> None:
            captured.append(message)

        config = SmtpConfig(
            host="smtp.test",
            port=587,
            user="u",
            password="p",
            sender="from@example.com",
        )
        send_delivery_success_email(
            config,
            to_addr="guest@example.com",
            episode_name="Jessiah",
            short_url="https://f.io/abc",
            flag_report=(
                "Flags Dropped At These Timestamps:\n00:01:00\n\n"
                "Pause Flags At These Timestamps:\n-none-"
            ),
            sender=capture,
        )
        body = captured[0].get_content()
        self.assertIn("Flags in the final edit:", body)
        self.assertIn("00:01:00", body)

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
