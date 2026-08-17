#!/usr/bin/env python3
"""CLI for the PIAB app controller (no GUI required)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.controller import PiabController
from app.controller.paths import migrate_legacy_work_files


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2))


def cmd_preflight(controller: PiabController, args: argparse.Namespace) -> int:
    report = controller.run_preflight()
    if args.json:
        _print_json(report.to_dict())
    else:
        for check in report.checks:
            blocks = f" [blocks: {', '.join(check.blocks)}]" if check.blocks else ""
            print(f"{check.id:14} {check.status:4}  {check.message}{blocks}")
        print()
        print(f"ok_for_recording: {report.ok_for_recording}")
        print(f"ok_for_autocut:   {report.ok_for_autocut}")
        print(f"ok_for_delivery:  {report.ok_for_delivery}")
    return 0


def cmd_sessions(controller: PiabController, args: argparse.Namespace) -> int:
    sessions = controller.list_recent_sessions(limit=args.limit)
    if args.json:
        _print_json([str(path) for path in sessions])
    else:
        for path in sessions:
            screen = controller.resume_screen_for(path)
            print(f"{path}\t(screen {screen})")
    return 0


def cmd_resume(controller: PiabController, args: argparse.Namespace) -> int:
    folder = Path(args.folder).resolve()
    screen = controller.resume_screen_for(folder)
    state = None
    try:
        from app.controller.session_store import load_session_state

        state = load_session_state(folder)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = {
        "working_folder": str(folder),
        "screen": screen,
        "resume_at": state.get("resume_at"),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Screen: {screen}")
        print(f"resume_at: {state.get('resume_at')}")
        print(f"Folder: {folder}")
    return 0


def cmd_auto_name(controller: PiabController, args: argparse.Namespace) -> int:
    name = controller.generate_session_name()
    path = controller.work_root / name
    if args.json:
        _print_json({"name": name, "path": str(path)})
    else:
        print(name)
        print(path)
    return 0


def cmd_check_overwrite(controller: PiabController, args: argparse.Namespace) -> int:
    paths = controller.check_overwrite_risk(args.action, Path(args.folder))
    if args.json:
        _print_json([str(path) for path in paths])
    else:
        if not paths:
            print("No existing files at risk.")
        else:
            print("Would overwrite:")
            for path in paths:
                print(f"  {path}")
    return 0


def cmd_lock(controller: PiabController, args: argparse.Namespace) -> int:
    if args.acquire:
        ok, message = controller.acquire_app_lock(force=args.force)
        if args.json:
            _print_json({"ok": ok, "message": message})
        elif not ok:
            print(message, file=sys.stderr)
        return 0 if ok else 1

    if args.release:
        controller.release_app_lock()
        print("Lock released.")
        return 0

    state = controller.lock.read()
    if args.json:
        _print_json(state.to_dict() if state else None)
    elif state:
        print(json.dumps(state.to_dict(), indent=2))
    else:
        print("No active lock.")
    return 0


def cmd_abort(controller: PiabController, args: argparse.Namespace) -> int:
    result = controller.abort_job(args.job, confirmed=args.yes)
    if args.json:
        _print_json(result.to_dict())
    else:
        print(f"{result.status}: {result.message}")
    return 0 if result.status == "aborted" else 1


def cmd_busy(controller: PiabController, args: argparse.Namespace) -> int:
    reasons = controller.busy_reasons()
    payload = {"busy": bool(reasons), "reasons": reasons}
    if args.json:
        _print_json(payload)
    else:
        print(f"busy={payload['busy']} reasons={reasons}")
    return 0


def cmd_start_prep(controller: PiabController, args: argparse.Namespace) -> int:
    try:
        job = controller.start_prep(
            Path(args.folder),
            allow_overwrite=args.allow_overwrite,
            resume=args.resume,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(job.to_dict())
    else:
        print(f"Started prep job {job.id} (pid {job.pid})")
    if args.wait:
        import time

        while True:
            controller.poll_jobs()
            job = controller.get_job(job.id)
            if job and job.status != "running":
                print(f"Job {job.id} finished: {job.status} {job.message}")
                return 0 if job.status == "completed" else 1
            time.sleep(1.0)
    return 0


def cmd_start_render(controller: PiabController, args: argparse.Namespace) -> int:
    try:
        job = controller.start_render(
            Path(args.folder),
            allow_overwrite=args.allow_overwrite,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(job.to_dict())
    else:
        print(f"Started render job {job.id} (pid {job.pid})")
    if args.wait:
        import time

        while True:
            controller.poll_jobs()
            job = controller.get_job(job.id)
            if job and job.status != "running":
                print(f"Job {job.id} finished: {job.status} {job.message}")
                return 0 if job.status == "completed" else 1
            time.sleep(1.0)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Podcast in a Box app controller CLI.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="Run startup prerequisite checks.")
    preflight.add_argument(
        "--allow-warn",
        action="store_true",
        help="Exit 0 even when autocut checks warn.",
    )

    sub.add_parser("sessions", help="List recent PIAB session folders.").add_argument(
        "--limit",
        type=int,
        default=20,
    )

    resume = sub.add_parser("resume", help="Show resume screen for a session folder.")
    resume.add_argument("--folder", required=True)

    sub.add_parser("auto-name", help="Print the next auto-generated session name.")

    overwrite = sub.add_parser("check-overwrite", help="List outputs at overwrite risk.")
    overwrite.add_argument("--folder", required=True)
    overwrite.add_argument(
        "--action",
        required=True,
        choices=("run_prep", "run_render", "rerun_one_min", "init_session"),
    )

    lock = sub.add_parser("lock", help="Show or manage the app singleton lock.")
    lock.add_argument("--acquire", action="store_true")
    lock.add_argument("--release", action="store_true")
    lock.add_argument("--force", action="store_true")

    abort = sub.add_parser("abort", help="Abort a running job.")
    abort.add_argument("--job", required=True)
    abort.add_argument("--yes", action="store_true", help="Confirm abort.")

    sub.add_parser("busy", help="Report busy state (recording / processing).")

    prep = sub.add_parser("start-prep", help="Start prep subprocess.")
    prep.add_argument("--folder", required=True)
    prep.add_argument("--allow-overwrite", action="store_true")
    prep.add_argument("--resume", action="store_true")
    prep.add_argument("--wait", action="store_true")

    render = sub.add_parser("start-render", help="Start full render subprocess.")
    render.add_argument("--folder", required=True)
    render.add_argument("--allow-overwrite", action="store_true")
    render.add_argument("--wait", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    migrate_legacy_work_files()
    controller = PiabController()

    handlers = {
        "preflight": cmd_preflight,
        "sessions": cmd_sessions,
        "resume": cmd_resume,
        "auto-name": cmd_auto_name,
        "check-overwrite": cmd_check_overwrite,
        "lock": cmd_lock,
        "abort": cmd_abort,
        "busy": cmd_busy,
        "start-prep": cmd_start_prep,
        "start-render": cmd_start_render,
    }
    handler = handlers[args.command]
    return handler(controller, args)


if __name__ == "__main__":
    raise SystemExit(main())
