"""Headless PIAB orchestrator (no Qt imports)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import json
from pathlib import Path

from app.controller.abort_session import record_session_abort
from app.controller.job_queue import JobQueueStore, Lane
from app.controller.jobs import FULL_KINDS, JobRunner
from app.controller.lock import AppLock
from app.controller.overwrite import check_overwrite_risk
from app.controller.prep_progress import (
    read_prep_failure,
    read_prep_progress as _read_prep_progress,
)
from app.controller.render_progress import read_render_progress as _read_render_progress
from app.controller.paths import (
    DEFAULT_SCAN_ROOT,
    DEFAULT_WORK_ROOT,
    JOB_QUEUE_FILENAME,
    REPO_ROOT,
    SCRIPTS_DIR,
    ensure_scripts_path,
    ensure_work_root,
)
from app.controller.preflight import run_preflight
from app.controller.types import AbortResult, Job, JobKind, PreflightReport
from app.controller.resume_router import resume_screen_for
from app.controller.session_store import (
    generate_session_name,
    list_recent_sessions,
    load_session_state,
    remember_session_folder,
    save_session_state,
)
from app.controller.labeling import (
    apply_labels,
    extract_audio_previews,
    extract_video_previews,
    validate_audio_labels,
    validate_video_labels,
)
from app.controller.recording import RecordingPhrases, get_recording_phrases, recording_instructions
from app.controller.session_setup import scan_session_folder, validate_delivery_email
from app.controller.review import (
    approve_one_min_test,
    fix_audio_speaker_swap,
    rerun_one_min_test,
    resolve_one_min_test_path,
    swap_labeled_files,
    swap_speaker_ids_toggle,
)
from app.controller.sync_offset import (
    needs_sync_offset_choice,
    record_sync_offset_choice,
    resolve_ab_test_paths,
)
from app.controller.failure_info import FailureInfo, read_failure_info
from app.controller.fast_preview import (
    approve_fast_preview,
    clear_preview_for_relabel,
    fast_preview_eligible_for_state,
    fast_preview_review_pending,
    full_after_preview_pending,
    should_start_fast_preview,
)


class PiabController:
    """Orchestrates PIAB scripts for GUI and CLI."""

    def __init__(
        self,
        *,
        scan_root: Path = DEFAULT_SCAN_ROOT,
        work_root: Path | None = None,
        lock: AppLock | None = None,
        jobs: JobRunner | None = None,
        job_queue: JobQueueStore | None = None,
        max_processing_jobs: int = 1,
    ) -> None:
        self.scan_root = scan_root.resolve()
        self.work_root = (work_root or DEFAULT_WORK_ROOT).resolve()
        if work_root is None:
            ensure_work_root(self.work_root)
        self.repo_root = REPO_ROOT
        self.scripts_dir = SCRIPTS_DIR
        self.lock = lock or AppLock()
        self.jobs = jobs or JobRunner(max_processing_jobs=max_processing_jobs)
        queue_path = self.work_root / JOB_QUEUE_FILENAME
        self.job_queue = job_queue or JobQueueStore(queue_path)
        self._recording_job_id: str | None = None
        self._recording_continue = threading.Event()

    def acquire_app_lock(self, *, force: bool = False) -> tuple[bool, str]:
        return self.lock.acquire(force=force)

    def release_app_lock(self) -> None:
        self.lock.release()

    def run_preflight(self) -> PreflightReport:
        return run_preflight(scan_root=self.scan_root)

    def generate_session_name(self) -> str:
        return generate_session_name(self.work_root)

    def list_recent_sessions(self, *, limit: int = 20) -> list[Path]:
        return list_recent_sessions(self.work_root, limit=limit)

    def remember_session_folder(self, working_folder: Path) -> None:
        remember_session_folder(working_folder)

    def resume_screen_for(self, working_folder: Path) -> str:
        folder = working_folder.resolve()
        state = load_session_state(folder)
        if read_prep_failure(folder):
            return "F1"
        return resume_screen_for(state.get("resume_at"), state=state)

    def resume_full_needs_overwrite(self, working_folder: Path) -> bool:
        """True when resuming an aborted full-after-preview that already wrote outputs."""
        try:
            state = load_session_state(working_folder.resolve())
        except Exception:
            return False
        if not (state.get("fast_preview_approval") or {}).get("approved_at"):
            return False
        if state.get("last_abort"):
            return True
        steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
        for step_id in (
            "06_conversation_sync",
            "07_deroom_placeholder",
            "08_video_sync",
            "09_transcribe",
            "10_one_min_test",
            "13_full_prep_after_preview",
            "13_full_render",
        ):
            entry = steps.get(step_id) if isinstance(steps, dict) else None
            if isinstance(entry, dict) and entry.get("status") in {"aborted", "in_progress"}:
                return True
        raw = Path(str((state.get("paths") or {}).get("raw") or working_folder / "Raw"))
        if raw.is_dir() and any(raw.glob("*Combined Audio.wav")):
            return True
        return False

    def check_overwrite_risk(self, action: str, working_folder: Path) -> list[Path]:
        return check_overwrite_risk(action, working_folder)

    def is_busy(self) -> bool:
        return bool(self.busy_reasons())

    def busy_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.lock.is_recording_active() or self._recording_job_id:
            reasons.append("recording")
        for job in self.jobs.list_jobs():
            if job.status == "running" and job.kind in {
                "prep",
                "render",
                "fast_preview",
                "full_after_preview",
            }:
                reasons.append(job.kind)
        return reasons

    def should_confirm_close_program(self) -> bool:
        """True when Close Program would abort a run or pause waiting jobs."""
        if self.job_queue.has_running_or_queued():
            return True
        return any(
            job.status == "running"
            and job.kind in {"prep", "render", "fast_preview", "full_after_preview"}
            for job in self.jobs.list_jobs()
        )

    def autocut_status_line(self) -> str:
        if self.job_queue.has_active_work() or any(
            job.status == "running"
            and job.kind in {"prep", "render", "fast_preview", "full_after_preview"}
            for job in self.jobs.list_jobs()
        ):
            return (
                "Autocut in progress. New Recordings OK. "
                "New Autocuts will be added to queue."
            )
        return ""

    def full_queue_display(self) -> tuple[dict | None, list[dict]]:
        current, waiting = self.job_queue.full_current_and_waiting()
        return (
            current.to_dict() if current else None,
            [entry.to_dict() for entry in waiting],
        )

    def protected_session_folders(self) -> set[Path]:
        folders = set(self.job_queue.protected_folders())
        for job in self.jobs.list_jobs():
            if job.status == "running" and job.session_folder is not None:
                folders.add(job.session_folder.resolve())
        return folders

    def get_recording_phrases(self) -> RecordingPhrases:
        return get_recording_phrases()

    def recording_instructions(self) -> str:
        return recording_instructions()

    def load_session_state(self, working_folder: Path) -> dict:
        return load_session_state(working_folder.resolve())

    def load_flag_report_text(self, working_folder: Path) -> str:
        from app.controller.flag_report import load_flag_report_text

        folder = working_folder.resolve()
        state = load_session_state(folder)
        return load_flag_report_text(state, working_folder=folder)

    def read_prep_progress(
        self,
        working_folder: Path,
        *,
        fallback_started_at=None,
    ):
        folder = working_folder.resolve()
        state = load_session_state(folder)
        return _read_prep_progress(state, folder, fallback_started_at=fallback_started_at)

    def find_running_prep_job(self, working_folder: Path):
        folder = working_folder.resolve()
        for job in self.jobs.list_jobs():
            if job.kind not in ("prep", "fast_preview", "full_after_preview") or job.status != "running":
                continue
            if job.session_folder and job.session_folder.resolve() == folder:
                return job
        return None

    def read_render_progress(
        self,
        working_folder: Path,
        *,
        fallback_started_at=None,
    ):
        folder = working_folder.resolve()
        state = load_session_state(folder)
        return _read_render_progress(
            state,
            folder,
            fallback_started_at=fallback_started_at,
        )

    def find_running_render_job(self, working_folder: Path):
        folder = working_folder.resolve()
        for job in self.jobs.list_jobs():
            if job.kind not in {"render", "full_after_preview"} or job.status != "running":
                continue
            if job.session_folder and job.session_folder.resolve() == folder:
                return job
        return None

    def send_delivery_link_to_email(
        self,
        working_folder: Path,
        email: str,
    ) -> str:
        folder = working_folder.resolve()
        state = load_session_state(folder)
        ensure_scripts_path()
        from harness_deliver_video import send_delivery_link_email

        recipient = send_delivery_link_email(
            state,
            to_addr=email,
            print_fn=lambda _msg: None,
        )
        save_session_state(folder, state)
        return recipient

    def scan_session(
        self,
        scan_dir: Path,
        *,
        date_filter=None,
        cluster_index: int = 0,
    ) -> dict:
        return scan_session_folder(
            scan_dir,
            date_filter=date_filter,
            cluster_index=cluster_index,
        )

    def validate_delivery_email(self, email: str) -> tuple[bool, str]:
        return validate_delivery_email(email)

    def extract_video_previews(self, working_folder: Path) -> dict:
        return extract_video_previews(working_folder)

    def extract_audio_previews(self, working_folder: Path) -> dict:
        return extract_audio_previews(working_folder)

    def apply_labels(
        self,
        working_folder: Path,
        *,
        video_labels: dict[str, str],
        audio_labels: dict[str, str],
        allow_overwrite: bool = False,
        on_copy=None,
        should_cancel=None,
    ) -> dict:
        return apply_labels(
            working_folder,
            video_labels=video_labels,
            audio_labels=audio_labels,
            allow_overwrite=allow_overwrite,
            on_copy=on_copy,
            should_cancel=should_cancel,
        )

    def validate_video_labels(self, labels: dict[str, str]) -> None:
        validate_video_labels(labels)

    def validate_audio_labels(self, labels: dict[str, str]) -> None:
        validate_audio_labels(labels)

    def resolve_one_min_test_path(self, state: dict, working_folder: Path) -> Path:
        return resolve_one_min_test_path(state, working_folder)

    def needs_sync_offset_choice(self, state: dict) -> bool:
        return needs_sync_offset_choice(state)

    def resolve_ab_test_paths(self, state: dict, working_folder: Path) -> tuple[Path, Path]:
        return resolve_ab_test_paths(state, working_folder)

    def record_sync_offset_choice(self, working_folder: Path, choice: str) -> dict:
        return record_sync_offset_choice(working_folder, choice)

    def approve_one_min_test(self, working_folder: Path) -> dict:
        return approve_one_min_test(working_folder)

    def fix_audio_speaker_swap(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> dict:
        return fix_audio_speaker_swap(working_folder, allow_overwrite=allow_overwrite)

    def swap_speaker_ids_toggle(self, working_folder: Path) -> dict:
        return swap_speaker_ids_toggle(working_folder)

    def swap_labeled_files(self, working_folder: Path, *, kind: str) -> dict:
        return swap_labeled_files(working_folder, kind=kind)

    def rerun_one_min_test(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> dict:
        return rerun_one_min_test(working_folder, allow_overwrite=allow_overwrite)

    def read_failure_info(
        self,
        working_folder: Path | None,
        state: dict | None = None,
        *,
        summary: str | None = None,
        detail: str | None = None,
        retry_screen: str | None = None,
        aborted: bool = False,
    ) -> FailureInfo:
        if working_folder is None:
            return FailureInfo(
                summary=summary or "Something went wrong during processing.",
                detail=detail,
                retry_screen=retry_screen or "A1",
                aborted=aborted,
            )
        return read_failure_info(
            working_folder,
            state,
            summary=summary,
            detail=detail,
            retry_screen=retry_screen,
            aborted=aborted,
        )

    def ensure_vmix_step(self):
        ensure_scripts_path()
        from piab_ensure_vmix import ensure_vmix_running

        return ensure_vmix_running()

    def open_vmix_preset_step(self):
        ensure_scripts_path()
        from piab_open_vmix_preset import open_vmix_preset

        return open_vmix_preset()

    def confirm_camera_setup_step(self, continue_event: threading.Event):
        ensure_scripts_path()
        from piab_confirm_camera_setup import confirm_camera_setup

        return confirm_camera_setup(
            use_continue_button_flag=True,
            continue_event=continue_event,
            open_fn=lambda _path: None,
            print_fn=lambda _msg: None,
        )

    def multicorder_is_active(self) -> bool:
        ensure_scripts_path()
        from piab_vmix_api import is_multicorder_active

        try:
            return is_multicorder_active()
        except Exception:
            return False

    def warmup_cameras_for_recording(self) -> dict:
        """Preview-cycle DeckLink cameras so HDMI audio can lock before MultiCorder."""
        ensure_scripts_path()
        from piab_vmix_warmup import warmup_decklink_cameras

        return warmup_decklink_cameras()

    def can_start_recording(self) -> tuple[bool, str]:
        if self.lock.is_recording_active() or self._recording_job_id:
            return False, "A recording session is already active."
        report = self.run_preflight()
        if not report.ok_for_recording:
            failed = [
                c.message
                for c in report.checks
                if c.status == "fail" and "recording" in c.blocks
            ]
            return False, failed[0] if failed else "Recording prerequisites not met."
        return True, ""

    def begin_recording(self, *, already_recording_action: str | None = None) -> Job:
        if self.lock.is_recording_active() or self._recording_job_id:
            raise RuntimeError("A recording session is already active in this app.")

        ok, message = self.can_start_recording()
        if not ok:
            raise RuntimeError(message)

        if self.multicorder_is_active() and already_recording_action is None:
            raise RuntimeError(
                "MultiCorder is already recording. Choose to continue or restart."
            )

        ensure_scripts_path()
        from piab_multicorder_record import prepare_multicorder_recording, stop_multicorder

        def _abort_recording() -> None:
            stop_multicorder()

        job = self.jobs.register_job("recording", abort_hook=_abort_recording)
        self._recording_job_id = job.id
        self._recording_continue.clear()
        self.lock.set_recording_active(True)

        try:
            prepare_multicorder_recording(
                already_recording_action=already_recording_action,
                auto_continue=False,
                input_fn=lambda _prompt: (_ for _ in ()).throw(
                    RuntimeError("Unexpected interactive prompt during begin_recording.")
                ),
                print_fn=lambda _msg: None,
            )
        except Exception as exc:
            self._recording_job_id = None
            self.lock.set_recording_active(False)
            job.status = "failed"
            job.message = str(exc)
            raise RuntimeError(f"Failed to start recording: {exc}") from exc

        return job

    def finish_recording(self) -> Job:
        if not self._recording_job_id:
            raise RuntimeError("No active recording session.")

        job = self.jobs.get_job(self._recording_job_id)
        if job is None:
            raise RuntimeError("Recording job not found.")

        ensure_scripts_path()
        from piab_multicorder_record import stop_multicorder

        try:
            stop_multicorder()
            job.status = "completed"
            job.message = "Recording stopped."
        except Exception as exc:
            job.status = "failed"
            job.message = str(exc)
            raise RuntimeError(f"Failed to stop recording: {exc}") from exc
        finally:
            self._recording_job_id = None
            self.lock.set_recording_active(False)

        return job

    def signal_recording_continue(self) -> None:
        """GUI Continue button during recording wait (future hook)."""
        self._recording_continue.set()

    def init_session(
        self,
        *,
        mode: str,
        name: str | None = None,
        working_folder: Path | None = None,
        allow_overwrite: bool = False,
        delivery_email: str | None = None,
        confirm_delivery_email: bool = False,
        from_scan_data: dict | None = None,
    ) -> Path:
        argv = [sys.executable, str(self.scripts_dir / "piab_init_session.py")]
        scan_json_path: Path | None = None
        if from_scan_data is not None:
            handle = tempfile.NamedTemporaryFile(
                "w",
                suffix=".json",
                delete=False,
                encoding="utf-8",
            )
            json.dump(from_scan_data, handle)
            handle.close()
            scan_json_path = Path(handle.name)
            argv.extend(["--from-scan-json", str(scan_json_path)])
        if mode == "special":
            if working_folder is None:
                raise ValueError("working_folder is required for special mode.")
            folder = working_folder.resolve()
            at_risk = self.check_overwrite_risk("init_session", folder)
            if at_risk and not allow_overwrite:
                raise RuntimeError(
                    f"Refusing to overwrite existing state: {at_risk[0]}"
                )
            argv.extend(["--working-folder", str(folder)])
        elif mode == "default":
            session_name = name or self.generate_session_name()
            folder = self.work_root / session_name
            argv.extend(
                [
                    "--name",
                    session_name,
                    "--root",
                    str(self.work_root),
                    "--scan-root",
                    str(self.scan_root),
                ]
            )
        else:
            raise ValueError(f"Unknown init mode: {mode!r}")

        if allow_overwrite:
            argv.append("--allow-overwrite")
        if delivery_email:
            argv.extend(["--delivery-email", delivery_email])
            if confirm_delivery_email:
                argv.append("--confirm-delivery-email")

        proc = subprocess.run(
            argv,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        if scan_json_path is not None:
            scan_json_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            message = f"piab_init_session.py failed with code {proc.returncode}"
            if detail:
                message = f"{message}:\n{detail}"
            raise RuntimeError(message)

        created = folder.resolve() if mode == "default" else working_folder.resolve()
        remember_session_folder(created)
        return created

    def fast_preview_eligible(self, state: dict) -> bool:
        return fast_preview_eligible_for_state(state)

    def should_start_fast_preview(self, state: dict) -> bool:
        return should_start_fast_preview(state)

    def fast_preview_review_pending(self, state: dict) -> bool:
        return fast_preview_review_pending(state)

    def full_after_preview_pending(self, state: dict) -> bool:
        return full_after_preview_pending(state)

    def start_fast_preview(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> Job:
        args = [str(working_folder.resolve())]
        if allow_overwrite:
            args.append("--allow-overwrite")
        folder = working_folder.resolve()
        job = self.jobs.start_script(
            "fast_preview",
            self.scripts_dir / "piab_run_fast_preview.py",
            args,
            cwd=self.repo_root,
            session_folder=folder,
        )
        self.lock.add_processing_job(job.id)
        self.job_queue.mark_running(folder, "fast_preview")
        return job

    def start_full_after_preview(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> Job:
        args = [str(working_folder.resolve())]
        if allow_overwrite:
            args.append("--allow-overwrite")
        folder = working_folder.resolve()
        job = self.jobs.start_script(
            "full_after_preview",
            self.scripts_dir / "piab_run_full_after_preview.py",
            args,
            cwd=self.repo_root,
            session_folder=folder,
        )
        self.lock.add_processing_job(job.id)
        self.job_queue.mark_running(folder, "full")
        return job

    def approve_fast_preview(self, working_folder: Path) -> dict:
        return approve_fast_preview(working_folder)

    def clear_preview_for_relabel(self, working_folder: Path) -> dict:
        return clear_preview_for_relabel(working_folder)

    def start_prep(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
        resume: bool = False,
    ) -> Job:
        at_risk = self.check_overwrite_risk("run_prep", working_folder)
        if at_risk and not allow_overwrite:
            raise RuntimeError(
                "Existing outputs would be overwritten. "
                f"Pass allow_overwrite=True after user confirmation. "
                f"First: {at_risk[0]}"
            )

        args = [str(working_folder.resolve())]
        if resume:
            args.append("--resume")
        if allow_overwrite:
            args.append("--allow-overwrite")

        job = self.jobs.start_script(
            "prep",
            self.scripts_dir / "piab_run_prep.py",
            args,
            cwd=self.repo_root,
            session_folder=working_folder.resolve(),
        )
        self.lock.add_processing_job(job.id)
        return job

    def start_render(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> Job:
        at_risk = self.check_overwrite_risk("run_render", working_folder)
        if at_risk and not allow_overwrite:
            raise RuntimeError(
                "Existing outputs would be overwritten. "
                f"Pass allow_overwrite=True after user confirmation. "
                f"First: {at_risk[0]}"
            )

        args = [str(working_folder.resolve())]
        if allow_overwrite:
            args.append("--allow-overwrite")

        folder = working_folder.resolve()
        job = self.jobs.start_script(
            "render",
            self.scripts_dir / "piab_run_full_render.py",
            args,
            cwd=self.repo_root,
            session_folder=folder,
        )
        self.lock.add_processing_job(job.id)
        self.job_queue.mark_running(folder, "full")
        return job

    def abort_job(
        self,
        job_id: str,
        *,
        confirmed: bool,
        advance_queue: bool = True,
    ) -> AbortResult:
        job = self.jobs.get_job(job_id)
        result = self.jobs.abort_job(job_id, confirmed=confirmed)
        if result.status == "aborted":
            self.lock.remove_processing_job(job_id)
            if job_id == self._recording_job_id:
                self._recording_job_id = None
                self.lock.set_recording_active(False)
            if job is not None and job.session_folder is not None:
                if job.kind != "recording":
                    try:
                        record_session_abort(
                            job.session_folder,
                            message=result.message or "Aborted by user.",
                        )
                    except Exception:
                        pass
                lane = self._lane_for_kind(job.kind)
                if lane is not None:
                    if advance_queue:
                        self.job_queue.cancel(job.session_folder, lane)
                        self.start_next_queued(lane)
                    else:
                        self.job_queue.mark_interrupted(job.session_folder, lane)
        return result

    def record_session_abort(
        self,
        working_folder: Path,
        *,
        message: str = "Aborted by user.",
    ) -> dict:
        return record_session_abort(working_folder, message=message)

    def hold_queued_job(self, working_folder: Path, lane: Lane) -> bool:
        """Remove a waiting job from auto-process without aborting the session."""
        return self.job_queue.hold(working_folder.resolve(), lane) is not None

    def list_held_jobs(self) -> list[dict]:
        return [entry.to_dict() for entry in self.job_queue.held()]

    def resume_held_job(self, working_folder: Path, lane: Lane) -> Job | None:
        folder = working_folder.resolve()
        entry = self.job_queue.entry_for(folder, lane)
        if entry is None or entry.status != "held":
            return None
        self.job_queue.enqueue(folder, lane, name=entry.name)
        return self.start_next_queued(lane)

    def cancel_queued_job(self, working_folder: Path, lane: Lane) -> bool:
        folder = working_folder.resolve()
        cancelled = self.job_queue.cancel(folder, lane)
        if cancelled:
            try:
                record_session_abort(
                    folder,
                    message="Cancelled from queue before the job started.",
                )
            except Exception:
                pass
            self.start_next_queued(lane)
        return cancelled

    def poll_jobs(self) -> list[Job]:
        finished = self.jobs.poll()
        for job in finished:
            if job.kind in {"prep", "render", "fast_preview", "full_after_preview"}:
                self.lock.remove_processing_job(job.id)
            if job.session_folder is None:
                continue
            lane = self._lane_for_kind(job.kind)
            if lane is None:
                continue
            if job.status == "completed":
                self.job_queue.complete(job.session_folder, lane)
                self.start_next_queued(lane)
            elif job.status == "failed":
                self.job_queue.mark_failed(job.session_folder, lane)
        return finished

    def get_job(self, job_id: str) -> Job | None:
        return self.jobs.get_job(job_id)

    def list_jobs(self) -> list[Job]:
        return self.jobs.list_jobs()

    def list_clean_working_candidates(self) -> list[dict]:
        from app.controller.clean_working import list_clean_working_candidates

        protected = {str(p) for p in self.protected_session_folders()}
        rows = list_clean_working_candidates()
        return [
            row
            for row in rows
            if str(Path(str(row.get("project_folder") or "")).resolve()) not in protected
        ]

    def scan_lost_clean_sessions(self) -> list[dict]:
        from app.controller.clean_working import scan_lost_clean_sessions

        protected = {str(p) for p in self.protected_session_folders()}
        rows = scan_lost_clean_sessions()
        return [
            row
            for row in rows
            if str(Path(str(row.get("project_folder") or "")).resolve()) not in protected
        ]

    def clean_working_files(self, project_folders: list[Path]) -> list[dict]:
        from app.controller.clean_working import clean_working_files

        protected = self.protected_session_folders()
        allowed = []
        for folder in project_folders:
            resolved = Path(folder).resolve()
            if resolved in protected:
                raise RuntimeError(
                    f"Cannot clean {resolved.name}: that session is queued or running."
                )
            allowed.append(resolved)
        results = clean_working_files(allowed)
        for folder in allowed:
            self.job_queue.cancel(folder, "full")
            self.job_queue.cancel(folder, "fast_preview")
        return results

    def _lane_for_kind(self, kind: JobKind) -> Lane | None:
        if kind == "fast_preview":
            return "fast_preview"
        if kind in FULL_KINDS:
            return "full"
        return None

    def _session_name(self, folder: Path) -> str:
        try:
            state = load_session_state(folder)
            return str(state.get("name") or folder.name)
        except Exception:
            return folder.name

    def request_fast_preview(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> Job | None:
        folder = working_folder.resolve()
        self.job_queue.enqueue(folder, "fast_preview", name=self._session_name(folder))
        started = self.start_next_queued(lane="fast_preview", allow_overwrite=allow_overwrite)
        if started and started.session_folder and started.session_folder.resolve() == folder:
            return started
        return None

    def request_full_job(
        self,
        working_folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> Job | None:
        folder = working_folder.resolve()
        self.job_queue.enqueue(folder, "full", name=self._session_name(folder))
        started = self.start_next_queued(lane="full", allow_overwrite=allow_overwrite)
        if started and started.session_folder and started.session_folder.resolve() == folder:
            return started
        return None

    def _start_full_lane_job(
        self,
        folder: Path,
        *,
        allow_overwrite: bool = False,
    ) -> Job:
        try:
            state = load_session_state(folder)
        except Exception:
            state = {}
        if self.full_after_preview_pending(state) or state.get("fast_preview_approval"):
            return self.start_full_after_preview(folder, allow_overwrite=allow_overwrite)
        return self.start_render(folder, allow_overwrite=allow_overwrite)

    def start_next_queued(
        self,
        lane: Lane,
        *,
        allow_overwrite: bool = False,
    ) -> Job | None:
        if lane == "fast_preview" and self.jobs.running_fast_preview_count():
            return None
        if lane == "full" and self.jobs.running_full_count():
            return None
        # Queue can stay "running" after a process exits if poll missed it.
        self.job_queue.requeue_stale_running(lane)
        rows = self.job_queue.load()[lane]
        if any(e.status in {"failed", "interrupted"} for e in rows):
            return None
        entry = self.job_queue.next_queued(lane)
        if entry is None:
            return None
        folder = entry.folder_path
        if lane == "fast_preview":
            return self.start_fast_preview(folder, allow_overwrite=allow_overwrite)
        return self._start_full_lane_job(folder, allow_overwrite=allow_overwrite)

    def interrupt_running_for_quit(self) -> None:
        for job in self.jobs.list_jobs():
            if job.status == "running" and job.kind != "recording":
                self.abort_job(job.id, confirmed=True, advance_queue=False)
        self.job_queue.interrupt_running()

    def list_interrupted_jobs(self) -> list[dict]:
        return [entry.to_dict() for entry in self.job_queue.interrupted()]

    def resume_interrupted(self, working_folder: Path, lane: Lane) -> Job | None:
        folder = working_folder.resolve()
        entry = self.job_queue.entry_for(folder, lane)
        if entry is None or entry.status != "interrupted":
            return None
        if lane == "fast_preview":
            if self.jobs.running_fast_preview_count():
                return None
            return self.start_fast_preview(folder, allow_overwrite=True)
        if self.jobs.running_full_count():
            return None
        return self._start_full_lane_job(folder, allow_overwrite=True)

    def abort_interrupted(self, working_folder: Path, lane: Lane) -> None:
        self.job_queue.cancel(working_folder.resolve(), lane)
        self.start_next_queued(lane)
