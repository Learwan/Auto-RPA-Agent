from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.desktop import Point, Rect

STRUCTURE_VERSION = 2


class StepType(str, Enum):
    CLICK = "click"
    TYPE = "type"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    DRAG = "drag"
    SWITCH_WINDOW = "switch_window"
    NAVIGATE = "navigate"
    UPLOAD_FILE = "upload_file"
    DOWNLOAD_FILE = "download_file"
    FILE_OP = "file_op"
    WAIT = "wait"
    CONDITION = "condition"
    LOOP = "loop"


class LocateStrategy(str, Enum):
    ACCESSIBILITY_ID = "accessibility_id"
    TEXT_MATCH = "text_match"
    CSS_SELECTOR = "css_selector"
    XPATH = "xpath"
    IMAGE_MATCH = "image_match"
    POSITION = "position"


# Backwards-compatible alias used by ``executor.engine`` for self-healing.
LocatorStrategy = LocateStrategy


class ErrorAction(str, Enum):
    RETRY = "retry"
    SKIP = "skip"
    ABORT = "abort"
    ASK_USER = "ask_user"
    FALLBACK = "fallback"
    RELOCATE = "relocate"
    ADJUST_AND_RETRY = "adjust_and_retry"


# ---------------------------------------------------------------------------
# Stable-locator policy
# ---------------------------------------------------------------------------
# These constants implement the user-requested closed-loop guarantee:
# *no fragile nodes, no naked hard coordinates as primary path*. We enforce
# this both in the locator (runtime) and in flow generation (design-time)
# by classifying strategies into reliability buckets and requiring at least
# one reinforcing locator for ``POSITION``-style targets.
STABLE_LOCATOR_STRATEGIES: frozenset[LocateStrategy] = frozenset(
    {
        LocateStrategy.ACCESSIBILITY_ID,
        LocateStrategy.CSS_SELECTOR,
        LocateStrategy.XPATH,
    }
)
SEMI_STABLE_LOCATOR_STRATEGIES: frozenset[LocateStrategy] = frozenset(
    {LocateStrategy.TEXT_MATCH, LocateStrategy.IMAGE_MATCH}
)


class StepTarget(BaseModel):
    """Target descriptor consumed by ``ElementLocator`` and platform adapters."""

    model_config = ConfigDict(extra="ignore")

    strategy: LocateStrategy = LocateStrategy.ACCESSIBILITY_ID
    accessibility_id: str | None = None
    title: str | None = None
    text_contains: str | None = None
    role: str | None = None
    class_name: str | None = None
    selector: str | None = None
    xpath: str | None = None
    image_path: str | None = None
    position: Point | None = None
    bounds: Rect | None = None
    url: str | None = None
    frame: str | None = None
    window_title: str | None = None
    tab_id: str | None = None
    expected_attributes: dict[str, Any] | None = Field(default_factory=dict)
    value: str | None = None
    text_preview: str | None = None

    @model_validator(mode="after")
    def _normalize_expected_attributes(self) -> "StepTarget":
        if self.expected_attributes is None:
            object.__setattr__(self, "expected_attributes", {})
        return self

    # ----- locator introspection helpers used by analyzer/executor -----
    def has_stable_locator(self) -> bool:
        return bool(
            self.accessibility_id
            or self.selector
            or self.xpath
        )

    def has_semantic_reinforcement(self) -> bool:
        """Return True if non-coordinate locator hints exist for the target.

        Used by the closure assessor to decide whether a non-stable strategy
        (TEXT_MATCH / IMAGE_MATCH / POSITION) has enough surrounding context
        to be safely retried.
        """
        return bool(
            self.title
            or self.text_contains
            or self.role
            or self.class_name
            or (self.expected_attributes or {})
            or self.window_title
            or self.url
        )

    def has_strong_text_reinforcement(self) -> bool:
        """Stricter variant used by the recorder/analyzer.

        Plain ``role + title + window_title`` is not enough to call a text-based
        locator "confirmed" because the same words appear on many surfaces.
        We require either real identifiers, a class name, expected attributes
        or a URL to mark the locator as confirmed at recording time.
        """
        if self.has_stable_locator():
            return True
        if self.class_name:
            return True
        if self.url:
            return True
        if self.expected_attributes:
            return True
        return False

    def inferred_locator_stability(self) -> str:
        if self.has_stable_locator():
            return "high"
        if self.strategy in SEMI_STABLE_LOCATOR_STRATEGIES and self.has_semantic_reinforcement():
            return "medium"
        if self.strategy in SEMI_STABLE_LOCATOR_STRATEGIES:
            return "low"
        if self.strategy == LocateStrategy.POSITION and self.has_semantic_reinforcement():
            return "low"
        return "very_low"

    def inferred_requires_confirmation(self) -> bool:
        if self.has_stable_locator():
            return False
        if self.strategy == LocateStrategy.POSITION:
            return True
        if self.strategy == LocateStrategy.IMAGE_MATCH:
            return True
        if self.strategy == LocateStrategy.TEXT_MATCH and not self.has_strong_text_reinforcement():
            return True
        return False


