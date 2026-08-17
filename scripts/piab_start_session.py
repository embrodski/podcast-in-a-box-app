#!/usr/bin/env python3
"""
Interactive Podcast In A Box session start.

Recording flow: vMix, preset, camera setup, MultiCorder record.
Autocut flow: scan MultiCorder dumps, init session, then prep/render (existing PIAB).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_episode_lib import PIAB_STATE_FILENAME
from harness_delivery_prompt import (
    delivery_already_confirmed,
    delivery_from_cli,
    merge_delivery_into_state,
    prompt_delivery_opt_in,
)
from piab_lib import (
    DEFAULT_SCAN_ROOT,
    DEFAULT_WORK_ROOT,
    collect_session_scan,
    load_piab_state,
    print_json,
    save_piab_state,
)
from piab_ensure_vmix import ensure_vmix_running
from piab_open_vmix_preset import open_vmix_preset
from piab_confirm_camera_setup import confirm_camera_setup
from piab_multicorder_record import run_multicorder_session
from piab_resume import is_prep_resumable, plan_to_json
from piab_resume import build_prep_resume_plan

AUTOCUT_AFTER_RECORDING_MESSAGE = (
    "Recording flow complete. Starting autocut flow with the newest MultiCorder "
    f"files in {DEFAULT_SCAN_ROOT}."
)
RECORDING_COMPLETE_STOP_MESSAGE = (
    "Recording complete. MultiCorder files are in "
    f"{DEFAULT_SCAN_ROOT}. Resume autocut later with option 2 or run autocut "
    "from Step 1."
)


def _prompt_choice(prompt: str, *, choices: dict[str, str]) -> str:
    labels = ", ".join(f"{key}={label}" for key, label in choices.items())
    while True:
        answer = input(f"{prompt} [{labels}]: ").strip().lower()
        if answer in choices:
            return answer
        print(f"Please enter one of: {', '.join(choices)}")


def _prompt_yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} [{suffix}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter y or n.")


def _run_init(repo: Path, argv: list[str]) -> int:
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / "piab_init_session.py"), *argv],
        cwd=str(repo),
    )
    return proc.returncode


def _maybe_configure_delivery(working: Path, *, delivery_email: str | None, confirm: bool) -> None:
    state = load_piab_state(working)
    if delivery_already_confirmed(state):
        return
    if delivery_email:
        delivery = delivery_from_cli(email=delivery_email, confirm=confirm)
    else:
        delivery = prompt_delivery_opt_in()
    merge_delivery_into_state(state, delivery)
    save_piab_state(working, state)


def _init_argv_with_delivery(base: list[str], args: argparse.Namespace) -> list[str]:
    argv = list(base)
    if args.delivery_email:
        argv.extend(["--delivery-email", args.delivery_email])
        if args.confirm_delivery_email:
            argv.append("--confirm-delivery-email")
    return argv


def _run_prep(repo: Path, working: Path, *, resume: bool) -> int:
    argv = [sys.executable, str(repo / "scripts" / "piab_run_prep.py"), str(working)]
    if resume:
        argv.append("--resume")
    proc = subprocess.run(argv, cwd=str(repo))
    return proc.returncode


def _prompt_recording_status() -> str:
    return _prompt_choice(
        "Have you already recorded this podcast, or will you be recording now?",
        choices={
            "1": "already recorded",
            "2": "record now",
        },
    )


def _prompt_continue_to_autocut() -> bool:
    choice = _prompt_choice(
        "Continue to Autocut, or Stop? "
        "(you can use the files yourself now, or resume autocut later)",
        choices={
            "1": "continue to autocut",
            "2": "stop",
        },
    )
    return choice == "1"


def _run_recording_flow(args: argparse.Namespace) -> int:
    if args.skip_recording_flow:
        return 0

    vmix = ensure_vmix_running(skip=args.skip_vmix)
    if not vmix.ok:
        if vmix.message:
            print(vmix.message, file=sys.stderr)
        return 1

    preset = open_vmix_preset(skip=args.skip_vmix_preset)
    if not preset.ok:
        if preset.message:
            print(preset.message, file=sys.stderr)
        return 1

    camera = confirm_camera_setup(
        skip=args.skip_camera_setup,
        auto_confirm=args.non_interactive or args.confirm_camera_ready,
    )
    if not camera.ok:
        if camera.message:
            print(camera.message, file=sys.stderr)
        return 1

    recording = run_multicorder_session(
        skip=args.skip_multicorder,
        auto_continue=args.non_interactive or args.auto_continue_recording,
        already_recording_action=args.already_recording,
    )
    if not recording.ok:
        if recording.message:
            print(recording.message, file=sys.stderr)
        return 1
    return 0


def _init_special_folder(repo: Path, args: argparse.Namespace, working: Path) -> int:
    try:
        payload = collect_session_scan(working)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(payload)
    requirements = payload["requirements"]
    if not requirements["ok"]:
        print("\nThis folder is missing required files for Podcast In A Box:")
        for line in requirements["missing"]:
            print(f"  - {line}")
        for line in requirements["warnings"]:
            print(f"  - {line}")
        if requirements.get("unrecognized_media"):
            print("  Unrecognized media files:")
            for name in requirements["unrecognized_media"]:
                print(f"    - {name}")
        if not _prompt_yes_no("Continue anyway?", default=False):
            return 1

    if not _prompt_yes_no(
        f"Use {working} as the working folder and start labeling?",
        default=True,
    ):
        return 1

    rc = _run_init(
        repo,
        _init_argv_with_delivery(
            ["--working-folder", str(working.resolve())],
            args,
        ),
    )
    if rc == 0:
        _maybe_configure_delivery(
            working.resolve(),
            delivery_email=args.delivery_email,
            confirm=args.confirm_delivery_email,
        )
    return rc


def _init_default_folder(repo: Path, args: argparse.Namespace, *, after_recording: bool) -> int:
    if after_recording:
        print()
        print(AUTOCUT_AFTER_RECORDING_MESSAGE)
        print()

    try:
        payload = collect_session_scan(DEFAULT_SCAN_ROOT)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(payload)
    requirements = payload["requirements"]
    if not requirements["ok"]:
        print("\nWARNING: Latest default-folder cluster is incomplete:")
        for line in requirements["missing"]:
            print(f"  - {line}")

    if after_recording:
        prompt = "Use these newest MultiCorder files for this session?"
    else:
        prompt = "Are these the files from this session?"

    if not _prompt_yes_no(prompt, default=True):
        print("Aborted. Move or copy sources into the default folder and re-run.")
        return 1

    if args.default_name:
        name = args.default_name
    else:
        while True:
            name = input("Working folder name to create under PodcastInABox\\Sessions: ").strip()
            if name and "/" not in name and "\\" not in name:
                break
            print("Enter a single folder name (no path separators).")

    working = DEFAULT_WORK_ROOT / name
    rc = _run_init(
        repo,
        _init_argv_with_delivery(
            [
                "--name",
                name,
                "--root",
                str(DEFAULT_WORK_ROOT),
                "--scan-root",
                str(DEFAULT_SCAN_ROOT),
            ],
            args,
        ),
    )
    if rc == 0:
        _maybe_configure_delivery(
            working,
            delivery_email=args.delivery_email,
            confirm=args.confirm_delivery_email,
        )
    return rc


def _run_autocut_flow(
    repo: Path,
    args: argparse.Namespace,
    *,
    after_recording: bool,
) -> int:
    if args.working_folder is not None:
        return _init_special_folder(repo, args, args.working_folder.resolve())

    if after_recording or args.after_recording:
        return _init_default_folder(repo, args, after_recording=True)

    print()
    mode = _prompt_choice(
        "Where are the MultiCorder source files?",
        choices={
            "1": f"default folder ({DEFAULT_SCAN_ROOT})",
            "2": "special folder (files already in a dedicated folder)",
        },
    )
    if mode == "2":
        while True:
            raw = input("Enter full path to the special folder: ").strip().strip('"')
            if not raw:
                print("Path is required.")
                continue
            working = Path(raw)
            if not working.is_dir():
                print(f"Folder not found: {working}")
                continue
            return _init_special_folder(repo, args, working)

    return _init_default_folder(repo, args, after_recording=False)


def _run_autocut_resume(repo: Path) -> int:
    while True:
        raw = input("Enter working folder path (contains podcast-in-a-box.json): ").strip().strip('"')
        if not raw:
            print("Path is required.")
            continue
        working = Path(raw)
        state_path = working / PIAB_STATE_FILENAME
        if not state_path.is_file():
            print(f"No PIAB state file: {state_path}")
            continue
        try:
            state = load_piab_state(working)
            plan = build_prep_resume_plan(state, working, resume=True)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}")
            continue

        print_json(plan_to_json(plan))
        if plan.ready_for_approval:
            print("\n1 Min Test is already done. Open Output and continue approval in the app/agent.")
            return 0
        if not is_prep_resumable(state, working) and not plan.skipped_steps:
            print("\nNothing to resume yet — complete labeling and run prep first.")
            continue
        if not _prompt_yes_no("Run prep with --resume from the step above?", default=True):
            continue
        return _run_prep(repo, working, resume=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive PIAB session start.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repo root (default: parent of scripts/).",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Require --default-name or --working-folder; do not prompt.",
    )
    parser.add_argument(
        "--default-name",
        help="Default mode: working subfolder name under E:\\PodcastRoom\\PodcastInABox\\Sessions.",
    )
    parser.add_argument(
        "--working-folder",
        type=Path,
        help="Special mode: folder that already contains MultiCorder sources.",
    )
    parser.add_argument(
        "--delivery-email",
        help="Non-interactive: recipient email for finished-video delivery.",
    )
    parser.add_argument(
        "--confirm-delivery-email",
        action="store_true",
        help="Required with --delivery-email for non-interactive delivery opt-in.",
    )
    parser.add_argument(
        "--skip-recording-flow",
        action="store_true",
        help="Skip recording flow (same as --already-recorded).",
    )
    parser.add_argument(
        "--already-recorded",
        action="store_true",
        help="Autocut flow only: podcast was recorded before starting PIAB.",
    )
    parser.add_argument(
        "--record-now",
        action="store_true",
        help="Non-interactive: run recording flow before autocut.",
    )
    parser.add_argument(
        "--stop-after-recording",
        action="store_true",
        help="Non-interactive: end after recording without starting autocut.",
    )
    parser.add_argument(
        "--continue-to-autocut",
        action="store_true",
        help="Non-interactive: after recording, start autocut with newest files.",
    )
    parser.add_argument(
        "--after-recording",
        action="store_true",
        help="Autocut flow: use newest files in the default folder without prompting for location.",
    )
    parser.add_argument(
        "--skip-vmix",
        action="store_true",
        help="Skip the vMix running check (automation/CI).",
    )
    parser.add_argument(
        "--skip-vmix-preset",
        action="store_true",
        help="Skip loading the standard vMix preset (automation/CI).",
    )
    parser.add_argument(
        "--skip-camera-setup",
        action="store_true",
        help="Skip camera/microphone setup confirmation (automation/CI).",
    )
    parser.add_argument(
        "--confirm-camera-ready",
        action="store_true",
        help="Non-interactive: skip camera setup wait and continue immediately.",
    )
    parser.add_argument(
        "--skip-multicorder",
        action="store_true",
        help="Skip MultiCorder record session (automation/CI).",
    )
    parser.add_argument(
        "--auto-continue-recording",
        action="store_true",
        help="Non-interactive: start and immediately stop MultiCorder.",
    )
    parser.add_argument(
        "--already-recording",
        choices=("continue", "restart"),
        help="Non-interactive: action when MultiCorder is already recording.",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()

    already_recorded = args.already_recorded or args.skip_recording_flow

    if args.non_interactive:
        if args.record_now and already_recorded:
            print(
                "ERROR: --record-now cannot be combined with --already-recorded or "
                "--skip-recording-flow.",
                file=sys.stderr,
            )
            return 1
        if args.stop_after_recording and args.continue_to_autocut:
            print(
                "ERROR: --stop-after-recording and --continue-to-autocut are mutually "
                "exclusive.",
                file=sys.stderr,
            )
            return 1

        ran_recording = False
        if not already_recorded:
            rc = _run_recording_flow(args)
            if rc:
                return rc
            ran_recording = True
            if args.stop_after_recording:
                print(RECORDING_COMPLETE_STOP_MESSAGE)
                return 0
            if (
                not args.continue_to_autocut
                and args.working_folder is None
                and args.default_name is None
            ):
                print(
                    "ERROR: after recording, pass --continue-to-autocut with "
                    "--default-name or --working-folder, or --stop-after-recording.",
                    file=sys.stderr,
                )
                return 1

        if args.working_folder is None and args.default_name is None:
            print(
                "ERROR: --non-interactive requires --working-folder or --default-name.",
                file=sys.stderr,
            )
            return 1

        return _run_autocut_flow(
            repo,
            args,
            after_recording=ran_recording or args.after_recording,
        )

    print("Podcast In A Box")
    print()
    action = _prompt_choice(
        "What would you like to do?",
        choices={
            "1": "new session",
            "2": "resume autocut flow",
        },
    )

    if action == "2":
        return _run_autocut_resume(repo)

    recording_status = _prompt_recording_status()
    if recording_status == "1":
        return _run_autocut_flow(repo, args, after_recording=False)

    rc = _run_recording_flow(args)
    if rc:
        return rc

    if not _prompt_continue_to_autocut():
        print()
        print(RECORDING_COMPLETE_STOP_MESSAGE)
        return 0

    return _run_autocut_flow(repo, args, after_recording=True)


if __name__ == "__main__":
    raise SystemExit(main())
