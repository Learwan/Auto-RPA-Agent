from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.models.desktop import Point


class StepType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    TYPE = "type"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    DRAG = "drag"
    WAIT = "wait"
    SWITCH_WINDOW = "switch_window"
    NAVIGATION = "navigation"
    NAVIGATE = "navigate"
    CONDITION = "condition"
    LOOP = "loop"
    SCREENSHOT = "screenshot"
    ASSERT = "assert"
    CUSTOM = "custom"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    UPLOAD_FILE = "upload_file"
    DOWNLOAD_FILE = "download_file"
    FILE_OP = "file_op"


class LocateStrategy(str, Enum):
    POSITION = "position"
    TEXT_MATCH = "text_match"
    ACCESSIBILITY_ID = "accessibility_id"
    CSS_SELECTOR = "css_selector"
    XPATH = "xpath"
    IMAGE = "image"
    IMAGE_MATCH = "image_match"
    OCR = "ocr"
    COORDINATE = "coordinate"


# Alias used in engine fallback path
LocatorStrategy = LocateStrategy


class ErrorAction(str, Enum):
    RETRY = "retry"
    SKIP = "skip"
    STOP = "stop"
    ABORT = "abort"
    ASK_USER = "ask_user"
    FALLBACK = "fallback"
    RELOCATE = "relocate"
    ADJUST_AND_RETRY = "adjust_and_retry"


class StepTarget(BaseModel):
    strategy: LocateStrategy = LocateStrategy.POSITION
    selector: str | None = None
    xpath: str | None = None
    text_contains: str | None = None
    title: str | None = None
    role: str | None = None
    accessibility_id: str | None = None
    position: Point | None = None
    window_title: str | None = None
    class_name: str | None = None
    url: str | None = None
    image_path: str | None = None
    frame: str | None = None
    expected_attributes: dict[str, Any] | None = Field(default_factory=dict)
    bounds: dict[str, int] | None = None

    model_config = {"extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def _coerce_position(cls, values: Any) -> Any:
        if isinstance(values, dict):
            pos = values.get("position")
            if isinstance(pos, dict) and "x" in pos and "y" in pos:
                values["position"] = Point(x=pos["x"], y=pos["y"])
        return values

    def inferred_requires_confirmation(self) -> bool:
        if self.selector or self.xpath or self.accessibility_id:
            return False
        text = self.text_contains or self.title or ""
        has_attrs = bool(self.expected_attributes)
        if text and len(text) > 4 and has_attrs:
            return False
        if self.strategy == LocateStrategy.POSITION:
            return True
        if self.strategy == LocateStrategy.TEXT_MATCH and not has_attrs:
            return True
        return False

    def inferred_locator_stability(self) -> str:
        if self.selector or self.xpath or self.accessibility_id:
            return "high"
        if self.title or self.text_contains:
            return "medium"
        return "low"


class StepCondition(BaseModel):
    field: str = ""
    operator: str = "equals"
    value: Any = None
    negate: bool = False

    model_config = {"extra": "allow"}


class StepBranch(BaseModel):
    condition: StepCondition | None = None
    step_ids: list[str] = Field(default_factory=list)
    label: str = ""

    model_config = {"extra": "allow"}


class AutomationStep(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: StepType = StepType.CLICK
    action: dict[str, Any] = Field(default_factory=dict)
    target: StepTarget = Field(default_factory=StepTarget)
    description: str = ""
    delay: int = 0
    on_error: ErrorAction = ErrorAction.RETRY
    retry_count: int = 3
    timeout: int = 30000
    verification_enabled: bool = True
    verification_strict: bool = False
    condition: StepCondition | None = None
    preconditions: list[StepCondition] = Field(default_factory=list)
    branches: list[StepBranch] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    execution_order: int | None = 0
    atomic: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    def locator_requires_confirmation(self) -> bool:
        if not (self.metadata or {}).get("locator_requires_confirmation", False):
            return False
        if self.target and not self.target.inferred_requires_confirmation():
            return False
        return True


class FlowVariable(BaseModel):
    name: str = ""
    var_type: str = "string"
    default_value: Any = None
    description: str = ""

    model_config = {"extra": "allow"}


class ErrorHandlingPolicy(BaseModel):
    default_action: ErrorAction = ErrorAction.RETRY
    max_retries: int = 3
    retry_delay_ms: int = 1000
    fallback_steps: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class DetectedPattern(BaseModel):
    id: str = ""
    pattern_sequence: list[StepType] = Field(default_factory=list)
    instances: list[dict[str, Any]] = Field(default_factory=list)
    support: int = 0
    avg_duration_ms: float = 0.0
    confidence: float = 0.0
    pattern_type: str = ""

    model_config = {"extra": "allow"}


class AutomationFlow(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    description: str = ""
    steps: list[AutomationStep] = Field(default_factory=list)
    variables: list[FlowVariable] = Field(default_factory=list)
    error_handling: ErrorHandlingPolicy = Field(default_factory=ErrorHandlingPolicy)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"
    behavior_tree: dict[str, Any] | None = None
    entry_step_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source_session_id: str | None = None
    source_pattern: dict[str, Any] | None = None
    execution_count: int = 0
    success_count: int = 0
    structure_version: int = 1
    created_at: float | None = None
    updated_at: float | None = None

    model_config = {"extra": "allow"}

    def ordered_steps(self) -> list[AutomationStep]:
        return sorted(self.steps, key=lambda s: s.execution_order or 0)

    def step_map(self) -> dict[str, AutomationStep]:
        return {step.id: step for step in self.steps}

    def get_next_step_ids(self, step_id: str, outcome: Any = None) -> list[str]:
        step_map = self.step_map()
        step = step_map.get(step_id)
        if step is None:
            return []
        if outcome is not None and step.branches:
            for branch in step.branches:
                cond = branch.condition
                if cond is not None and cond.value == outcome:
                    return list(branch.step_ids)
        ordered = self.ordered_steps()
        for i, s in enumerate(ordered):
            if s.id == step_id and i + 1 < len(ordered):
                return [ordered[i + 1].id]
        return []
