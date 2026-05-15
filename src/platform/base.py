from abc import ABC, abstractmethod

from src.models.desktop import ElementCriteria, ProcessInfo, Rect, UIElement, WindowInfo


class BasePlatformAdapter(ABC):
    @abstractmethod
    async def get_windows(self) -> list[WindowInfo]: ...

    @abstractmethod
    async def get_active_window(self) -> WindowInfo | None: ...

    @abstractmethod
    async def get_processes(self) -> list[ProcessInfo]: ...

    @abstractmethod
    async def get_focused_element(self) -> UIElement | None: ...

    @abstractmethod
    async def capture_screen(self, region: Rect | None = None) -> bytes: ...

    @abstractmethod
    async def get_element_at_position(self, x: int, y: int) -> UIElement | None: ...

    @abstractmethod
    async def find_element(self, criteria: ElementCriteria) -> UIElement | None: ...

    @abstractmethod
    def check_permissions(self) -> dict[str, bool]: ...

    @abstractmethod
    def get_platform_name(self) -> str: ...

    @abstractmethod
    async def get_screen_size(self) -> tuple[int, int]: ...

    async def get_dpi_scale(self) -> float:
        return 1.0

    async def to_logical_coords(self, physical_x: int, physical_y: int) -> tuple[int, int]:
        scale = await self.get_dpi_scale()
        if scale <= 1.0:
            return physical_x, physical_y
        return int(physical_x / scale), int(physical_y / scale)

    async def to_physical_coords(self, logical_x: int, logical_y: int) -> tuple[int, int]:
        scale = await self.get_dpi_scale()
        if scale <= 1.0:
            return logical_x, logical_y
        return int(logical_x * scale), int(logical_y * scale)

    async def get_logical_screen_size(self) -> tuple[int, int]:
        physical_w, physical_h = await self.get_screen_size()
        scale = await self.get_dpi_scale()
        if scale <= 1.0:
            return physical_w, physical_h
        return int(physical_w / scale), int(physical_h / scale)

    async def close(self) -> None:
        return None
