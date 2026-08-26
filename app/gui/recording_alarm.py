"""Urgent B4 alarm when MultiCorder stops or the vMix API dies."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QWidget

RECORDING_STOPPED_TEXT = "Recording has stopped"
RECORDING_ALARM_URGENT_MS = 2000
_ALARM_FLASH_ON = "background-color: #dc2626;"
_ALARM_FLASH_OFF = "background-color: #111111;"
_ALARM_LABEL_STYLE = (
    "color: #ffffff; font-size: 64px; font-weight: 900;"
)

_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SW_RESTORE = 9
_SW_SHOW = 5
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_SHOWWINDOW = 0x0040
_SWP_NOACTIVATE = 0x0010
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002
_FLASHW_STOP = 0
_FLASHW_ALL = 3
_FLASHW_TIMER = 4


def alarm_flash_stylesheet(*, on: bool) -> str:
    return _ALARM_FLASH_ON if on else _ALARM_FLASH_OFF


def alarm_label_style() -> str:
    return _ALARM_LABEL_STYLE


def bring_window_to_front(widget: QWidget, *, steal_focus: bool = True) -> None:
    """Force the alarm window above other apps. Qt raise_ is not enough on Windows."""
    _raise_widget(
        widget,
        steal_focus=steal_focus,
        stay_topmost=True,
        flash=True,
    )


def raise_app_window(widget: QWidget) -> None:
    """Bring PIAB in front of other apps (e.g. vMix) without staying always-on-top."""
    _raise_widget(
        widget,
        steal_focus=True,
        stay_topmost=False,
        flash=False,
    )


def _raise_widget(
    widget: QWidget,
    *,
    steal_focus: bool,
    stay_topmost: bool,
    flash: bool,
) -> None:
    window = widget.window()
    if window is None:
        return
    window.show()
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
    app = QApplication.instance()
    if app is not None:
        app.setActiveWindow(window)
        if flash:
            app.alert(window, 0)
    _win32_force_to_front(
        window,
        topmost=True,
        steal_focus=steal_focus,
        drop_topmost=not stay_topmost,
        flash=flash,
    )


def release_alarm_window(widget: QWidget) -> None:
    """Drop always-on-top and stop taskbar flashing when the alarm ends."""
    window = widget.window()
    if window is None:
        return
    _win32_force_to_front(window, topmost=False, steal_focus=False)
    _flash_taskbar(window, enabled=False)


def stop_taskbar_flash(widget: QWidget) -> None:
    window = widget.window()
    if window is None:
        return
    _flash_taskbar(window, enabled=False)


def start_alarm_sound() -> None:
    if sys.platform != "win32":
        app = QApplication.instance()
        if app is not None:
            app.beep()
        return
    import winsound

    winsound.PlaySound(
        "SystemHand",
        winsound.SND_ALIAS
        | winsound.SND_ASYNC
        | winsound.SND_LOOP
        | winsound.SND_NODEFAULT,
    )


def stop_alarm_sound() -> None:
    if sys.platform != "win32":
        return
    import winsound

    winsound.PlaySound(None, winsound.SND_PURGE)


def ping_alarm_beep() -> None:
    """Extra hit if the looping alias is muted or unavailable."""
    if sys.platform != "win32":
        app = QApplication.instance()
        if app is not None:
            app.beep()
        return
    import winsound

    winsound.MessageBeep(winsound.MB_ICONHAND)


def _native_hwnd(window: QWidget) -> int:
    handle = window.windowHandle()
    if handle is not None:
        hwnd = int(handle.winId())
        if hwnd:
            return hwnd
    return int(window.winId())


def _win32_force_to_front(
    window: QWidget,
    *,
    topmost: bool,
    steal_focus: bool,
    drop_topmost: bool = False,
    flash: bool = True,
) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = _native_hwnd(window)
        if hwnd <= 0:
            return
        hwnd_val = wintypes.HWND(hwnd)

        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]

        user32.ShowWindow(hwnd_val, _SW_RESTORE)
        user32.ShowWindow(hwnd_val, _SW_SHOW)
        user32.SetWindowPos(
            hwnd_val,
            wintypes.HWND(_HWND_TOPMOST if topmost else _HWND_NOTOPMOST),
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW,
        )
        if not topmost:
            user32.SetWindowPos(
                hwnd_val,
                wintypes.HWND(_HWND_NOTOPMOST),
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
            )
            return

        if not steal_focus:
            _flash_taskbar(window, enabled=True)
            return

        foreground = user32.GetForegroundWindow()
        pid = wintypes.DWORD(0)
        foreground_tid = user32.GetWindowThreadProcessId(
            foreground, ctypes.byref(pid)
        )
        our_tid = kernel32.GetCurrentThreadId()
        attached = False
        if foreground_tid and foreground_tid != our_tid:
            attached = bool(
                user32.AttachThreadInput(our_tid, foreground_tid, True)
            )
        try:
            # Alt key lets a background process pass the foreground lock.
            user32.keybd_event(_VK_MENU, 0, 0, 0)
            user32.BringWindowToTop(hwnd_val)
            user32.SetForegroundWindow(hwnd_val)
            if hasattr(user32, "SetActiveWindow"):
                user32.SetActiveWindow.argtypes = [wintypes.HWND]
                user32.SetActiveWindow(hwnd_val)
            user32.SwitchToThisWindow(hwnd_val, True)
            user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
        finally:
            if attached:
                user32.AttachThreadInput(our_tid, foreground_tid, False)
        if drop_topmost:
            user32.SetWindowPos(
                hwnd_val,
                wintypes.HWND(_HWND_NOTOPMOST),
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW,
            )
        if flash:
            _flash_taskbar(window, enabled=True)
    except Exception:
        return


def _flash_taskbar(window: QWidget, *, enabled: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = _native_hwnd(window)
        if hwnd <= 0:
            return

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hwnd", wintypes.HWND),
                ("dwFlags", ctypes.c_uint),
                ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint),
            ]

        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = hwnd
        info.dwFlags = (_FLASHW_ALL | _FLASHW_TIMER) if enabled else _FLASHW_STOP
        info.uCount = 0
        info.dwTimeout = 0
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        return
