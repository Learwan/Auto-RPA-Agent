from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.models.desktop import UIElement, WindowInfo


class OperationType(str, Enum):
    MOUSE_CLICK = "mouse_click"
    MOUSE_SCROLL = "mouse_scroll"
    MOUSE_DRAG = "mouse_drag"
    MOUSE_MOVE = "mouse_move"
    KEY_PRESS = "key_press"
    KEY_INPUT = "key_input"
    WINDOW_SWITCH = "window_switch"
    NAVIGATION = "navigation"
    FILE_OP = "file_op"
    CLIPBOARD = "clipboard"
    SCREENSHOT = "screenshot"
    CUSTOM = "custom"


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class MouseAction(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    PRESS = "press"
    RELEASE = "release"
    MOVE = "move"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    DRAG = "drag"
    DRAG_START = "drag_start"
    DRAG_END = "drag_end"
    DROP = "drop"


class KeyAction(str, Enum):
    PRESS = "press"
    RELEASE = "release"
    INPUT = "input"
    HOTKEY = "hotkey"


class ClipboardType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    HTML = "html"


class FileOperation(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"
    MOVE = "move"
    COPY = "copy"
    OPEN = "open"


class MouseEventData(BaseModel):
    x: int = 0
    y: int = 0
    button: MouseButton = MouseButton.LEFT
    action: MouseAction = MouseAction.CLICK
    scroll_dx: int = 0
    scroll_dy: int = 0
    drag_start: dict[str, int] | None = None
    drag_start_x: int | None = None
    drag_start_y: int | None = None

    model_config = {"extra": "allow"}


class KeyboardEventData(BaseModel):
    key: str = ""
    action: KeyAction = KeyAction.PRESS
    text: str | None = None
    modifiers: list[str] = Field(default_factory=list)
    is_sensitive: bool = False

    model_config = {"extra": "allow"}


class WindowEventData(BaseModel):
    from_window: WindowInfo | None = None
    to_window: WindowInfo | None = None
    method: str | None = None

    model_config = {"extra": "allow"}


class NavigationEventData(BaseModel):
    url: str = ""
    title: str = ""
    method: str | None = None
    referrer: str | None = None
    transition: str | None = None
    from_url: str | None = None
    from_title: str | None = None
    same_document: bool = False

    model_config = {"extra": "allow"}


class ClipboardEventData(BaseModel):
    content_type: ClipboardType = ClipboardType.TEXT
    content_preview: str = ""

    model_config = {"extra": "allow"}


class FileEventData(BaseModel):
    operation: FileOperation = FileOperation.OPEN
    src_path: str = ""
    dst_path: str | None = None
    dest_path: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    file_type: str | None = None
    paths: list[str] | None = None
    size: int | None = None

    model_config = {"extra": "allow"}


class OperationContext(BaseModel):
    platform: str | None = ""
    active_window: WindowInfo | None = None
    focused_element: UIElement | None = None
    screenshot_path: str | None = None
    process_name: str | None = None
    process_pid: int | None = None
    node_suggestion: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


class OperationEvent(BaseModel):
    id: str = ""
    session_id: str = ""
    seq_num: int = 0
    timestamp: int = 0
    type: OperationType = OperationType.MOUSE_CLICK
    data: (
        MouseEventData | KeyboardEventData | WindowEventData
        | NavigationEventData | ClipboardEventData | FileEventData
    ) = Field(default_factory=MouseEventData)
    context: OperationContext = Field(default_factory=OperationContext)

    model_config = {"extra": "allow"}
