import shutil
import subprocess

import psutil

from src.models.desktop import Rect, WindowInfo


def _active_window_id() -> int | None:
    if shutil.which("xdotool") is not None:
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

    if shutil.which("xprop") is None:
        return None

    try:
        result = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0 or "#" not in result.stdout:
        return None

    _, value = result.stdout.split("#", maxsplit=1)
    token = value.strip().split()[0]
    if token == "0x0":
        return None

    try:
        return int(token, 16)
    except ValueError:
        return None


def _app_name_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return f"pid-{pid}"


def _window_class_for_id(window_id: int) -> str:
    if shutil.which("xdotool") is None:
        return ""
    try:
        result = subprocess.run(
            ["xdotool", "getwindowclassname", str(window_id)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _parse_wmctrl_line(line: str, active_window_id: int | None) -> WindowInfo | None:
    parts = line.split(None, 8)
    if len(parts) < 9:
        return None

    window_id_text, _desktop, pid_text, x_text, y_text, width_text, height_text, _host, title = parts
    if title.strip() == "":
        return None

    try:
        window_id = int(window_id_text, 16)
        pid = int(pid_text)
        x = int(x_text)
        y = int(y_text)
        width = int(width_text)
        height = int(height_text)
    except ValueError:
        return None

    if width <= 0 or height <= 0:
        return None

    app_name = _app_name_for_pid(pid)
    return WindowInfo(
        window_id=window_id,
        title=title.strip(),
        app_name=app_name,
        pid=pid,
        bounds=Rect(x=x, y=y, width=width, height=height),
        is_active=active_window_id == window_id,
        is_visible=True,
        layer=0,
    )


async def get_windows() -> list[WindowInfo]:
    if shutil.which("wmctrl") is None:
        return []

    try:
        result = subprocess.run(
            ["wmctrl", "-lpG"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    active_window_id = _active_window_id()
    windows: list[WindowInfo] = []
    for line in result.stdout.splitlines():
        info = _parse_wmctrl_line(line, active_window_id)
        if info is not None:
            windows.append(info)
    return windows


async def get_active_window() -> WindowInfo | None:
    active_id = _active_window_id()
    if active_id is None:
        return None
    for window in await get_windows():
        if int(window.window_id) == active_id:
            window.is_active = True
            return window
    return None


async def activate_window(window: WindowInfo) -> bool:
    if shutil.which("wmctrl") is None and shutil.which("xdotool") is None:
        return False

    if shutil.which("xdotool") is not None:
        try:
            result = subprocess.run(
                ["xdotool", "windowactivate", "--sync", str(window.window_id)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    window_id = hex(int(window.window_id))
    try:
        result = subprocess.run(["wmctrl", "-ia", window_id], capture_output=True, text=True, timeout=5, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


async def get_window_class_name(window: WindowInfo) -> str:
    return _window_class_for_id(int(window.window_id))


async def is_window_responsive(window: WindowInfo) -> bool:
    if shutil.which("xdotool") is None:
        return True
    try:
        result = subprocess.run(
            ["xdotool", "getwindowfocus", "--name"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