class StepCondition(BaseModel):
    """Boolean check evaluated before/after a step (e.g. ``element_exists``)."""

    model_config = ConfigDict(extra="ignore")

    field: str
    operator: str = "eq"
    value: Any = None
    timeout_ms: int = 3000
    description: str | None = None
    critical: bool = False


class StepBranch(BaseModel):
    """Edge in the flow DAG: outcome -> next step."""

    model_config = ConfigDict(extra="ignore")

    target_step_id: str
    outcome: str = "default"
    condition: StepCondition | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowVariable(BaseModel):
    """Variable referenced by a flow (``${name}`` resolution)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    var_type: str = "string"
    default_value: Any = None
    description: str | None = None
    required: bool = False


class ErrorHandlingPolicy(BaseModel):
    """Flow-level retry / recovery configuration."""

    model_config = ConfigDict(extra="ignore")

    max_retries: int = 3
    retry_delay_ms: int = 500
    on_global_failure: ErrorAction = ErrorAction.ABORT
    notify_on_failure: bool = False
    capture_screenshot_on_failure: bool = True


class DetectedPattern(BaseModel):
    """A recurring operation pattern found by ``PatternDetector``."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex)
    pattern_type: str = "repeat"
    pattern_sequence: list[Any] = Field(default_factory=list)
    instances: list[dict[str, Any]] = Field(default_factory=list)
    support: int = 0
    avg_duration_ms: int = 0
    confidence: float = 0.0


