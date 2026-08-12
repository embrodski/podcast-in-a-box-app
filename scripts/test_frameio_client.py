"""Tests for Frame.io client helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frameio_client import (
    FrameioConfig,
    create_local_upload,
    poll_upload_complete,
    sanitize_frameio_error,
    upload_file_chunks,
    upload_file_and_create_share,
)


class FrameioClientTests(unittest.TestCase):
    def test_sanitize_frameio_error_hides_presigned_url(self) -> None:
        raw = "failed https://bucket.s3.amazonaws.com/key?sig=secret"
        self.assertEqual(
            sanitize_frameio_error(raw),
            "Upload request failed.",
        )

    def test_sanitize_frameio_error_json_detail(self) -> None:
        raw = json.dumps({"errors": [{"detail": "Folder not found"}]})
        self.assertIn("Folder not found", sanitize_frameio_error(raw))

    def test_upload_file_chunks_puts_each_part(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"abcdef")
            tmp_path = Path(tmp.name)
        try:
            calls: list[tuple[str, bytes, dict[str, str]]] = []

            def fake_put(url: str, data: bytes, headers: dict[str, str]) -> None:
                calls.append((url, data, headers))

            upload_file_chunks(
                tmp_path,
                upload_urls=[
                    {"size": 3, "url": "https://upload.example/1"},
                    {"size": 3, "url": "https://upload.example/2"},
                ],
                media_type="video/mp4",
                put_bytes=fake_put,
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][1], b"abc")
            self.assertEqual(calls[1][1], b"def")
            self.assertEqual(calls[0][2]["x-amz-acl"], "private")
        finally:
            tmp_path.unlink(missing_ok=True)

    @patch("frameio_client._api_request")
    def test_create_local_upload(self, mock_api) -> None:
        mock_api.return_value = {
            "data": {
                "id": "file-1",
                "media_type": "video/mp4",
                "upload_urls": [{"size": 10, "url": "https://upload.example/1"}],
            }
        }
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"0123456789")
            tmp_path = Path(tmp.name)
        try:
            config = FrameioConfig(
                access_token="token",
                account_id="acct",
                project_id="proj",
                upload_folder_id="folder",
            )
            data = create_local_upload(config, file_path=tmp_path)
            self.assertEqual(data["id"], "file-1")
        finally:
            tmp_path.unlink(missing_ok=True)

    @patch("frameio_client._api_request")
    def test_poll_upload_complete(self, mock_api) -> None:
        mock_api.side_effect = [
            {"data": {"upload_complete": False, "upload_failed": False}},
            {"data": {"upload_complete": True, "upload_failed": False}},
        ]
        config = FrameioConfig(
            access_token="token",
            account_id="acct",
            project_id="proj",
            upload_folder_id="folder",
        )
        poll_upload_complete(
            config,
            file_id="file-1",
            poll_interval_sec=0,
            sleep_fn=lambda _sec: None,
        )
        self.assertEqual(mock_api.call_count, 2)

    @patch("frameio_client.create_public_share")
    @patch("frameio_client.poll_upload_complete")
    @patch("frameio_client.upload_file_chunks")
    @patch("frameio_client.create_local_upload")
    def test_upload_files_and_create_share(
        self,
        mock_create,
        mock_upload,
        mock_poll,
        mock_share,
    ) -> None:
        mock_create.side_effect = [
            {
                "id": "file-1",
                "media_type": "video/mp4",
                "upload_urls": [{"size": 4, "url": "https://upload.example/1"}],
            },
            {
                "id": "file-2",
                "media_type": "text/plain",
                "upload_urls": [{"size": 4, "url": "https://upload.example/2"}],
            },
        ]
        mock_share.return_value = type(
            "Share",
            (),
            {"share_id": "share-1", "short_url": "https://f.io/abc", "name": "Test"},
        )()
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "video.mp4"
            transcript = Path(td) / "transcript.txt"
            video.write_bytes(b"data")
            transcript.write_bytes(b"txt")
            config = FrameioConfig(
                access_token="token",
                account_id="acct",
                project_id="proj",
                upload_folder_id="folder",
            )
            from frameio_client import upload_files_and_create_share

            result = upload_files_and_create_share(
                config,
                file_paths=[video, transcript],
                share_name="Episode",
            )
            self.assertEqual(len(result.uploads), 2)
            self.assertEqual(result.upload.file_id, "file-1")
            self.assertEqual(result.share.short_url, "https://f.io/abc")
            self.assertEqual(mock_upload.call_count, 2)
            self.assertEqual(mock_poll.call_count, 2)
            share_kwargs = mock_share.call_args.kwargs
            self.assertEqual(share_kwargs["asset_ids"], ["file-1", "file-2"])

    @patch("frameio_client.poll_upload_complete")
    @patch("frameio_client.upload_file_chunks")
    @patch("frameio_client.create_local_upload")
    @patch("frameio_client.create_public_share")
    def test_upload_file_and_create_share(
        self,
        mock_share,
        mock_create,
        mock_upload,
        mock_poll,
    ) -> None:
        mock_create.return_value = {
            "id": "file-1",
            "media_type": "video/mp4",
            "upload_urls": [{"size": 4, "url": "https://upload.example/1"}],
        }
        mock_share.return_value = type(
            "Share",
            (),
            {"share_id": "share-1", "short_url": "https://f.io/abc", "name": "Test"},
        )()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = Path(tmp.name)
        try:
            config = FrameioConfig(
                access_token="token",
                account_id="acct",
                project_id="proj",
                upload_folder_id="folder",
            )
            result = upload_file_and_create_share(config, file_path=tmp_path)
            self.assertEqual(result.upload.file_id, "file-1")
            self.assertEqual(len(result.uploads), 1)
            self.assertEqual(result.share.short_url, "https://f.io/abc")
            mock_upload.assert_called_once()
            mock_poll.assert_called_once()
            mock_share.assert_called_once()
            self.assertEqual(mock_share.call_args.kwargs["asset_ids"], ["file-1"])
        finally:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
