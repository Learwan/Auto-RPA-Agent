from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    ABORTED = "aborted"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class VerificationLevel(str, Enum):
    NONE = "none"
    BASIC = "basic"
    VISUAL = "visual"
    FULL = "full"
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class AttributeMatchResult(BaseModel):
    attribute_name: str = ""
    expected: str | None = None
    actual: str | None = None
    matched: bool = False
    is_core: bool = False

    model_config = {"extra": "allow"}


class VerificationResult(BaseModel):
    success: bool = False
    level: VerificationLevel = VerificationLevel.NONE
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    visual_verification: VerificationLevel = VerificationLevel.NONE
    visual_details: str = ""
    coordinate_verification: VerificationLevel = VerificationLevel.NONE
    coordinate_distance_px: float | None = None
    coordinate_details: str | None = None
    attribute_verification: VerificationLevel = VerificationLevel.NONE
    attribute_matches: list[AttributeMatchResult] | None = None
    attribute_details: str = ""
    overall_level: VerificationLevel = VerificationLevel.NONE
    overall_confidence: float = 0.0
    can_proceed: bool = True
    recovery_suggestion: str = ""

    model_config = {"extra": "allow"}


class ExecutionStepLog(BaseModel):
    id: str = ""
    execution_id: str = ""
    step_id: str = ""
    step_type: str = ""
    step_index: int = 0
    status: StepStatus = StepStatus.PENDING
    started_at: float | None = None
    completed_at: float | None = None
    error_message: str | None = None
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    verification_result: VerificationResult | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ExecutionAdvice(BaseModel):
    phase: str = ""
    level: str = "info"
    title: str = ""
    summary: str = ""
    rationale: str = ""
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    provider: str = "rules"
    local_ai_used: bool = False

    model_config = {"extra": "allow"}


class ExecutionFragileStep(BaseModel):
    step_index: int = 0
    step_id: str = ""
    reason: str = ""
    recommendation: str = ""

    model_config = {"extra": "allow"}


class ExecutionSummary(BaseModel):
    overview: str = ""
    warnings: list[str] = Field(default_factory=list)
    optimization_items: list[str] = Field(default_factory=list)
    fragile_steps: list[ExecutionFragileStep] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class ExecutionFeedback(BaseModel):
    execution_id: str = ""
    step_id: str | None = None
    step_index: int | None = None
    step_status: StepStatus | None = None
    progress: float = 0.0
    message: str = ""
    ai_check: ExecutionAdvice | None = None
    ai_assist: dict[str, Any] | None = None
    timestamp: float = Field(default_factory=time.time)

    model_config = {"extra": "allow"}


class ExecutionAIOptions(BaseModel):
    enabled: bool = False
    mode: str = "smart"
    vision_enabled: bool = False
    check_enabled: bool = True
    summary_enabled: bool = True

    model_config = {"extra": "allow"}


class ExecutionRecord(BaseModel):
    id: str = ""
    automation_id: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: float | None = None
    completed_at: float | None = None
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    error_summary: str | None = None
    step_logs: list[ExecutionStepLog] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)
    ai_summary: ExecutionSummary | None = None
    ai_step_insights: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
