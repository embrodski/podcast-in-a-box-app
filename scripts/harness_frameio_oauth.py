#!/usr/bin/env python3
"""One-time Frame.io Native App (PKCE) login for PIAB delivery."""

from __future__ import annotations

import argparse
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frameio_oauth import (
    DEFAULT_PENDING_PATH,
    DEFAULT_TOKEN_PATH,
    PIAB_DEFAULTS,
    build_authorization_request,
    clear_pending_auth,
    exchange_authorization_code,
    get_valid_access_token,
    keep_frameio_oauth_alive,
    load_pending_auth,
    parse_authorization_response,
    save_pending_auth,
    save_token_data,
)
from frameio_oauth_windows import (
    clear_capture,
    ensure_keepalive_task,
    register_protocol_handler,
    scheme_from_redirect_uri,
    uninstall_keepalive_task,
    unregister_protocol_handler,
    wait_for_capture,
    write_capture,
)

LOOPBACK_SETUP_HINT = """
Loopback login needs a Redirect URI pattern in Adobe Developer Console:
  http://127.0.0.1:8765/callback

App Builder projects often do not expose that field. On Windows, prefer:
  python scripts/harness_frameio_oauth.py register-protocol
  python scripts/harness_frameio_oauth.py login
"""


def _wait_for_loopback_callback(
    *,
    host: str,
    port: int,
    timeout_sec: float,
) -> str:
    result: dict[str, str | None] = {"raw_path": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            result["raw_path"] = self.path
            body = (
                "<html><body><h1>Frame.io login complete</h1>"
                "<p>You can close this tab and return to the terminal.</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    server = HTTPServer((host, port), Handler)
    server.timeout = 1.0

    import time

    end = time.time() + timeout_sec
    while time.time() < end and result["raw_path"] is None:
        server.handle_request()

    server.server_close()
    raw_path = result.get("raw_path")
    if not raw_path:
        raise TimeoutError(
            "Timed out waiting for Adobe to redirect to the local callback URL."
        )
    return raw_path


def _complete_login(code: str, state: str | None) -> int:
    try:
        saved = load_pending_auth(DEFAULT_PENDING_PATH)
        if state and state != saved.get("state"):
            print(
                "ERROR: state mismatch — start over with harness_frameio_oauth.py login",
                file=sys.stderr,
            )
            return 1
        token_data = exchange_authorization_code(
            client_id=str(saved["client_id"]),
            redirect_uri=str(saved["redirect_uri"]),
            code=code,
            code_verifier=str(saved["code_verifier"]),
        )
        save_token_data(token_data, DEFAULT_TOKEN_PATH)
        clear_pending_auth(DEFAULT_PENDING_PATH)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Saved OAuth tokens to {DEFAULT_TOKEN_PATH}")
    print("Next:")
    print("  python scripts/harness_frameio_discover.py --write-env")
    return 0


def _login_native_protocol(args: argparse.Namespace) -> int:
    client_id = args.client_id or PIAB_DEFAULTS["client_id"]
    redirect_uri = args.redirect_uri or PIAB_DEFAULTS["redirect_uri"]
    scheme = scheme_from_redirect_uri(redirect_uri)

    print("Frame.io login (Windows app redirect)")
    print()
    print("Uses your existing Adobe Native App redirect:")
    print(f"  {redirect_uri}")
    print()
    print("After sign-in, Windows should ask to open PIAB OAuth.")
    print("Click Allow / Open so the authorization code can be captured.")
    print()

    try:
        register_protocol_handler(scheme=scheme)
    except OSError as exc:
        print(f"WARNING: could not register the Windows OAuth handler: {exc}")

    pending = build_authorization_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    save_pending_auth(
        pending,
        client_id=client_id,
        redirect_uri=redirect_uri,
        path=DEFAULT_PENDING_PATH,
    )
    clear_capture()

    print("Opening browser for Adobe sign-in...")
    print()
    print(pending.url)
    print()
    if not args.no_browser:
        try:
            webbrowser.open(pending.url)
        except OSError:
            print("Could not open a browser automatically — copy the URL above.")

    try:
        captured = wait_for_capture(timeout_sec=float(args.timeout_sec))
        return _complete_login(captured["code"], captured.get("state"))
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "\nIf you never saw an Open-app prompt, run once:\n"
            "  python scripts/harness_frameio_oauth.py register-protocol\n"
            "Then run login again.",
            file=sys.stderr,
        )
        return 1


def _login_loopback(args: argparse.Namespace) -> int:
    client_id = args.client_id or PIAB_DEFAULTS["client_id"]
    redirect_uri = args.redirect_uri or PIAB_DEFAULTS["loopback_redirect_uri"]
    host = args.loopback_host
    port = int(args.loopback_port)

    print("Frame.io login (localhost callback)")
    print()
    print("Using redirect URI:")
    print(f"  {redirect_uri}")
    print(LOOPBACK_SETUP_HINT)

    pending = build_authorization_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    save_pending_auth(
        pending,
        client_id=client_id,
        redirect_uri=redirect_uri,
        path=DEFAULT_PENDING_PATH,
    )

    print("Waiting for browser callback on", f"{host}:{port}", "...")
    print("Opening browser for Adobe sign-in...")
    print()
    print(pending.url)
    print()
    if not args.no_browser:
        try:
            webbrowser.open(pending.url)
        except OSError:
            print("Could not open a browser automatically — copy the URL above.")

    try:
        raw_path = _wait_for_loopback_callback(
            host=host,
            port=port,
            timeout_sec=float(args.timeout_sec),
        )
        parsed = urllib.parse.urlparse(raw_path)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if params.get("error"):
            desc = (params.get("error_description") or params.get("error") or ["Unknown"])[0]
            print(f"ERROR: Adobe authorization error: {desc}", file=sys.stderr)
            return 1
        code_vals = params.get("code")
        if not code_vals:
            print(
                "ERROR: Local callback received no ?code= parameter.\n"
                "If the browser stayed on a blank Adobe page, the redirect URI is "
                "probably not registered yet — see the setup steps above.",
                file=sys.stderr,
            )
            return 1
        state_vals = params.get("state")
        state = state_vals[0] if state_vals else None
        return _complete_login(code_vals[0], state)
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(LOOPBACK_SETUP_HINT, file=sys.stderr)
        return 1


def _login_manual(args: argparse.Namespace) -> int:
    client_id = args.client_id or PIAB_DEFAULTS["client_id"]
    redirect_uri = args.redirect_uri or PIAB_DEFAULTS["redirect_uri"]
    pending = build_authorization_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    save_pending_auth(
        pending,
        client_id=client_id,
        redirect_uri=redirect_uri,
        path=DEFAULT_PENDING_PATH,
    )

    print("Frame.io Native App login (manual paste — often fails on Windows)")
    print()
    print(pending.url)
    print()
    if not args.no_browser:
        try:
            webbrowser.open(pending.url)
        except OSError:
            pass
    raw = input("Paste redirect URL or authorization code: ").strip()
    if not raw:
        print("ERROR: No response pasted.", file=sys.stderr)
        return 1
    code, state = parse_authorization_response(raw)
    return _complete_login(code, state)


def _login(args: argparse.Namespace) -> int:
    if args.manual:
        return _login_manual(args)
    if args.loopback:
        return _login_loopback(args)
    if sys.platform == "win32":
        return _login_native_protocol(args)
    return _login_loopback(args)


def _register_protocol(args: argparse.Namespace) -> int:
    redirect_uri = args.redirect_uri or PIAB_DEFAULTS["redirect_uri"]
    scheme = scheme_from_redirect_uri(redirect_uri)
    try:
        register_protocol_handler(scheme=scheme, python_exe=args.python_exe)
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Registered Windows handler for Adobe OAuth redirect.")
    print(f"  Scheme: {scheme}")
    print()
    print("Next:")
    print("  python scripts/harness_frameio_oauth.py login")
    return 0


def _unregister_protocol(args: argparse.Namespace) -> int:
    redirect_uri = args.redirect_uri or PIAB_DEFAULTS["redirect_uri"]
    scheme = scheme_from_redirect_uri(redirect_uri)
    try:
        unregister_protocol_handler(scheme=scheme)
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Removed Windows handler for {scheme}")
    return 0


def _capture(args: argparse.Namespace) -> int:
    raw = args.url
    if not raw and len(sys.argv) > 2:
        raw = sys.argv[2]
    if not raw:
        print("ERROR: No redirect URL provided.", file=sys.stderr)
        return 1
    try:
        code, state = parse_authorization_response(raw)
        write_capture(code=code, state=state, raw=raw)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Captured Frame.io authorization code.")
    return 0


def _status(_args: argparse.Namespace) -> int:
    token = get_valid_access_token()
    if token:
        print("Frame.io OAuth: access token is available (refresh will happen automatically).")
        print(f"Token file: {DEFAULT_TOKEN_PATH}")
        return 0
    if DEFAULT_TOKEN_PATH.is_file():
        print("Frame.io OAuth token file exists but could not obtain a valid access token.")
        print("Run: python scripts/harness_frameio_oauth.py login")
        return 1
    print("Frame.io OAuth is not configured yet.")
    print("Run: python scripts/harness_frameio_oauth.py login")
    return 1


def _keep_alive(_args: argparse.Namespace) -> int:
    from win_hidden_console import install_hidden_console

    install_hidden_console()
    result = keep_frameio_oauth_alive()
    print(f"{result.status}: {result.message}")
    return 0 if result.ok else 1


def _install_keep_alive(_args: argparse.Namespace) -> int:
    try:
        status = ensure_keepalive_task()
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Weekly Frame.io OAuth keep-alive task {status}.")
    return 0


def _uninstall_keep_alive(_args: argparse.Namespace) -> int:
    try:
        removed = uninstall_keepalive_task()
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if removed:
        print("Removed weekly Frame.io OAuth keep-alive task.")
    else:
        print("No weekly Frame.io OAuth keep-alive task was installed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Frame.io Native App OAuth setup.")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="Run one-time browser login and save tokens.")
    login.add_argument("--client-id", default=PIAB_DEFAULTS["client_id"])
    login.add_argument("--redirect-uri", default=None)
    login.add_argument("--manual", action="store_true", help="Paste redirect URL manually.")
    login.add_argument(
        "--loopback",
        action="store_true",
        help="Use http://127.0.0.1 callback (requires Adobe redirect URI pattern).",
    )
    login.add_argument("--loopback-host", default="127.0.0.1")
    login.add_argument("--loopback-port", type=int, default=8765)
    login.add_argument("--timeout-sec", type=float, default=300.0)
    login.add_argument("--no-browser", action="store_true")
    login.set_defaults(func=_login)

    register = sub.add_parser(
        "register-protocol",
        help="Register Windows handler for the Adobe adobe+:// redirect (one-time).",
    )
    register.add_argument("--redirect-uri", default=PIAB_DEFAULTS["redirect_uri"])
    register.add_argument("--python-exe", default=None)
    register.set_defaults(func=_register_protocol)

    unregister = sub.add_parser(
        "unregister-protocol",
        help="Remove the Windows Adobe adobe+:// redirect handler.",
    )
    unregister.add_argument("--redirect-uri", default=PIAB_DEFAULTS["redirect_uri"])
    unregister.set_defaults(func=_unregister_protocol)

    capture = sub.add_parser(
        "capture",
        help=argparse.SUPPRESS,
    )
    capture.add_argument("url", nargs="?", default="")
    capture.set_defaults(func=_capture)

    status = sub.add_parser("status", help="Check whether saved tokens work.")
    status.set_defaults(func=_status)

    keep = sub.add_parser(
        "keep-alive",
        help="Refresh saved Frame.io tokens if they are older than 12 hours.",
    )
    keep.set_defaults(func=_keep_alive)

    install_keep = sub.add_parser(
        "install-keep-alive",
        help="Install a weekly Windows scheduled task that refreshes Frame.io tokens.",
    )
    install_keep.set_defaults(func=_install_keep_alive)

    uninstall_keep = sub.add_parser(
        "uninstall-keep-alive",
        help="Remove the weekly Frame.io token keep-alive scheduled task.",
    )
    uninstall_keep.set_defaults(func=_uninstall_keep_alive)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
