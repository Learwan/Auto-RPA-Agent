import asyncio
import logging
import time

from src.models.desktop import DesktopState, ProcessInfo, Rect, UIElement, WindowInfo
from src.platform.base import BasePlatformAdapter
from src.platform.factory import create_platform_adapter

logger = logging.getLogger(__name__)


class DesktopCollector:
    def __init__(self):
        self._adapter: BasePlatformAdapter = create_platform_adapter()

    async def collect_windows(self) -> list[WindowInfo]:
        return await self._adapter.get_windows()

    async def collect_active_window(self) -> WindowInfo | None:
        return await self._adapter.get_active_window()

    async def collect_processes(self) -> list[ProcessInfo]:
        return await self._adapter.get_processes()

    async def collect_focused_element(self) -> UIElement | None:
        return await self._adapter.get_focused_element()

    async def capture_screen(self, region: Rect | None = None) -> bytes:
        return await self._adapter.capture_screen(region)

    async def collect_full_state(self) -> DesktopState:
        windows_task = asyncio.ensure_future(self.collect_windows())
        active_task = asyncio.ensure_future(self.collect_active_window())
        processes_task = asyncio.ensure_future(self.collect_processes())
        element_task = asyncio.ensure_future(self.collect_focused_element())

        await asyncio.gather(windows_task, active_task, processes_task, element_task, return_exceptions=True)

        windows = windows_task.result() if not windows_task.exception() else []
        active_window = active_task.result() if not active_task.exception() else None
        processes = processes_task.result() if not processes_task.exception() else []
        focused_element = element_task.result() if not element_task.exception() else None

        return DesktopState(
            windows=windows,
            active_window=active_window,
            processes=processes,
            focused_element=focused_element,
            timestamp=int(time.time() * 1000),
        )
