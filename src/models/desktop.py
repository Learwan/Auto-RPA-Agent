from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Point(BaseModel):
    """Pixel coordinate used by recording and execution."""

    model_config = ConfigDict(extra="ignore")

    x: int = 0
    y: int = 0


class Rect(BaseModel):
    """Rectangle in screen coordinates."""

    model_config = ConfigDict(extra="ignore")

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> Point:
        return Point(x=self.x + self.width // 2, y=self.y + self.height // 2)

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x <= self.right and self.y <= y <= self.bottom

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


class WindowInfo(BaseModel):
    """Active-window snapshot used both during recording and execution."""

    model_config = ConfigDict(extra="ignore")

    window_id: str | int | None = None
    title: str | None = None
    app_name: str | None = None
    pid: int | None = None
    class_name: str | None = None
    process_name: str | None = None
    bounds: Rect | None = None
    is_active: bool = True
    is_visible: bool = True
    is_minimized: bool = False
    url: str | None = None
    browser_type: str | None = None
    tab_id: str | None = None


class ProcessInfo(BaseModel):
    """Process snapshot used by desktop snapshot capture."""

    model_config = ConfigDict(extra="ignore")

    pid: int
    name: str | None = None
    executable: str | None = None
    cpu_percent: float | None = None
    memory_mb: float | None = None
    command_line: str | None = None


class UIElement(BaseModel):
    """A single UI element, normalised across desktop and web."""

    model_config = ConfigDict(extra="ignore")

    role: str | None = None
    title: str | None = None
    value: str | None = None
    identifier: str | None = None
    description: str | None = None
    functional_label: str | None = None
    class_name: str | None = None
    selector: str | None = None
    xpath: str | None = None
    url: str | None = None
    frame: str | None = None
    tab_id: str | None = None
    bounds: Rect | None = None
    is_enabled: bool = True
    is_focused: bool = False
    is_visible: bool = True
    input_type: str | None = None
    tag_name: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    children: list["UIElement"] = Field(default_factory=list)

    def signature(self) -> str:
        return "|".join(
            [
                self.role or "",
                self.identifier or "",
                self.title or "",
                self.selector or "",
                self.xpath or "",
            ]
        )


class ElementCriteria(BaseModel):
    """Search criteria passed to platform adapters when looking up an element."""

    model_config = ConfigDict(extra="ignore")

    role: str | None = None
    title: str | None = None
    text_contains: str | None = None
    accessibility_id: str | None = None
    class_name: str | None = None
    selector: str | None = None
    xpath: str | None = None
    url: str | None = None
    frame: str | None = None
    bounds: Rect | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class DesktopState(BaseModel):
    """Full desktop snapshot consumed by analysis and visualisation tools."""

    model_config = ConfigDict(extra="ignore")

    timestamp: int = 0
    active_window: WindowInfo | None = None
    windows: list[WindowInfo] = Field(default_factory=list)
    processes: list[ProcessInfo] = Field(default_factory=list)
    screen_size: tuple[int, int] | None = None
    platform: str | None = None
    focused_element: UIElement | None = None
    screenshot_path: str | None = None


UIElement.model_rebuild()
