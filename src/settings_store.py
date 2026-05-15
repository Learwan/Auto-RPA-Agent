from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from src.models.hotkey import UserSettings

logger = logging.getLogger(__name__)


class SettingsStore:
    _instance: SettingsStore | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> SettingsStore:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._settings: UserSettings = UserSettings()
        self._file_path: Path | None = None
        self._file_lock = threading.Lock()

    def initialize(self, data_dir: str) -> None:
        self._file_path = Path(data_dir) / "user_settings.json"
        self._load()

    def _load(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        try:
            with self._file_lock:
                raw = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            self._settings = UserSettings.model_validate(data)
            logger.info("User settings loaded from %s", self._file_path)
        except Exception as e:
            logger.warning("Failed to load user settings: %s", e)

    def _save(self) -> None:
        if self._file_path is None:
            return
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            raw = self._settings.model_dump_json(indent=2)
            with self._file_lock:
                self._file_path.write_text(raw, encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save user settings: %s", e)

    def get_settings(self) -> UserSettings:
        return self._settings

    def update_settings(self, updates: dict) -> UserSettings:
        current = self._settings.model_dump()
        self._deep_update(current, updates)
        self._settings = UserSettings.model_validate(current)
        self._save()
        return self._settings

    def update_hotkey(self, action: str, binding: dict) -> UserSettings:
        from src.models.hotkey import HotkeyAction

        current = self._settings.model_dump()
        action_map = {
            HotkeyAction.START_RECORDING.value: "start_recording",
            HotkeyAction.PAUSE_RESUME_RECORDING.value: "pause_resume_recording",
            HotkeyAction.STOP_RECORDING.value: "stop_recording",
            HotkeyAction.TOGGLE_HUD_INTERACTION.value: "toggle_hud_interaction",
            HotkeyAction.CAPTURE_FOCUS_CONTEXT.value: "capture_focus_context",
        }
        field = action_map.get(action)
        if not field:
            raise ValueError(f"Unknown hotkey action: {action}")
        current["hotkeys"][field] = binding
        self._settings = UserSettings.model_validate(current)
        self._save()
        return self._settings

    def reset_to_defaults(self) -> UserSettings:
        self._settings = UserSettings()
        self._save()
        return self._settings

    @staticmethod
    def _deep_update(target: dict, source: dict) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                SettingsStore._deep_update(target[key], value)
            else:
                target[key] = value
