from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.desktop import Rect


class UIElement(BaseModel):
    """Vision-detected UI element (distinct from accessibility-derived elements)."""

    model_config = ConfigDict(extra="ignore")

    element_type: str = "generic"
    label: str | None = None
    text: str | None = None
    description: str | None = None
    bbox: Rect | None = None
    confidence: float = 0.0
    actionable: bool = True
    suggested_action: str | None = None
    state: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("bbox", mode="before")
    @classmethod
    def _coerce_bbox(cls, value: Any) -> Any:
        if isinstance(value, (tuple, list)) and len(value) == 4:
            x, y, w, h = value
            return Rect(x=int(x), y=int(y), width=int(w), height=int(h))
        return value


class GroundingResult(BaseModel):
    """Result returned by visual grounding (used for self-healing locators)."""

    model_config = ConfigDict(extra="ignore")

    found: bool = False
    description: str | None = None
    element_type: str | None = None
    element_label: str | None = None
    bbox: Rect | None = None
    bbox_pixel: tuple[int, int, int, int] | None = None
    bbox_normalized: tuple[float, float, float, float] | None = None
    center_x: float = 0.0
    center_y: float = 0.0
    confidence: float = 0.0
    alternative_targets: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str | None = None
    raw_response: dict[str, Any] | None = None


class ScreenshotAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    elements: list[UIElement] = Field(default_factory=list)
    actionable_elements: list[UIElement] = Field(default_factory=list)
    description: str = ""
    title: str | None = None
    app_name: str | None = None
    window_title: str | None = None
    ui_state_description: str | None = None
    layout: str | None = None
    user_intent: str | None = None
    suggested_next_actions: list[str] = Field(default_factory=list)
    thinking_trace: str | None = None
    interactive_count: int = 0
    confidence: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScreenshotDiff(BaseModel):
    model_config = ConfigDict(extra="ignore")

    similarity: float = 1.0
    change_ratio: float = 0.0
    changed_regions: list[Rect] = Field(default_factory=list)
    is_significant: bool = False
    summary: str = ""


class VisualStep(BaseModel):
    """A reconstructed step from purely visual observation."""

    model_config = ConfigDict(extra="ignore")

    step_index: int = 0
    description: str = ""
    target: UIElement | None = None
    action_type: str = "click"
    confidence: float = 0.0
    screenshot_path: str | None = None
