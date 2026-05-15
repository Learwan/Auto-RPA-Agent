import psutil

from src.models.desktop import ElementCriteria, ProcessInfo, Rect, UIElement, WindowInfo
from src.platform.base import BasePlatformAdapter
from src.platform.windows.accessibility import find_element_by_criteria, get_element_at_position, get_focused_element
from src.platform.windows.window import activate_window, get_active_window, get_windows
from src.platform.screenshot import mss_screenshot_to_png_bytes


class WindowsAdapter(BasePlatformAdapter):
    async def get_windows(self) -> list[WindowInfo]:
        return await get_windows()

    async def get_active_window(self) -> WindowInfo | None:
        return await get_active_window()

    async def get_processes(self) -> list[ProcessInfo]:
        result = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status", "create_time"]):
            try:
                info = proc.info
                mem = info.get("memory_info")
                result.append(
                    ProcessInfo(
                        pid=info["pid"],
                        name=info["name"] or "",
                        cpu_percent=info.get("cpu_percent", 0.0) or 0.0,
                        memory_mb=(mem.rss / 1024 / 1024) if mem else 0.0,
                        status=info.get("status", "unknown") or "unknown",
                        create_time=info.get("create_time", 0.0) or 0.0,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return result

    async def get_focused_element(self) -> UIElement | None:
        return await get_focused_element()

    async def capture_screen(self, region: Rect | None = None) -> bytes:
        import mss

        with mss.MSS() as sct:
            monitor = (
                sct.monitors[0]
                if not region
                else {
                    "left": region.x,
                    "top": region.y,
                    "width": region.width,
                    "height": region.height,
                }
            )
            return mss_screenshot_to_png_bytes(sct.grab(monitor))

    async def get_element_at_position(self, x: int, y: int) -> UIElement | None:
        return await get_element_at_position(x, y)

    async def find_element(self, criteria: ElementCriteria) -> UIElement | None:
        return await find_element_by_criteria(criteria)

    async def activate_window(self, window: WindowInfo) -> bool:
        return await activate_window(window)

    def check_permissions(self) -> dict[str, bool]:
        try:
            _ = find_element_by_criteria
            accessibility = True
        except Exception:
            accessibility = False
        return {"accessibility": accessibility, "screen_recording": True}

    def get_platform_name(self) -> str:
        return "windows"

    async def get_screen_size(self) -> tuple[int, int]:
        import mss

        with mss.MSS() as sct:
            monitor = sct.monitors[0]
            return monitor["width"], monitor["height"]
