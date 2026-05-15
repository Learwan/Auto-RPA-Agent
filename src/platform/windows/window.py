import ctypes
from ctypes import wintypes
from functools import lru_cache

import psutil

from src.models.desktop import Rect, WindowInfo


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


@lru_cache(maxsize=1)
def _user32():
    return ctypes.windll.user32


@lru_cache(maxsize=1)
def _kernel32():
    return ctypes.windll.kernel32


def _window_title(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _window_rect(hwnd: int) -> Rect | None:
    rect = _RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = max(0, rect.right - rect.left)
    height = max(0, rect.bottom - rect.top)
    if width == 0 or height == 0:
        return None
    return Rect(x=int(rect.left), y=int(rect.top), width=int(width), height=int(height))


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _window_layer(hwnd: int) -> int:
    owner = _user32().GetWindow(hwnd, 4)
    if owner != 0:
        return 1
    ex_style = _user32().GetWindowLongW(hwnd, -20)
    if ex_style & 0x00000080:
        return 3
    if ex_style & 0x00000008:
        return 2
    return 0


def _app_name_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""


def _build_window_info(hwnd: int, active_hwnd: int) -> WindowInfo | None:
    user32 = _user32()
    if not user32.IsWindowVisible(hwnd):
        return None

    title = _window_title(hwnd)
    if not title:
        return None

    bounds = _window_rect(hwnd)
    if bounds is None:
        return None

    pid = _window_pid(hwnd)
    app_name = _app_name_for_pid(pid)
    if not app_name:
        return None

    return WindowInfo(
        window_id=int(hwnd),
        title=title,
        app_name=app_name,
        pid=pid,
        bounds=bounds,
        is_active=int(hwnd) == int(active_hwnd),
        is_visible=True,
        layer=_window_layer(hwnd),
    )


async def get_windows() -> list[WindowInfo]:
    user32 = _user32()
    active_hwnd = int(user32.GetForegroundWindow())
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_windows_proc(hwnd, _lparam):
        info = _build_window_info(int(hwnd), active_hwnd)
        if info is not None:
            windows.append(info)
        return True

    user32.EnumWindows(_enum_windows_proc, 0)
    return windows


async def get_active_window() -> WindowInfo | None:
    user32 = _user32()
    active_hwnd = int(user32.GetForegroundWindow())
    if active_hwnd == 0:
        return None
    return _build_window_info(active_hwnd, active_hwnd)


async def activate_window(window: WindowInfo) -> bool:
    hwnd = int(window.window_id)
    user32 = _user32()
    if not user32.IsWindow(hwnd):
        return False
    user32.ShowWindow(hwnd, 5)
    return bool(user32.SetForegroundWindow(hwnd))


async def get_window_class_name(window: WindowInfo) -> str:
    return _window_class(int(window.window_id))


async def is_window_responsive(window: WindowInfo) -> bool:
    hwnd = int(window.window_id)
    result = ctypes.c_bool(False)
    _user32().SendMessageTimeoutW(hwnd, 0x0000, 0, 0, 0x0002, 1000, ctypes.byref(result))
    return True
