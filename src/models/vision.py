from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UIElement(BaseModel):
    element_type: str = ""
    label: str = ""
    description: str = ""
    bbox: tuple[int, int, int, int] | list[int] | None = None
    confidence: float = 0.0
    actionable: bool = False
    suggested_action: str | None = None

    model_config = {"extra": "allow"}


class ScreenshotAnalysis(BaseModel):
    app_name: str = ""
    window_title: str = ""
    ui_state_description: str = ""
    actionable_elements: list[UIElement] = Field(default_factory=list)
    user_intent: str = ""
    suggested_next_actions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    thinking_trace: str | None = ""

    model_config = {"extra": "allow"}


class ScreenshotDiff(BaseModel):
    is_significant: bool = False
    changed_regions: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""
    confidence: float = 0.0

    model_config = {"extra": "allow"}


class VisualStep(BaseModel):
    step_type: str = ""
    description: str = ""
    element: UIElement | None = None
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    confidence: float = 0.0

    model_config = {"extra": "allow"}


class GroundingResult(BaseModel):
    found: bool = False
    element_type: str = ""
    element_label: str = ""
    bbox: list[int | float] = Field(default_factory=list)
    confidence: float = 0.0
    alternative_targets: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str = ""
    action_type: str | None = None
    action_params: dict[str, Any] | None = None
    thought: str = ""

    model_config = {"extra": "allow"}
