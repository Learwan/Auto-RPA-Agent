from __future__ import annotations

import time

from pydantic import BaseModel, Field


class Point(BaseModel):
    x: int = 0
    y: int = 0


class Rect(BaseModel):
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


class UIElement(BaseModel):
    role: str = ""
    title: str | None = None
    identifier: str | None = None
    value: str | None = None
    label: str | None = None
    description: str | None = None
    functional_label: str | None = None
    class_name: str | None = None
    selector: str | None = None
    xpath: str | None = None
    url: str | None = None
    frame: str | None = None
    tag_name: str | None = None
    input_type: str | None = None
    tab_id: str | None = None
    bounds: Rect | None = None
    is_enabled: bool = True
    is_visible: bool = True
    is_focusable: bool = False
    is_focused: bool = False
    children: list[UIElement] | None = None
    children_count: int = 0

    model_config = {"extra": "allow"}


class WindowInfo(BaseModel):
    window_id: int | str = 0
    title: str = ""
    app_name: str = ""
    pid: int = 0
    bounds: Rect | None = None
    is_active: bool = False
    is_visible: bool = True
    url: str | None = None
    browser_type: str | None = None
    tab_id: str | None = None
    window_class: str | None = None
    layer: int = 0

    model_config = {"extra": "allow"}


class ProcessInfo(BaseModel):
    pid: int = 0
    name: str = ""
    path: str = ""
    is_running: bool = True
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    status: str = "unknown"
    create_time: float = 0.0

    model_config = {"extra": "allow"}


class ElementCriteria(BaseModel):
    accessibility_id: str | None = None
    role: str | None = None
    title: str | None = None
    text_contains: str | None = None
    identifier: str | None = None
    value: str | None = None
    label: str | None = None
    description: str | None = None
    functional_label: str | None = None
    input_type: str | None = None
    tag_name: str | None = None
    selector: str | None = None
    xpath: str | None = None
    url: str | None = None
    frame: str | None = None
    class_name: str | None = None
    bounds: Rect | None = None
    position: Point | None = None
    position_tolerance: int = 20

    model_config = {"extra": "allow"}


class DesktopState(BaseModel):
    active_window: WindowInfo | None = None
    focused_element: UIElement | None = None
    windows: list[WindowInfo] = Field(default_factory=list)
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    screen_size: tuple[int, int] | None = None

    model_config = {"extra": "allow"}
