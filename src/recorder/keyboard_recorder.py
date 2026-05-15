import asyncio
import logging
import os
import platform
import time
from uuid import uuid4

from pynput import keyboard

from src.models.operation import (
    KeyAction,
    KeyboardEventData,
    OperationContext,
    OperationEvent,
    OperationType,
)
from src.platform.factory import create_platform_adapter
from src.recorder.base import BaseRecorder
from src.recorder.context_inference import enrich_operation_context

logger = logging.getLogger(__name__)

MODIFIER_KEYS = {
    "Key.ctrl",
    "Key.ctrl_l",
    "Key.ctrl_r",
    "Key.alt",
    "Key.alt_l",
    "Key.alt_r",
    "Key.shift",
    "Key.shift_l",
    "Key.shift_r",
    "Key.cmd",
    "Key.cmd_l",
    "Key.cmd_r",
}
SENSITIVE_PATTERNS = ["password", "passwd", "pwd", "secret", "token", "api_key", "apikey"]
TEXT_FLUSH_GAP_MS = 1500


def _check_display_available() -> bool:
    system = platform.system().lower()
    if system == "linux":
        display = os.environ.get("DISPLAY")
        wayland = os.environ.get("WAYLAND_DISPLAY")
        if not display and not wayland:
            return False
    return True


class KeyboardRecorder(BaseRecorder):
    def __init__(self):
        super().__init__()
        self._listener: keyboard.Listener | None = None
        self._session_id = ""
        self._seq = 0
        self._active_modifiers: set[str] = set()
        self._text_buffer: list[str] = []
        self._last_input_time = 0.0
        self._is_sensitive_context = False
        self._last_hotkey_time = 0.0
        self._last_hotkey_combo: str = ""
        self._adapter = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listener_healthy = False

    def start(self, session_id: str, callback) -> None:
        self._session_id = session_id
        self._callback = callback
        self._running = True
        self._seq = 0
        self._active_modifiers.clear()
        self._text_buffer.clear()
        self._last_hotkey_time = 0.0
        self._last_hotkey_combo = ""
        self._listener_healthy = False
        try:
            self._adapter = create_platform_adapter()
        except Exception as e:
            logger.warning(f"KeyboardRecorder: failed to create platform adapter: {e}")
            self._adapter = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        if not _check_display_available():
            logger.warning(
                "KeyboardRecorder: no display server detected (DISPLAY and WAYLAND_DISPLAY are unset). "
                "Keyboard events will NOT be captured. Set RECORD_ENABLE_KEYBOARD=false to suppress this warning, "
                "or provide a display server (X11/Wayland) for desktop recording."
            )
            self._running = False
            return

        try:
            self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._listener.start()
            self._listener_healthy = True
        except Exception as e:
            logger.warning(f"KeyboardRecorder: failed to start listener: {e}")
            self._listener = None
            self._running = False

    def stop(self) -> None:
        self._running = False
        self._flush_text_buffer()
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._adapter = None
        self._loop = None

    def _emit(self, data: KeyboardEventData, op_type: OperationType) -> None:
        if not self._running or not self._callback:
            return
        self._seq += 1
        event = OperationEvent(
            id=uuid4().hex,
            session_id=self._session_id,
            seq_num=self._seq,
            timestamp=int(time.time() * 1000),
            type=op_type,
            data=data,
            context=enrich_operation_context(self._build_context()),
        )
        self._callback(event)

    def _build_context(self) -> OperationContext:
        if not self._adapter or not self._loop:
            return OperationContext(platform="desktop")

        try:
            future = asyncio.run_coroutine_threadsafe(self._collect_context(), self._loop)
            return enrich_operation_context(future.result(timeout=0.5))
        except Exception:
            return enrich_operation_context(OperationContext(platform=self._adapter.get_platform_name()))

    async def _collect_context(self) -> OperationContext:
        active_window = None
        focused_element = None

        try:
            active_window = await self._adapter.get_active_window()
        except Exception:
            active_window = None

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

    def _flush_text_buffer(self) -> None:
        if not self._text_buffer:
            return
        text = "".join(self._text_buffer)
        self._text_buffer.clear()
        is_sensitive = self._is_sensitive_context
        self._emit(
            KeyboardEventData(
                key="text_input",
                action=KeyAction.INPUT,
                text="***" if is_sensitive else text,
                is_sensitive=is_sensitive,
            ),
            OperationType.KEY_INPUT,
        )

    def _key_name(self, key) -> str:
        try:
            return key.char if hasattr(key, "char") and key.char else str(key)
        except AttributeError:
            return str(key)

    def _on_press(self, key):
        if not self._running:
            return
        key_name = self._key_name(key)
        now = time.time()

        if key_name in MODIFIER_KEYS or key_name.startswith("Key."):
            self._active_modifiers.add(key_name)
            if self._text_buffer:
                self._flush_text_buffer()
            self._emit(
                KeyboardEventData(key=key_name, action=KeyAction.PRESS, modifiers=list(self._active_modifiers)),
                OperationType.KEY_PRESS,
            )
        else:
            if self._active_modifiers:
                if self._text_buffer:
                    self._flush_text_buffer()
                combo = "+".join(sorted(self._active_modifiers)) + "+" + key_name
                if combo == self._last_hotkey_combo and (now - self._last_hotkey_time) < 0.5:
                    return
                self._last_hotkey_combo = combo
                self._last_hotkey_time = now
                self._emit(
                    KeyboardEventData(
                        key=key_name,
                        action=KeyAction.HOTKEY,
                        modifiers=list(self._active_modifiers),
                    ),
                    OperationType.KEY_PRESS,
                )
            else:
                if now - self._last_input_time > TEXT_FLUSH_GAP_MS / 1000 and self._text_buffer:
                    self._flush_text_buffer()
                self._text_buffer.append(key_name)
                self._last_input_time = now

    def _on_release(self, key):
        if not self._running:
            return
        key_name = self._key_name(key)
        if key_name in self._active_modifiers:
            self._active_modifiers.discard(key_name)
