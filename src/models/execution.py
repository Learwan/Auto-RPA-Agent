from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    PAUSED = "paused"


class VerificationLevel(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class AttributeMatchResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    attribute_name: str
    expected: str | None = None
    actual: str | None = None
    matched: bool = False
    is_core: bool = False


class VerificationResult(BaseModel):
    """Aggregated verification result emitted by ``StepVerifier``."""

    model_config = ConfigDict(extra="ignore")

    visual_verification: VerificationLevel = VerificationLevel.PASSED
    visual_details: str = ""
    coordinate_verification: VerificationLevel = VerificationLevel.PASSED
    coordinate_distance_px: float | None = None
    coordinate_details: str = ""
    attribute_verification: VerificationLevel = VerificationLevel.PASSED
    attribute_matches: list[AttributeMatchResult] = Field(default_factory=list)
    attribute_details: str = ""
    overall_level: VerificationLevel = VerificationLevel.PASSED
    overall_confidence: float = 1.0
    can_proceed: bool = True
    recovery_suggestion: str = ""


class ExecutionStepLog(BaseModel):
    """Per-step log persisted with each execution."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    step_id: str
    step_type: str
    step_index: int = 0
    status: StepStatus = StepStatus.PENDING
    started_at: float | None = None
    completed_at: float | None = None
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    error_message: str | None = None
    visual_comparison: dict[str, Any] | None = None
    verification_result: dict[str, Any] | None = None


class ExecutionFeedback(BaseModel):
    """Live feedback frame streamed over WebSocket during execution."""

    model_config = ConfigDict(extra="ignore")

    execution_id: str
    current_step: int = 0
    total_steps: int = 0
    step_status: StepStatus = StepStatus.PENDING
    step_description: str = ""
    step_id: str | None = None
    step_type: str | None = None
    visual_comparison: dict[str, Any] | None = None
    elapsed_ms: int = 0
    error: str | None = None
    ai_check: dict[str, Any] | None = None
    ai_assist: dict[str, Any] | None = None


class ExecutionFragileStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step_index: int
    step_id: str
    reason: str
    recommendation: str | None = None


class ExecutionAdvice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phase: str  # "pre" | "post" | "assist"
    level: str = "info"
    title: str = ""
    summary: str = ""
    rationale: str | None = None
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    provider: str = "rules"
    local_ai_used: bool = False


class ExecutionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overview: str = ""
    warnings: list[str] = Field(default_factory=list)
    optimization_items: list[str] = Field(default_factory=list)
    fragile_steps: list[ExecutionFragileStep] = Field(default_factory=list)
    provider: str = "rules"
    local_ai_used: bool = False


class ExecutionAIOptions(BaseModel):
    """Toggles for the optional AI advisor during execution."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    mode: str = "rules"  # "rules" | "local_ai" | "remote_ai"
    check_enabled: bool = True
    summary_enabled: bool = True
    assist_enabled: bool = True
    use_local_ai: bool = True


class ExecutionRecord(BaseModel):
    """Top-level execution record persisted to the database."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex)
    automation_id: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: float | None = None
    completed_at: float | None = None
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    error_summary: str | None = None
    variables: dict[str, Any] | None = None
    step_logs: list[ExecutionStepLog] | None = None
    ai_summary: ExecutionSummary | None = None
    ai_step_insights: dict[str, Any] | None = None
