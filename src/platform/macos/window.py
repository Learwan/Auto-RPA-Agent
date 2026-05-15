import subprocess

from src.models.desktop import Rect, WindowInfo
from src.platform.macos.browser_context import enrich_browser_window

_SYSTEM_OVERLAY_APPS = {
    "Control Center",
    "ControlCenter",
    "Dock",
    "NotificationCenter",
    "SystemUIServer",
    "Wallpaper",
    "wallpaperd",
    "控制中心",
    "程序坞",
    "通知中心",
    "墙纸",
}
_OVERLAY_WINDOW_TITLES = {
    "Fullscreen Backdrop",
    "Offscreen Wallpaper Window",
    "Recording HUD",
}
_OVERLAY_WINDOW_PREFIXES = (
    "Wallpaper-",
)


def _window_area(window: WindowInfo) -> int:
    return max(window.bounds.width, 0) * max(window.bounds.height, 0)


def _is_overlay_window(window: WindowInfo) -> bool:
    if window.app_name in _SYSTEM_OVERLAY_APPS:
        return True
    if window.title in _OVERLAY_WINDOW_TITLES:
        return True
    return any(window.title.startswith(prefix) for prefix in _OVERLAY_WINDOW_PREFIXES)


def _window_sort_key(window: WindowInfo) -> tuple[int, int, int]:
    is_regular_layer = 1 if window.layer <= 3 else 0
    distinct_title = 1 if window.title.strip() and window.title.strip() != window.app_name.strip() else 0
    return (is_regular_layer, distinct_title, _window_area(window))


def _pick_best_window(windows: list[WindowInfo], preferred_app: str | None = None) -> WindowInfo | None:
    if not windows:
        return None

    if preferred_app and preferred_app not in _SYSTEM_OVERLAY_APPS:
        same_app = [window for window in windows if window.app_name == preferred_app]
        if same_app:
            return max(same_app, key=_window_sort_key)

    non_overlay = [window for window in windows if not _is_overlay_window(window)]
    if preferred_app:
        preferred_non_overlay = [window for window in non_overlay if window.app_name == preferred_app]
        if preferred_non_overlay:
            return max(preferred_non_overlay, key=_window_sort_key)

    if non_overlay:
        return max(non_overlay, key=_window_sort_key)

    if preferred_app:
        same_app = [window for window in windows if window.app_name == preferred_app]
        if same_app:
            return max(same_app, key=_window_sort_key)

    return max(windows, key=_window_sort_key)


async def get_windows() -> list[WindowInfo]:
    try:
        from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionOnScreenOnly

        window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        result = []
        for win in window_list:
            bounds = win.get("kCGWindowBounds", {})
            if not bounds or win.get("kCGWindowIsOnscreen") is False:
                continue
            owner = win.get("kCGWindowOwnerName", "")
            name = win.get("kCGWindowName", "")
            if not owner:
                continue
            if owner in ("Window Server", "Dock", "SystemUIServer"):
                continue
            w = WindowInfo(
                window_id=win.get("kCGWindowNumber", 0),
                title=name or owner,
                app_name=owner,
                pid=win.get("kCGWindowOwnerPID", 0),
                bounds=Rect(
                    x=int(bounds.get("X", 0)),
                    y=int(bounds.get("Y", 0)),
                    width=int(bounds.get("Width", 0)),
                    height=int(bounds.get("Height", 0)),
                ),
                is_active=win.get("kCGWindowAlpha", 0) > 0,
                is_visible=True,
                layer=win.get("kCGWindowLayer", 0),
            )
            result.append(w)
        return result
    except ImportError:
        return []


async def get_active_window() -> WindowInfo | None:
    windows = await get_windows()
    if not windows:
        return None
    try:
        script = 'tell application "System Events" to get name of first process whose frontmost is true'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            active_app = result.stdout.strip()
            active_window = _pick_best_window(windows, preferred_app=active_app)
            if active_window is not None:
                active_window = enrich_browser_window(active_window)
                active_window.is_active = True
                return active_window
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    active_window = _pick_best_window(windows)
    if active_window is not None:
        active_window = enrich_browser_window(active_window)
        active_window.is_active = True
    return active_window


async def activate_window(window: WindowInfo) -> bool:
    try:
        script = f'''
        tell application "{window.app_name}"
            activate
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
