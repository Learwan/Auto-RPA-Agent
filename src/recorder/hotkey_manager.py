from __future__ import annotations

import logging
import threading
from collections.abc import Callable

try:
    from pynput.keyboard import GlobalHotKeys
except Exception as exc:  # pragma: no cover - environment dependent
    GlobalHotKeys = None
    _PYNPUT_IMPORT_ERROR = exc
else:
    _PYNPUT_IMPORT_ERROR = None

from src.models.hotkey import UserSettings

logger = logging.getLogger(__name__)

_MODIFIER_MAP: dict[str, str] = {
    "ctrl": "<ctrl>",
    "alt": "<alt>",
    "shift": "<shift>",
    "cmd": "<cmd>",
    "super": "<cmd>",
    "win": "<cmd>",
}

_RESERVED_KEYS: set[str] = {
    "escape",
    "esc",
    "tab",
    "caps_lock",
    "backspace",
    "enter",
    "return",
    "space",
    "delete",
    "insert",
    "home",
    "end",
    "page_up",
    "page_down",
    "up",
    "down",
    "left",
    "right",
    "print_screen",
    "scroll_lock",
    "pause",
    "menu",
    "num_lock",
}


def _py_key_name(key_str: str) -> str:
    lower = key_str.lower()
    if lower in _MODIFIER_MAP:
        return _MODIFIER_MAP[lower]
    if lower.startswith("f") and lower[1:].isdigit():
        fn = int(lower[1:])
        if 1 <= fn <= 24:
            return f"f{fn}"
    if len(lower) == 1 and lower.isalnum():
        return lower
    reserved = lower.replace(" ", "_")
    if reserved in _RESERVED_KEYS:
        return reserved
    if len(lower) == 1:
        return lower
    return lower


def _build_pynput_combo(binding) -> str:
    modifiers = []
    normalized_modifiers = {m.lower() for m in binding.modifiers}
    normalized_key = binding.key.lower()

    # Skip invalid bindings like "ctrl + ctrl" that pynput cannot parse.
    if normalized_key in normalized_modifiers:
        return ""

    for m in binding.modifiers:
        pym = _MODIFIER_MAP.get(m)
        if pym is None:
            continue
        modifiers.append(str(pym))
    key_obj = _py_key_name(binding.key)
    key_name = str(key_obj).strip()
    if not key_name:
        return ""
    if len(key_name) == 1 and key_name.isalnum():
        key_token = key_name
    else:
        key_token = f"<{key_name}>"
    return "+".join(modifiers + [key_token])


class HotkeyManager:
    _instance: HotkeyManager | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> HotkeyManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._listener: GlobalHotKeys | None = None
        self._callbacks: dict[str, Callable] = {}
        self._running = False
        self._thread: threading.Thread | None = None

    def start(
        self,
        settings: UserSettings,
        on_start: Callable | None = None,
        on_pause_resume: Callable | None = None,
        on_stop: Callable | None = None,
        on_toggle_hud: Callable | None = None,
        on_capture_focus: Callable | None = None,
    ) -> None:
        if self._running:
            self.stop()

        self._callbacks = {}

        self._register_binding(
            settings.hotkeys.start_recording,
            on_start,
            "start",
        )
        self._register_binding(
            settings.hotkeys.pause_resume_recording,
            on_pause_resume,
            "pause/resume",
        )
        self._register_binding(
            settings.hotkeys.stop_recording,
            on_stop,
            "stop",
        )
        self._register_binding(
            settings.hotkeys.toggle_hud_interaction,
            on_toggle_hud,
            "toggle hud",
        )
        self._register_binding(
            settings.hotkeys.capture_focus_context,
            on_capture_focus,
            "capture focus",
        )

        if not self._callbacks:
            logger.warning("No hotkeys to register")
            return

        if GlobalHotKeys is None:
            logger.warning("Hotkey listener unavailable, skipping hotkeys: %s", _PYNPUT_IMPORT_ERROR)
            self._running = False
            return

        try:
            self._listener = GlobalHotKeys(self._callbacks)
            self._thread = threading.Thread(target=self._listener.run, daemon=True)
            self._thread.start()
            self._running = True
            logger.info("Hotkey manager started with %d hotkeys", len(self._callbacks))
        except Exception as e:
            logger.error("Failed to start hotkey manager: %s", e)
            self._running = False

    def stop(self) -> None:
        if self._listener and self._running:
            try:
                self._listener.stop()
                if self._thread and self._thread.is_alive():
                    self._thread.join(timeout=2.0)
            except Exception as e:
                logger.warning("Error stopping hotkey listener: %s", e)
        self._listener = None
        self._thread = None
        self._callbacks.clear()
        self._running = False
        logger.info("Hotkey manager stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def get_active_hotkeys(self) -> list[str]:
        return list(self._callbacks.keys())

    def _register_binding(self, binding, callback: Callable | None, label: str) -> None:
        if not binding.enabled or callback is None:
            return

        combo = _build_pynput_combo(binding)
        if not combo:
            logger.warning(
                "Skipping invalid %s hotkey binding: modifiers=%s, key=%s",
                label,
                binding.modifiers,
                binding.key,
            )
            return

        self._callbacks[combo] = lambda: callback()
        logger.info("Registered %s hotkey: %s", label, combo)