class AutomationStep(BaseModel):
    """A single executable step inside an ``AutomationFlow``."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex)
    type: StepType
    action: dict[str, Any] = Field(default_factory=dict)
    target: StepTarget = Field(default_factory=StepTarget)
    atomic: bool = True
    depends_on: list[str] = Field(default_factory=list)
    preconditions: list[StepCondition] = Field(default_factory=list)
    branches: list[StepBranch] = Field(default_factory=list)
    execution_order: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    condition: StepCondition | None = None
    delay: int = 0
    on_error: ErrorAction = ErrorAction.RETRY
    retry_count: int = 0
    description: str | None = None
    verification_enabled: bool = True
    verification_strict: bool = False

    def locator_requires_confirmation(self) -> bool:
        """Return True if this step must not auto-execute without review.

        The analyzer is responsible for tagging fragile steps at recording
        time (``metadata['locator_requires_confirmation']``).  At runtime we
        treat that flag as the source of truth, but we still refuse to trust
        targets that have *no* identifier and are pinned to raw pixel
        coordinates — those are always considered fragile.

        A stale metadata flag is dropped if the target has subsequently been
        reinforced with a stable or strongly-textual locator.  That contract
        is what ``FlowClosureAssessor`` uses to certify a flow as a
        closed-loop ready.
        """

        target = self.target or StepTarget()
        if target.has_stable_locator():
            return False

        metadata = self.metadata or {}
        flagged = bool(metadata.get("locator_requires_confirmation"))
        confirmed = bool(metadata.get("locator_confirmed"))

        if flagged and not confirmed:
            # Honour the analyzer flag unless the target has been reinforced
            # enough at runtime to be considered safe.
            if (
                target.strategy in SEMI_STABLE_LOCATOR_STRATEGIES
                and target.has_strong_text_reinforcement()
            ):
                return False
            return True

        # Even when the analyzer did not flag the step, naked coordinate
        # / image targets remain fragile.  This is the core anti-fragility
        # rule for the closed loop.
        if target.strategy == LocateStrategy.POSITION:
            return True
        if (
            target.strategy == LocateStrategy.IMAGE_MATCH
            and not target.has_strong_text_reinforcement()
        ):
            return True
        return False


class AutomationFlow(BaseModel):
    """A complete automation flow ready for editing and execution."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    description: str = ""
    structure_version: int = STRUCTURE_VERSION
    steps: list[AutomationStep] = Field(default_factory=list)
    entry_step_ids: list[str] = Field(default_factory=list)
    variables: list[FlowVariable] = Field(default_factory=list)
    error_handling: ErrorHandlingPolicy = Field(default_factory=ErrorHandlingPolicy)
    confidence: float = 0.0
    source_session_id: str | None = None
    source_pattern: dict[str, Any] | None = None
    source_screenshots: list[str] = Field(default_factory=list)
    status: str = "draft"
    execution_count: int = 0
    success_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    window_context: str | None = None
    behavior_tree: dict[str, Any] | None = None

    # ---------- structural helpers ----------
    @model_validator(mode="after")
    def _normalize_entry_steps(self) -> "AutomationFlow":
        if not self.entry_step_ids and self.steps:
            for step in self.steps:
                if not step.depends_on:
                    object.__setattr__(self, "entry_step_ids", [self.steps[0].id])
                    break
        return self

    def step_map(self) -> dict[str, AutomationStep]:
        return {step.id: step for step in self.steps}

    def ordered_steps(self) -> list[AutomationStep]:
        ordered = sorted(
            self.steps,
            key=lambda s: (s.execution_order if s.execution_order is not None else 1_000_000, self.steps.index(s)),
        )
        return ordered

    def set_entry_steps(self, step_ids: list[str]) -> None:
        valid_ids = {step.id for step in self.steps}
        for sid in step_ids:
            if sid not in valid_ids:
                raise ValueError(f"Entry step id not found: {sid}")
        self.entry_step_ids = list(step_ids)

    def add_step(self, step: AutomationStep) -> AutomationStep:
        if any(existing.id == step.id for existing in self.steps):
            raise ValueError(f"Step id already exists: {step.id}")
        self.steps.append(step)
        if not self.entry_step_ids and not step.depends_on:
            self.entry_step_ids = [step.id]
        return step

    def remove_step(self, step_id: str) -> AutomationStep:
        for index, step in enumerate(self.steps):
            if step.id == step_id:
                removed = self.steps.pop(index)
                self.entry_step_ids = [sid for sid in self.entry_step_ids if sid != step_id]
                for other in self.steps:
                    other.depends_on = [d for d in other.depends_on if d != step_id]
                    other.branches = [b for b in other.branches if b.target_step_id != step_id]
                return removed
        raise ValueError(f"Step not found: {step_id}")

    def update_step(self, step_id: str, patch: dict[str, Any]) -> AutomationStep:
        for index, step in enumerate(self.steps):
            if step.id == step_id:
                data = step.model_dump()
                data.update(patch)
                # Pydantic will coerce nested models when validating.
                self.steps[index] = AutomationStep.model_validate(data)
                return self.steps[index]
        raise ValueError(f"Step not found: {step_id}")

    def upsert_branch(self, step_id: str, branch: StepBranch) -> StepBranch:
        for step in self.steps:
            if step.id == step_id:
                for existing in step.branches:
                    if (
                        existing.target_step_id == branch.target_step_id
                        and existing.outcome == branch.outcome
                    ):
                        existing.condition = branch.condition
                        existing.description = branch.description
                        existing.metadata = dict(branch.metadata or {})
                        return existing
                step.branches.append(branch)
                return branch
        raise ValueError(f"Step not found: {step_id}")

    def remove_branch(self, step_id: str, target_step_id: str, outcome: str | None = None) -> StepBranch:
        for step in self.steps:
            if step.id != step_id:
                continue
            for index, branch in enumerate(step.branches):
                if branch.target_step_id != target_step_id:
                    continue
                if outcome is None or branch.outcome == outcome:
                    return step.branches.pop(index)
            raise ValueError(
                f"Branch not found on step {step_id}: target={target_step_id} outcome={outcome}"
            )
        raise ValueError(f"Step not found: {step_id}")

    def get_next_step_ids(self, step_id: str, outcome: str | None = None) -> list[str]:
        for step in self.steps:
            if step.id != step_id:
                continue
            if step.branches:
                candidates = [
                    b for b in step.branches
                    if outcome is None or b.outcome == outcome
                ]
                if not candidates and outcome is not None:
                    candidates = [b for b in step.branches if b.outcome == "default"]
                if candidates:
                    return [b.target_step_id for b in candidates]
            ordered = self.ordered_steps()
            for index, candidate in enumerate(ordered):
                if candidate.id == step.id and index + 1 < len(ordered):
                    return [ordered[index + 1].id]
            return []
        return []
