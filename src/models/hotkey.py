from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class HotkeyAction(str, Enum):
    START_RECORDING = "start_recording"
    PAUSE_RESUME_RECORDING = "pause_resume_recording"
    STOP_RECORDING = "stop_recording"
    TOGGLE_HUD_INTERACTION = "toggle_hud_interaction"
    CAPTURE_FOCUS_CONTEXT = "capture_focus_context"


class HotkeyBinding(BaseModel):
    action: str = ""
    modifiers: list[str] = Field(default_factory=lambda: ["ctrl", "shift"])
    key: str = ""
    enabled: bool = True

    model_config = {"extra": "allow"}

    @property
    def display_string(self) -> str:
        parts = [m.capitalize() for m in self.modifiers] + [self.key.upper()]
        return "+".join(parts)


class HotkeyConfig(BaseModel):
    start_recording: HotkeyBinding = Field(
        default_factory=lambda: HotkeyBinding(action="start_recording", modifiers=["ctrl", "shift"], key="r")
    )
    pause_resume_recording: HotkeyBinding = Field(
        default_factory=lambda: HotkeyBinding(action="pause_resume_recording", modifiers=["ctrl", "shift"], key="p")
    )
    stop_recording: HotkeyBinding = Field(
        default_factory=lambda: HotkeyBinding(action="stop_recording", modifiers=["ctrl", "shift"], key="s")
    )
    toggle_hud_interaction: HotkeyBinding = Field(
        default_factory=lambda: HotkeyBinding(action="toggle_hud_interaction", modifiers=["ctrl", "shift"], key="u")
    )
    capture_focus_context: HotkeyBinding = Field(
        default_factory=lambda: HotkeyBinding(action="capture_focus_context", modifiers=["ctrl", "shift"], key="i")
    )

    model_config = {"extra": "allow"}

    def find_by_action(self, action: str) -> HotkeyBinding | None:
        for field_name in self.model_fields:
            binding = getattr(self, field_name, None)
            if isinstance(binding, HotkeyBinding) and binding.action == action:
                return binding
        return None


class OpenAICompatibleLLMConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 4096

    model_config = {"extra": "allow"}

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


class UserSettings(BaseModel):
    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)
    remote_llm: OpenAICompatibleLLMConfig = Field(default_factory=OpenAICompatibleLLMConfig)
    theme: str = "system"
    language: str = "zh-CN"
    auto_save: bool = True

    model_config = {"extra": "allow"}
