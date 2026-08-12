"""Frame.io v4 upload + public share helpers for harness delivery."""

from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

FRAMEIO_API_BASE = "https://api.frame.io/v4"
FRAMEIO_USER_AGENT = "LighthavenPodcastInABox/1.0"


@dataclass(frozen=True)
class FrameioConfig:
    access_token: str
    account_id: str
    project_id: str
    upload_folder_id: str

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> FrameioConfig:
        env = environ if environ is not None else os.environ
        access_token = str(env.get("FRAMEIO_ACCESS_TOKEN") or "").strip()
        if not access_token:
            from frameio_oauth import get_valid_access_token

            access_token = get_valid_access_token() or ""
        missing = [
            name
            for name, key, value in (
                ("FRAMEIO_ACCESS_TOKEN", "access_token", access_token),
                ("FRAMEIO_ACCOUNT_ID", "account_id", env.get("FRAMEIO_ACCOUNT_ID")),
                ("FRAMEIO_PROJECT_ID", "project_id", env.get("FRAMEIO_PROJECT_ID")),
                (
                    "FRAMEIO_UPLOAD_FOLDER_ID",
                    "upload_folder_id",
                    env.get("FRAMEIO_UPLOAD_FOLDER_ID"),
                ),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise ValueError(
                "Missing Frame.io configuration: " + ", ".join(missing)
            )
        return cls(
            access_token=access_token,
            account_id=str(env["FRAMEIO_ACCOUNT_ID"]).strip(),
            project_id=str(env["FRAMEIO_PROJECT_ID"]).strip(),
            upload_folder_id=str(env["FRAMEIO_UPLOAD_FOLDER_ID"]).strip(),
        )


@dataclass(frozen=True)
class FrameioUploadResult:
    file_id: str
    file_name: str
    media_type: str


@dataclass(frozen=True)
class FrameioShareResult:
    share_id: str
    short_url: str
    name: str


@dataclass(frozen=True)
class FrameioDeliveryResult:
    uploads: tuple[FrameioUploadResult, ...]
    share: FrameioShareResult

    @property
    def upload(self) -> FrameioUploadResult:
        """Primary (first) uploaded file — usually the interview video."""
        if not self.uploads:
            raise RuntimeError("Frame.io delivery has no uploads.")
        return self.uploads[0]


def _api_request(
    method: str,
    url: str,
    *,
    token: str,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    req_headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": FRAMEIO_USER_AGENT,
    }
    payload: bytes | None = data
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=payload,
        headers=req_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Frame.io API {method} {url} failed ({exc.code}): "
            f"{sanitize_frameio_error(detail)}"
        ) from exc
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Frame.io API returned unexpected JSON.")
    return parsed


def sanitize_frameio_error(raw: str) -> str:
    """Return a user-safe error string (no tokens or presigned URLs)."""
    text = str(raw or "").strip()
    if not text:
        return "Unknown Frame.io error."
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if "http" in text.lower() and "amazonaws.com" in text.lower():
            return "Upload request failed."
        return text[:500]
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        parts: list[str] = []
        for item in errors:
            if isinstance(item, dict) and item.get("detail"):
                parts.append(str(item["detail"]))
        if parts:
            return "; ".join(parts)[:500]
    return text[:500]


def create_local_upload(
    config: FrameioConfig,
    *,
    file_path: Path,
) -> dict[str, Any]:
    file_path = file_path.resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Upload file not found: {file_path}")
    body = {
        "data": {
            "file_size": file_path.stat().st_size,
            "name": file_path.name,
        }
    }
    url = (
        f"{FRAMEIO_API_BASE}/accounts/{config.account_id}/folders/"
        f"{config.upload_folder_id}/files/local_upload"
    )
    response = _api_request("POST", url, token=config.access_token, body=body)
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Frame.io local_upload response missing data.")
    return data


def upload_file_chunks(
    file_path: Path,
    *,
    upload_urls: list[dict[str, Any]],
    media_type: str,
    put_bytes: Callable[[str, bytes, dict[str, str]], None] | None = None,
) -> None:
    """
    Upload ``file_path`` to Frame.io presigned URLs.

    Frame.io returns one URL for small files and many ~20 MB chunk URLs for
    large files. Each chunk is uploaded with PUT directly to S3; Frame.io
    assembles the parts server-side.
    """
    if not upload_urls:
        raise RuntimeError("Frame.io returned no upload URLs.")
    file_path = file_path.resolve()
    put = put_bytes or _default_put_bytes
    content_type = media_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    with file_path.open("rb") as handle:
        for index, item in enumerate(upload_urls, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(f"Invalid upload URL entry at chunk {index}.")
            chunk_size = int(item["size"])
            url = str(item["url"])
            chunk = handle.read(chunk_size)
            if not chunk:
                raise RuntimeError(
                    f"Unexpected end of file while uploading chunk {index}."
                )
            put(
                url,
                chunk,
                {
                    "Content-Type": content_type,
                    "x-amz-acl": "private",
                },
            )


def _default_put_bytes(url: str, data: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=600.0) as resp:
            if resp.status and resp.status >= 400:
                raise RuntimeError(f"Upload chunk failed with status {resp.status}.")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Upload chunk failed ({exc.code})."
        ) from exc


def poll_upload_complete(
    config: FrameioConfig,
    *,
    file_id: str,
    poll_interval_sec: float = 5.0,
    timeout_sec: float = 4 * 60 * 60,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> None:
    url = (
        f"{FRAMEIO_API_BASE}/accounts/{config.account_id}/files/{file_id}/status"
    )
    deadline = monotonic_fn() + timeout_sec
    while monotonic_fn() < deadline:
        response = _api_request("GET", url, token=config.access_token)
        data = response.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Frame.io upload status response missing data.")
        if data.get("upload_failed"):
            raise RuntimeError("Frame.io reported upload_failed.")
        if data.get("upload_complete"):
            return
        sleep_fn(poll_interval_sec)
    raise TimeoutError("Timed out waiting for Frame.io upload to complete.")


def create_public_share(
    config: FrameioConfig,
    *,
    asset_ids: list[str],
    share_name: str,
) -> FrameioShareResult:
    if not asset_ids:
        raise ValueError("Frame.io share requires at least one asset id.")
    url = (
        f"{FRAMEIO_API_BASE}/accounts/{config.account_id}/projects/"
        f"{config.project_id}/shares"
    )
    body = {
        "data": {
            "type": "asset",
            "access": "public",
            "name": share_name,
            "asset_ids": asset_ids,
            "expiration": None,
            "downloading_enabled": True,
        }
    }
    response = _api_request("POST", url, token=config.access_token, body=body)
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Frame.io share response missing data.")
    share_id = str(data.get("id") or "")
    short_url = str(data.get("short_url") or "")
    if not share_id or not short_url:
        raise RuntimeError("Frame.io share response missing id or short_url.")
    return FrameioShareResult(
        share_id=share_id,
        short_url=short_url,
        name=str(data.get("name") or share_name),
    )


def upload_file_to_frameio(
    config: FrameioConfig,
    file_path: Path,
    *,
    put_bytes: Callable[[str, bytes, dict[str, str]], None] | None = None,
    poll_interval_sec: float = 5.0,
    timeout_sec: float = 4 * 60 * 60,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> FrameioUploadResult:
    """Upload one local file to Frame.io and wait until processing completes."""
    file_path = file_path.resolve()
    created = create_local_upload(config, file_path=file_path)
    file_id = str(created.get("id") or "")
    media_type = str(created.get("media_type") or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
    upload_urls = created.get("upload_urls") or []
    if not file_id:
        raise RuntimeError("Frame.io local_upload response missing file id.")
    if not isinstance(upload_urls, list):
        raise RuntimeError("Frame.io local_upload response missing upload_urls.")
    upload_file_chunks(
        file_path,
        upload_urls=upload_urls,
        media_type=media_type,
        put_bytes=put_bytes,
    )
    poll_upload_complete(
        config,
        file_id=file_id,
        poll_interval_sec=poll_interval_sec,
        timeout_sec=timeout_sec,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
    )
    return FrameioUploadResult(
        file_id=file_id,
        file_name=file_path.name,
        media_type=media_type,
    )


def upload_files_and_create_share(
    config: FrameioConfig,
    *,
    file_paths: list[Path],
    share_name: str,
    put_bytes: Callable[[str, bytes, dict[str, str]], None] | None = None,
    poll_interval_sec: float = 5.0,
    timeout_sec: float = 4 * 60 * 60,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> FrameioDeliveryResult:
    """Upload multiple files and expose them in one public share."""
    if not file_paths:
        raise ValueError("No files to upload.")
    uploads = tuple(
        upload_file_to_frameio(
            config,
            path,
            put_bytes=put_bytes,
            poll_interval_sec=poll_interval_sec,
            timeout_sec=timeout_sec,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
        for path in file_paths
    )
    share = create_public_share(
        config,
        asset_ids=[item.file_id for item in uploads],
        share_name=share_name,
    )
    return FrameioDeliveryResult(uploads=uploads, share=share)


def upload_file_and_create_share(
    config: FrameioConfig,
    *,
    file_path: Path,
    share_name: str | None = None,
    put_bytes: Callable[[str, bytes, dict[str, str]], None] | None = None,
    poll_interval_sec: float = 5.0,
    timeout_sec: float = 4 * 60 * 60,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> FrameioDeliveryResult:
    file_path = file_path.resolve()
    return upload_files_and_create_share(
        config,
        file_paths=[file_path],
        share_name=share_name or file_path.name,
        put_bytes=put_bytes,
        poll_interval_sec=poll_interval_sec,
        timeout_sec=timeout_sec,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
    )
