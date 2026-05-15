import mss
import psutil

from src.models.desktop import ElementCriteria, ProcessInfo, Rect, UIElement, WindowInfo
from src.platform.base import BasePlatformAdapter
from src.platform.macos.accessibility import find_element_by_criteria, get_element_at_position, get_focused_element
from src.platform.macos.window import get_active_window, get_windows
from src.platform.screenshot import mss_screenshot_to_png_bytes


class MacOSAdapter(BasePlatformAdapter):
    def __init__(self):
        self._dpi_scale_cache: float | None = None

    async def get_dpi_scale(self) -> float:
        if self._dpi_scale_cache is not None:
            return self._dpi_scale_cache
        try:
            import Quartz

            main_id = Quartz.CGMainDisplayID()
            physical_w = Quartz.CGDisplayPixelsWide(main_id)
            logical_w = Quartz.CGDisplayBounds(main_id).size.width
            self._dpi_scale_cache = physical_w / logical_w if logical_w > 0 else 1.0
        except ImportError:
            try:
                from AppKit import NSScreen

                self._dpi_scale_cache = NSScreen.mainScreen().backingScaleFactor()
            except ImportError:
                self._dpi_scale_cache = 1.0
        return self._dpi_scale_cache

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
        with mss.MSS() as sct:
            if region:
                monitor = {
                    "left": region.x,
                    "top": region.y,
                    "width": region.width,
                    "height": region.height,
                }
            else:
                monitor = sct.monitors[0]
            screenshot = sct.grab(monitor)
            return mss_screenshot_to_png_bytes(screenshot)

    async def get_element_at_position(self, x: int, y: int) -> UIElement | None:
        return await get_element_at_position(x, y)

    async def find_element(self, criteria: ElementCriteria) -> UIElement | None:
        criteria_dict = {}
        if criteria.accessibility_id:
            criteria_dict["accessibility_id"] = criteria.accessibility_id
        if criteria.title:
            criteria_dict["title"] = criteria.title
        if criteria.role:
            criteria_dict["role"] = criteria.role
        if criteria.class_name:
            criteria_dict["class_name"] = criteria.class_name
        if criteria.text_contains:
            criteria_dict["text_contains"] = criteria.text_contains
        return await find_element_by_criteria(criteria_dict)

    def check_permissions(self) -> dict[str, bool]:
        accessibility = False
        try:
            from ApplicationServices import AXIsProcessTrusted

            accessibility = AXIsProcessTrusted()
        except ImportError:
            pass
        return {"accessibility": accessibility, "screen_recording": True}

    def get_platform_name(self) -> str:
        return "macos"

    async def get_screen_size(self) -> tuple[int, int]:
        with mss.MSS() as sct:
            monitor = sct.monitors[0]
            return monitor["width"], monitor["height"]
