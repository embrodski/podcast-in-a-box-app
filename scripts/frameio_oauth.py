"""Adobe IMS OAuth (Native App / PKCE) token storage for Frame.io v4."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMS_AUTHORIZE_URL = "https://ims-na1.adobelogin.com/ims/authorize/v2"
IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
DEFAULT_SCOPES = "openid email profile offline_access additional_info.roles"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TOKEN_PATH = REPO_ROOT / ".frameio-oauth.json"
DEFAULT_PENDING_PATH = REPO_ROOT / ".frameio-oauth-pending.json"
ACCESS_TOKEN_LIFETIME_SEC = 24 * 3600 - 60
KEEP_ALIVE_MIN_INTERVAL_SEC = 12 * 3600

PIAB_DEFAULTS = {
    "client_id": "47e70e7744c24ea3af17598e2f845192",
    "redirect_uri": (
        "adobe+c0f5fce82f162fd257b5d34c8b9ec11ac6f78787://adobeid/"
        "47e70e7744c24ea3af17598e2f845192"
    ),
    "loopback_redirect_uri": "http://127.0.0.1:8765/callback",
    "project_name": "Podcast In A Box uploads",
}


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    code_verifier: str


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorization_request(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: str = DEFAULT_SCOPES,
) -> AuthorizationRequest:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = IMS_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    return AuthorizationRequest(url=url, state=state, code_verifier=verifier)


def save_pending_auth(
    pending: AuthorizationRequest,
    *,
    client_id: str,
    redirect_uri: str,
    path: Path = DEFAULT_PENDING_PATH,
) -> None:
    payload = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": pending.state,
        "code_verifier": pending.code_verifier,
        "scopes": DEFAULT_SCOPES,
        "created_at": time.time(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_pending_auth(path: Path = DEFAULT_PENDING_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Pending OAuth state not found: {path}. Run harness_frameio_oauth.py login first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def clear_pending_auth(path: Path = DEFAULT_PENDING_PATH) -> None:
    if path.is_file():
        path.unlink()


def parse_authorization_response(raw: str) -> tuple[str, str | None]:
    """
    Extract authorization ``code`` and optional ``state`` from a pasted callback
    URL or query string.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Empty authorization response.")
    if "://" in text:
        parsed = urllib.parse.urlparse(text)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    elif text.startswith("?"):
        params = urllib.parse.parse_qs(text[1:], keep_blank_values=True)
    elif "=" in text:
        params = urllib.parse.parse_qs(text, keep_blank_values=True)
    else:
        return text, None
    if params.get("error"):
        desc = (params.get("error_description") or params.get("error") or ["Unknown"])[0]
        raise ValueError(f"Adobe authorization error: {desc}")
    code_vals = params.get("code")
    if not code_vals or not code_vals[0].strip():
        raise ValueError("No authorization code found in the pasted response.")
    state_vals = params.get("state")
    state = state_vals[0].strip() if state_vals else None
    return code_vals[0].strip(), state


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        IMS_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Adobe token request failed ({exc.code}): {detail[:500]}"
        ) from exc
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("Adobe token response was not JSON.")
    if payload.get("error"):
        raise RuntimeError(
            f"Adobe token error: {payload.get('error')} "
            f"{payload.get('error_description', '')}".strip()
        )
    return payload


def exchange_authorization_code(
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    payload = _token_request(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
    )
    return _normalize_token_payload(payload, client_id=client_id, redirect_uri=redirect_uri)


def refresh_access_token(token_data: dict[str, Any]) -> dict[str, Any]:
    refresh_token = str(token_data.get("refresh_token") or "").strip()
    client_id = str(token_data.get("client_id") or "").strip()
    redirect_uri = str(token_data.get("redirect_uri") or "").strip()
    if not refresh_token or not client_id:
        raise RuntimeError("Saved Frame.io OAuth tokens are missing refresh_token or client_id.")
    payload = _token_request(
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        }
    )
    merged = _normalize_token_payload(
        payload,
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    if not merged.get("refresh_token"):
        merged["refresh_token"] = refresh_token
    return merged


def _normalize_token_payload(
    payload: dict[str, Any],
    *,
    client_id: str,
    redirect_uri: str,
) -> dict[str, Any]:
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Adobe token response missing access_token.")
    expires_in = int(payload.get("expires_in") or 3600)
    now = time.time()
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token") or "").strip(),
        "token_type": str(payload.get("token_type") or "bearer"),
        "expires_at": now + max(30, expires_in - 60),
        "refreshed_at": now,
        "scopes": str(payload.get("scope") or DEFAULT_SCOPES),
    }


def save_token_data(data: dict[str, Any], path: Path = DEFAULT_TOKEN_PATH) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_token_data(path: Path = DEFAULT_TOKEN_PATH) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def last_refreshed_at(data: dict[str, Any]) -> float:
    """When the saved login was last successfully refreshed (unix seconds)."""
    raw = data.get("refreshed_at")
    try:
        stamped = float(raw)
    except (TypeError, ValueError):
        stamped = 0.0
    if stamped > 0:
        return stamped
    try:
        expires_at = float(data.get("expires_at") or 0)
    except (TypeError, ValueError):
        return 0.0
    if expires_at <= 0:
        return 0.0
    return max(0.0, expires_at - ACCESS_TOKEN_LIFETIME_SEC)


@dataclass(frozen=True)
class KeepAliveResult:
    status: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status in {"refreshed", "skipped"}


def keep_frameio_oauth_alive(
    path: Path = DEFAULT_TOKEN_PATH,
    *,
    now: float | None = None,
    min_interval_sec: float = KEEP_ALIVE_MIN_INTERVAL_SEC,
    refresh: Any = None,
) -> KeepAliveResult:
    """
    Refresh the saved Adobe login so the 14-day refresh token does not expire.

    Skips when there is no login, or when we already refreshed within
    ``min_interval_sec`` *and* the access token is still valid.
    """
    data = load_token_data(path)
    if not data or not str(data.get("refresh_token") or "").strip():
        return KeepAliveResult("skipped", "No saved Frame.io login to refresh.")
    clock = time.time() if now is None else float(now)
    expires_at = float(data.get("expires_at") or 0)
    age = clock - last_refreshed_at(data)
    still_valid = expires_at > clock
    if still_valid and age < float(min_interval_sec):
        return KeepAliveResult(
            "skipped",
            f"Frame.io login is still fresh ({age / 3600:.1f}h since last refresh).",
        )
    try:
        refresher = refresh if refresh is not None else refresh_access_token
        refreshed = refresher(data)
        save_token_data(refreshed, path)
    except Exception as exc:
        return KeepAliveResult("failed", str(exc))
    return KeepAliveResult("refreshed", "Frame.io OAuth token refreshed.")


def get_valid_access_token(path: Path = DEFAULT_TOKEN_PATH) -> str | None:
    data = load_token_data(path)
    if not data:
        return None
    expires_at = float(data.get("expires_at") or 0)
    if expires_at > time.time() and data.get("access_token"):
        return str(data["access_token"])
    if not data.get("refresh_token"):
        return str(data.get("access_token") or "") or None
    refreshed = refresh_access_token(data)
    save_token_data(refreshed, path)
    return str(refreshed["access_token"])
