from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class HotkeyAction(str, Enum):
    START_RECORDING = "start_recording"
    PAUSE_RESUME_RECORDING = "pause_resume_recording"
    STOP_RECORDING = "stop_recording"
    TOGGLE_HUD_INTERACTION = "toggle_hud_interaction"
    CAPTURE_FOCUS_CONTEXT = "capture_focus_context"


class HotkeyBinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str
    key: str = ""
    modifiers: list[str] = Field(default_factory=list)
    enabled: bool = True
    description: str | None = None

    @property
    def display_string(self) -> str:
        if not self.key:
            return ""
        order = {"ctrl": 0, "alt": 1, "shift": 2, "cmd": 3, "super": 3, "meta": 3, "win": 3}
        ordered_modifiers = sorted(
            (m for m in self.modifiers if m),
            key=lambda m: order.get(m.lower(), 99),
        )
        parts = [m.capitalize() if len(m) > 2 else m.upper() for m in ordered_modifiers]
        key_label = self.key.upper() if len(self.key) == 1 else self.key.capitalize()
        parts.append(key_label)
        return "+".join(parts)


def _default_start() -> HotkeyBinding:
    return HotkeyBinding(
        action=HotkeyAction.START_RECORDING.value,
        key="r",
        modifiers=["ctrl", "shift"],
        description="开始录制",
    )


def _default_pause_resume() -> HotkeyBinding:
    return HotkeyBinding(
        action=HotkeyAction.PAUSE_RESUME_RECORDING.value,
        key="p",
        modifiers=["ctrl", "shift"],
        description="暂停或继续录制",
    )


def _default_stop() -> HotkeyBinding:
    return HotkeyBinding(
        action=HotkeyAction.STOP_RECORDING.value,
        key="s",
        modifiers=["ctrl", "shift"],
        description="停止录制",
    )


def _default_toggle_hud() -> HotkeyBinding:
    return HotkeyBinding(
        action=HotkeyAction.TOGGLE_HUD_INTERACTION.value,
        key="u",
        modifiers=["ctrl", "shift"],
        description="切换 HUD 交互",
    )


def _default_capture_focus() -> HotkeyBinding:
    return HotkeyBinding(
        action=HotkeyAction.CAPTURE_FOCUS_CONTEXT.value,
        key="i",
        modifiers=["ctrl", "shift"],
        description="抓取焦点上下文",
    )


class HotkeyConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_recording: HotkeyBinding = Field(default_factory=_default_start)
    pause_resume_recording: HotkeyBinding = Field(default_factory=_default_pause_resume)
    stop_recording: HotkeyBinding = Field(default_factory=_default_stop)
    toggle_hud_interaction: HotkeyBinding = Field(default_factory=_default_toggle_hud)
    capture_focus_context: HotkeyBinding = Field(default_factory=_default_capture_focus)

    def all_bindings(self) -> list[HotkeyBinding]:
        return [
            self.start_recording,
            self.pause_resume_recording,
            self.stop_recording,
            self.toggle_hud_interaction,
            self.capture_focus_context,
        ]

    def find_by_action(self, action: str) -> HotkeyBinding | None:
        for binding in self.all_bindings():
            if binding.action == action:
                return binding
        return None

    def check_conflicts(self) -> list[dict[str, list[str]]]:
        seen: dict[tuple[str, frozenset[str]], list[str]] = {}
        for binding in self.all_bindings():
            if not binding.enabled or not binding.key:
                continue
            key = (binding.key.lower(), frozenset(m.lower() for m in binding.modifiers))
            seen.setdefault(key, []).append(binding.action)
        return [
            {"combo": f"{'+'.join(sorted(mods))}+{key}", "actions": actions}
            for (key, mods), actions in seen.items()
            if len(actions) > 1
        ]


class OpenAICompatibleLLMConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.2
    top_p: float = 1.0
    max_tokens: int = 1024
    timeout_seconds: float = 60.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


class UserSettings(BaseModel):
    """Top-level user-facing settings stored on disk."""

    model_config = ConfigDict(extra="ignore")

    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)
    remote_llm: OpenAICompatibleLLMConfig = Field(default_factory=OpenAICompatibleLLMConfig)
    audio_feedback_enabled: bool = True
    system_notifications_enabled: bool = True
    hud_enabled_by_default: bool = True
