"""Windows custom-scheme handler for Frame.io Native App OAuth."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAPTURE_PATH = REPO_ROOT / ".frameio-oauth-capture.json"
HANDLER_SCRIPT = Path(__file__).resolve().parent / "harness_frameio_oauth.py"
KEEPALIVE_TASK_NAME = "PIAB Frame.io OAuth keep-alive"
KEEPALIVE_TASK_START = "2026-01-04T10:00:00"  # Sunday; StartWhenAvailable catches misses.


def scheme_from_redirect_uri(redirect_uri: str) -> str:
    parsed = redirect_uri.split("://", 1)
    if len(parsed) != 2 or not parsed[0]:
        raise ValueError(f"Invalid redirect URI: {redirect_uri}")
    return parsed[0]


def protocol_handler_command(*, python_exe: str | None = None) -> str:
    py = python_exe or sys.executable
    return f"\"{py}\" \"{HANDLER_SCRIPT}\" capture \"%1\""


def register_protocol_handler(*, scheme: str, python_exe: str | None = None) -> None:
    if sys.platform != "win32":
        raise OSError("Protocol registration is only supported on Windows.")

    import winreg

    command = protocol_handler_command(python_exe=python_exe)
    base = f"Software\\Classes\\{scheme}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
        winreg.SetValue(key, "", winreg.REG_SZ, f"URL:{scheme}")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        f"{base}\\shell\\open\\command",
    ) as key:
        winreg.SetValue(key, "", winreg.REG_SZ, command)


def unregister_protocol_handler(*, scheme: str) -> None:
    if sys.platform != "win32":
        raise OSError("Protocol registration is only supported on Windows.")

    import winreg

    base = f"Software\\Classes\\{scheme}"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{base}\\shell\\open\\command")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{base}\\shell\\open")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{base}\\shell")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base)
    except FileNotFoundError:
        pass


def clear_capture(path: Path = DEFAULT_CAPTURE_PATH) -> None:
    if path.is_file():
        path.unlink()


def write_capture(
    *,
    code: str,
    state: str | None,
    raw: str,
    path: Path = DEFAULT_CAPTURE_PATH,
) -> None:
    payload = {
        "code": code,
        "state": state,
        "raw": raw,
        "captured_at": time.time(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def wait_for_capture(
    *,
    timeout_sec: float,
    path: Path = DEFAULT_CAPTURE_PATH,
    poll_sec: float = 0.25,
) -> dict[str, str | None]:
    end = time.time() + timeout_sec
    while time.time() < end:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            path.unlink()
            code = str(payload.get("code") or "").strip()
            if not code:
                raise ValueError("Capture file did not contain an authorization code.")
            state = payload.get("state")
            return {"code": code, "state": str(state).strip() if state else None}
        time.sleep(poll_sec)
    raise TimeoutError(
        "Timed out waiting for Adobe to open the PIAB OAuth handler. "
        "After sign-in, allow the browser to open the app when prompted."
    )


def keepalive_pythonw(executable: Path | None = None) -> Path:
    exe = Path(executable or sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.is_file():
        return candidate
    return exe


def keepalive_task_command(
    *,
    python_exe: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[str, str, str]:
    """Return (command, arguments, working_directory) for the weekly task."""
    root = (repo_root or REPO_ROOT).resolve()
    launcher = keepalive_pythonw(python_exe)
    script = (Path(__file__).resolve().parent / "harness_frameio_oauth.py").resolve()
    args = f'"{script}" keep-alive'
    return str(launcher), args, str(root)


def keepalive_task_xml(
    *,
    python_exe: Path | None = None,
    repo_root: Path | None = None,
    start_boundary: str = KEEPALIVE_TASK_START,
) -> str:
    command, arguments, working = keepalive_task_command(
        python_exe=python_exe,
        repo_root=repo_root,
    )
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <URI>\\{xml_escape(KEEPALIVE_TASK_NAME)}</URI>
    <Description>Refresh Frame.io Adobe OAuth so PIAB delivery login does not expire after 14 days idle.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{xml_escape(start_boundary)}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Sunday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml_escape(command)}</Command>
      <Arguments>{xml_escape(arguments)}</Arguments>
      <WorkingDirectory>{xml_escape(working)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks_kwargs() -> dict:
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    kwargs: dict = {"capture_output": True, "text": True}
    if sys.platform == "win32" and flags:
        kwargs["creationflags"] = flags
    return kwargs


def keepalive_task_installed(*, task_name: str = KEEPALIVE_TASK_NAME) -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        **_schtasks_kwargs(),
    )
    return result.returncode == 0


def install_keepalive_task(
    *,
    python_exe: Path | None = None,
    repo_root: Path | None = None,
    task_name: str = KEEPALIVE_TASK_NAME,
) -> None:
    if sys.platform != "win32":
        raise OSError("Frame.io keep-alive scheduled task is Windows-only.")
    xml_text = keepalive_task_xml(python_exe=python_exe, repo_root=repo_root)
    xml_path = (repo_root or REPO_ROOT).resolve() / ".frameio-oauth-keepalive-task.xml"
    xml_path.write_text(xml_text, encoding="utf-16")
    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
            **_schtasks_kwargs(),
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(err or f"schtasks failed ({result.returncode})")
    finally:
        xml_path.unlink(missing_ok=True)


def uninstall_keepalive_task(*, task_name: str = KEEPALIVE_TASK_NAME) -> bool:
    if sys.platform != "win32":
        return False
    if not keepalive_task_installed(task_name=task_name):
        return False
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        **_schtasks_kwargs(),
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err or f"schtasks delete failed ({result.returncode})")
    return True


def ensure_keepalive_task(
    *,
    python_exe: Path | None = None,
    repo_root: Path | None = None,
) -> str:
    """Install or refresh the weekly task. Returns installed|updated|skipped."""
    if sys.platform != "win32":
        return "skipped"
    existed = keepalive_task_installed()
    install_keepalive_task(python_exe=python_exe, repo_root=repo_root)
    return "updated" if existed else "installed"
