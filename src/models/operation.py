from __future__ import annotations

from enum import Enum
from typing import Any, Union

from pydantic import BaseModel, ConfigDict, Field

from src.models.desktop import UIElement, WindowInfo


class OperationType(str, Enum):
    MOUSE_CLICK = "mouse_click"
    MOUSE_SCROLL = "mouse_scroll"
    MOUSE_DRAG = "mouse_drag"
    KEY_PRESS = "key_press"
    KEY_INPUT = "key_input"
    WINDOW_SWITCH = "window_switch"
    NAVIGATION = "navigation"
    FILE_OP = "file_op"
    CLIPBOARD = "clipboard"


class MouseAction(str, Enum):
    PRESS = "press"
    RELEASE = "release"
    MOVE = "move"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    DRAG_START = "drag_start"
    DRAG_END = "drag_end"


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class KeyAction(str, Enum):
    PRESS = "press"
    RELEASE = "release"
    INPUT = "input"
    HOTKEY = "hotkey"


class FileOperation(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    MOVE = "move"
    UPLOAD = "upload"
    DOWNLOAD = "download"


class ClipboardType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    UNKNOWN = "unknown"


class MouseEventData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: int = 0
    y: int = 0
    button: MouseButton = MouseButton.LEFT
    action: MouseAction = MouseAction.CLICK
    scroll_dx: int = 0
    scroll_dy: int = 0
    drag_start_x: int | None = None
    drag_start_y: int | None = None


class KeyboardEventData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str = ""
    action: KeyAction = KeyAction.PRESS
    text: str | None = None
    modifiers: list[str] = Field(default_factory=list)
    is_sensitive: bool = False


class WindowEventData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    from_window: WindowInfo | None = None
    to_window: WindowInfo = Field(default_factory=WindowInfo)


class NavigationEventData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    title: str | None = None
    transition: str | None = None
    from_url: str | None = None
    from_title: str | None = None
    same_document: bool = False


class FileEventData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operation: FileOperation = FileOperation.CREATE
    src_path: str | None = None
    dest_path: str | None = None
    paths: list[str] = Field(default_factory=list)
    file_type: str | None = None
    size_bytes: int | None = None


class ClipboardEventData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content_type: ClipboardType = ClipboardType.TEXT
    content_preview: str | None = None
    size_bytes: int | None = None


OperationData = Union[
    MouseEventData,
    KeyboardEventData,
    WindowEventData,
    NavigationEventData,
    FileEventData,
    ClipboardEventData,
]


class OperationContext(BaseModel):
    """Per-operation context used by the analyzer / executor."""

    model_config = ConfigDict(extra="ignore")

    active_window: WindowInfo | None = None
    focused_element: UIElement | None = None
    process_name: str | None = None
    process_pid: int | None = None
    platform: str | None = None
    screenshot_path: str | None = None
    node_suggestion: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class OperationEvent(BaseModel):
    """A single user operation event captured during recording."""

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    id: str
    session_id: str
    seq_num: int = 0
    timestamp: int = 0
    type: OperationType
    data: OperationData
    context: OperationContext | None = None
