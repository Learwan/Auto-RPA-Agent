import asyncio
import logging
import os
import platform
import time
from uuid import uuid4

from pynput import mouse

from src.models.desktop import UIElement
from src.models.operation import (
    MouseAction,
    MouseButton,
    MouseEventData,
    OperationContext,
    OperationEvent,
    OperationType,
)
from src.platform.factory import create_platform_adapter
from src.recorder.base import BaseRecorder
from src.recorder.context_inference import enrich_operation_context

logger = logging.getLogger(__name__)

SAME_POSITION_THRESHOLD = 15
SAME_ELEMENT_DEBOUNCE_MS = 500
SCROLL_MERGE_WINDOW_MS = 500


def _check_display_available() -> bool:
    system = platform.system().lower()
    if system == "linux":
        display = os.environ.get("DISPLAY")
        wayland = os.environ.get("WAYLAND_DISPLAY")
        if not display and not wayland:
            return False
    return True


class MouseRecorder(BaseRecorder):
    def __init__(self):
        super().__init__()
        self._listener: mouse.Listener | None = None
        self._session_id = ""
        self._seq = 0
        self._last_press_pos = None
        self._last_press_button = None
        self._last_press_time = 0.0
        self._adapter = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_click_element_id: str | None = None
        self._last_click_time = 0.0
        self._last_click_pos = (0, 0)
        self._scroll_accumulator: dict = {}
        self._last_scroll_time = 0.0
        self._listener_healthy = False

    def start(self, session_id: str, callback) -> None:
        self._session_id = session_id
        self._callback = callback
        self._running = True
        self._seq = 0
        self._last_click_element_id = None
        self._last_click_time = 0.0
        self._scroll_accumulator = {}
        self._last_scroll_time = 0.0
        self._listener_healthy = False
        try:
            self._adapter = create_platform_adapter()
        except Exception as e:
            logger.warning(f"Failed to create platform adapter: {e}")
            self._adapter = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        if not _check_display_available():
            logger.warning(
                "MouseRecorder: no display server detected (DISPLAY and WAYLAND_DISPLAY are unset). "
                "Mouse events will NOT be captured. Set RECORD_ENABLE_MOUSE=false to suppress this warning, "
                "or provide a display server (X11/Wayland) for desktop recording."
            )
            self._running = False
            return

        try:
            self._listener = mouse.Listener(
                on_click=self._on_click,
                on_scroll=self._on_scroll,
            )
            self._listener.start()
            self._listener_healthy = True
        except Exception as e:
            logger.warning(f"MouseRecorder: failed to start listener: {e}")
            self._listener = None
            self._running = False

    def stop(self) -> None:
        self._running = False
        self._flush_scroll()
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._adapter = None
        self._loop = None

    def _emit(self, data: MouseEventData, op_type: OperationType, context: OperationContext | None = None) -> None:
        if not self._running or not self._callback:
            return
        self._seq += 1
        default_context = OperationContext(
            platform=self._adapter.get_platform_name() if self._adapter else "desktop"
        )
        final_context = enrich_operation_context(context or default_context)
        event = OperationEvent(
            id=uuid4().hex,
            session_id=self._session_id,
            seq_num=self._seq,
            timestamp=int(time.time() * 1000),
            type=op_type,
            data=data,
            context=final_context,
        )
        self._callback(event)

    def _get_element_at(self, x: int, y: int) -> UIElement | None:
        if not self._adapter or not self._loop:
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._adapter.get_element_at_position(x, y),
                self._loop,
            )
            return future.result(timeout=1.0)
        except Exception as e:
            logger.debug(f"Failed to get element at ({x}, {y}): {e}")
            return None

    def _element_signature(self, element: UIElement | None) -> str:
        if not element:
            return ""
        parts = [element.role or "", element.identifier or "", element.title or ""]
        return "|".join(parts)

    def _build_context(self, x: int, y: int, element: UIElement | None = None) -> OperationContext:
        if not self._adapter or not self._loop:
            return OperationContext(platform="desktop", focused_element=element)

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._collect_context(x, y, element),
                self._loop,
            )
            return enrich_operation_context(future.result(timeout=0.75))
        except Exception as e:
            logger.debug(f"Failed to build operation context at ({x}, {y}): {e}")
            return enrich_operation_context(
                OperationContext(
                    focused_element=element,
                    platform=self._adapter.get_platform_name(),
                )
            )

    async def _collect_context(self, x: int, y: int, element: UIElement | None = None) -> OperationContext:
        active_window = None
        focused_element = element

        try:
            active_window = await self._adapter.get_active_window()
        except Exception:
            active_window = None

        if focused_element is None:
            try:
                focused_element = await self._adapter.get_element_at_position(x, y)
            except Exception:
                focused_element = None

        if focused_element is None:
            try:
                focused_element = await self._adapter.get_focused_element()
            except Exception:
                focused_element = None

        return OperationContext(
            active_window=active_window,
            focused_element=focused_element,
            process_name=active_window.app_name if active_window else None,
            process_pid=active_window.pid if active_window else None,
            platform=self._adapter.get_platform_name(),
        )

    def _remember_click(self, x: int, y: int, element: UIElement | None, timestamp: float) -> None:
        self._last_click_element_id = self._element_signature(element)
        self._last_click_time = timestamp
        self._last_click_pos = (x, y)

    def _is_duplicate_click(self, x: int, y: int, element: UIElement | None, action: MouseAction) -> bool:
        now = time.time()
        if action != MouseAction.CLICK:
            self._remember_click(x, y, element, now)
            return False

        sig = self._element_signature(element)
        if (
            sig
            and sig == self._last_click_element_id
            and (now - self._last_click_time) < SAME_ELEMENT_DEBOUNCE_MS / 1000
        ):
            return True
        dx = abs(x - self._last_click_pos[0])
        dy = abs(y - self._last_click_pos[1])
        if (
            dx < SAME_POSITION_THRESHOLD
            and dy < SAME_POSITION_THRESHOLD
            and (now - self._last_click_time) < SAME_ELEMENT_DEBOUNCE_MS / 1000
        ):
            return True
        self._remember_click(x, y, element, now)
        return False

    def _on_click(self, x, y, button, pressed):
        if not self._running:
            return
        x, y = int(x), int(y)
        now = time.time()
        btn_map = {
            mouse.Button.left: MouseButton.LEFT,
            mouse.Button.right: MouseButton.RIGHT,
            mouse.Button.middle: MouseButton.MIDDLE,
        }
        btn = btn_map.get(button, MouseButton.LEFT)

        if pressed:
            self._last_press_pos = (x, y)
            self._last_press_button = btn
            self._last_press_time = now
        else:
            action = MouseAction.CLICK
            if self._last_press_pos and self._last_press_button == btn:
                dx = abs(x - self._last_press_pos[0])
                dy = abs(y - self._last_press_pos[1])
                if dx > 5 or dy > 5:
                    action = MouseAction.DRAG_END
                    element = self._get_element_at(x, y)
                    context = self._build_context(x, y, element)
                    self._emit(
                        MouseEventData(
                            x=x,
                            y=y,
                            button=btn,
                            action=MouseAction.DRAG_END,
                            drag_start_x=self._last_press_pos[0],
                            drag_start_y=self._last_press_pos[1],
                        ),
                        OperationType.MOUSE_DRAG,
                        context,
                    )
                    self._last_press_pos = None
                    return
                if btn == MouseButton.RIGHT:
                    action = MouseAction.RIGHT_CLICK
                elif (
                    btn == MouseButton.LEFT
                    and now - self._last_click_time < 0.3
                    and abs(x - self._last_click_pos[0]) < 5
                    and abs(y - self._last_click_pos[1]) < 5
                ):
                    action = MouseAction.DOUBLE_CLICK

            element = self._get_element_at(x, y)

            if self._is_duplicate_click(x, y, element, action):
                self._last_press_pos = None
                return

            context = self._build_context(x, y, element)
            self._emit(MouseEventData(x=x, y=y, button=btn, action=action), OperationType.MOUSE_CLICK, context)
            self._last_press_pos = None

    def _on_scroll(self, x, y, dx, dy):
        if not self._running:
            return
        x, y = int(x), int(y)
        now = time.time()
        if now - self._last_scroll_time > SCROLL_MERGE_WINDOW_MS / 1000:
            self._flush_scroll()
        self._last_scroll_time = now
        key = f"{x}:{y}"
        if key not in self._scroll_accumulator:
            self._scroll_accumulator[key] = {"x": x, "y": y, "dx": 0, "dy": 0}
        self._scroll_accumulator[key]["dx"] += int(dx)
        self._scroll_accumulator[key]["dy"] += int(dy)

    def _flush_scroll(self) -> None:
        if not self._scroll_accumulator:
            return
        for _key, acc in self._scroll_accumulator.items():
            if acc["dy"] == 0 and acc["dx"] == 0:
                continue
            action = MouseAction.SCROLL_DOWN if acc["dy"] < 0 else MouseAction.SCROLL_UP
            context = self._build_context(acc["x"], acc["y"])
            self._emit(
                MouseEventData(x=acc["x"], y=acc["y"], action=action, scroll_dx=acc["dx"], scroll_dy=acc["dy"]),
                OperationType.MOUSE_SCROLL,
                context,
            )
        self._scroll_accumulator = {}
