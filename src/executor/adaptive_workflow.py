from __future__ import annotations

import hashlib
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

LOOP_DETECTION_WINDOW = 10
LOOP_MAX_ITERATIONS = 3
STATE_FINGERPRINT_SIZE = 16


class BranchType(StrEnum):
    EXCLUSIVE = "exclusive"
    INCLUSIVE = "inclusive"
    PARALLEL = "parallel"


class StepType(StrEnum):
    ACTION = "action"
    CONDITION = "condition"
    LOOP = "loop"
    SUB_WORKFLOW = "sub_workflow"
    DYNAMIC = "dynamic"


@dataclass
class BranchCondition:
    variable_name: str
    operator: str
    expected_value: str | int | float | bool
    target_step_id: str

    def evaluate(self, variables: dict) -> bool:
        actual = variables.get(self.variable_name)
        if actual is None:
            return False
        ops = {
            "==": lambda a, e: a == e,
            "!=": lambda a, e: a != e,
            ">": lambda a, e: a > e,
            "<": lambda a, e: a < e,
            ">=": lambda a, e: a >= e,
            "<=": lambda a, e: a <= e,
            "contains": lambda a, e: e in str(a),
            "in": lambda a, e: a in e if isinstance(e, (list, tuple, set)) else False,
        }
        fn = ops.get(self.operator)
        if fn is None:
            return False
        try:
            return fn(actual, self.expected_value)
        except (TypeError, ValueError):
            return False


@dataclass
class ConditionalBranch:
    branch_id: str
    branch_type: BranchType
    conditions: list[BranchCondition]
    default_target: str | None = None

    def evaluate(self, variables: dict) -> list[str]:
        targets = []
        for cond in self.conditions:
            if cond.evaluate(variables):
                targets.append(cond.target_step_id)

        if self.branch_type == BranchType.EXCLUSIVE:
            return targets[:1] if targets else [self.default_target or ""]

        if self.branch_type == BranchType.INCLUSIVE:
            return targets if targets else [self.default_target or ""]

        if self.branch_type == BranchType.PARALLEL:
            return targets if targets else [self.default_target or ""]

        return targets


@dataclass
class LoopInfo:
    detected: bool
    pattern: list[str] = field(default_factory=list)
    iterations: int = 0
    suggested_max: int = LOOP_MAX_ITERATIONS


@dataclass
class DynamicStepTemplate:
    template_id: str
    step_type: str
    description_template: str
    parameter_schema: dict = field(default_factory=dict)
    required_context: list[str] = field(default_factory=list)

    def instantiate(self, context: dict) -> dict | None:
        missing = [k for k in self.required_context if k not in context]
        if missing:
            logger.debug(f"Template {self.template_id} missing context: {missing}")
            return None

        step = {
            "step_type": self.step_type,
            "description": self.description_template.format(**context),
        }
        for key, schema in self.parameter_schema.items():
            if key in context:
                step[key] = context[key]
            elif "default" in schema:
                step[key] = schema["default"]
            else:
                return None

        return step


class StateFingerprintTracker:
    def __init__(self, window_size: int = LOOP_DETECTION_WINDOW):
        self._window_size = window_size
        self._fingerprints: deque[str] = deque(maxlen=window_size)
        self._iteration_counts: dict[str, int] = {}

    def record_state(self, step_id: str, variables: dict) -> str:
        var_snapshot = {k: repr(v) for k, v in sorted(variables.items())}
        raw = f"{step_id}|{var_snapshot}"
        fp = hashlib.md5(raw.encode()).hexdigest()[:STATE_FINGERPRINT_SIZE]
        self._fingerprints.append(fp)
        self._iteration_counts[fp] = self._iteration_counts.get(fp, 0) + 1
        return fp

    def detect_loop(self) -> LoopInfo:
        if len(self._fingerprints) < 4:
            return LoopInfo(detected=False)

        recent = list(self._fingerprints)
        for pattern_len in range(2, len(recent) // 2 + 1):
            pattern = recent[-pattern_len:]
            prev = recent[-2 * pattern_len : -pattern_len]
            if pattern == prev:
                iterations = self._count_pattern_occurrences(pattern, recent)
                return LoopInfo(
                    detected=True,
                    pattern=[str(p) for p in pattern],
                    iterations=iterations,
                    suggested_max=LOOP_MAX_ITERATIONS,
                )

        for fp, count in self._iteration_counts.items():
            if count >= LOOP_MAX_ITERATIONS:
                return LoopInfo(
                    detected=True,
                    pattern=[fp],
                    iterations=count,
                    suggested_max=LOOP_MAX_ITERATIONS,
                )

        return LoopInfo(detected=False)

    def reset(self) -> None:
        self._fingerprints.clear()
        self._iteration_counts.clear()

    def _count_pattern_occurrences(self, pattern: list[str], sequence: list[str]) -> int:
        count = 0
        pattern_len = len(pattern)
        for i in range(len(sequence) - pattern_len + 1):
            if sequence[i : i + pattern_len] == pattern:
                count += 1
        return count


class AdaptiveWorkflowEngine:
    def __init__(self):
        self._branches: dict[str, ConditionalBranch] = {}
        self._templates: dict[str, DynamicStepTemplate] = {}
        self._loop_tracker = StateFingerprintTracker()
        self._dynamic_steps_generated: int = 0
        self._loop_detections: int = 0
        self._branch_evaluations: int = 0

    def register_branch(self, branch: ConditionalBranch) -> None:
        self._branches[branch.branch_id] = branch

    def register_template(self, template: DynamicStepTemplate) -> None:
        self._templates[template.template_id] = template

    def evaluate_branch(self, branch_id: str, variables: dict) -> list[str]:
        branch = self._branches.get(branch_id)
        if not branch:
            return []
        self._branch_evaluations += 1
        return branch.evaluate(variables)

    def generate_dynamic_step(self, template_id: str, context: dict) -> dict | None:
        template = self._templates.get(template_id)
        if not template:
            return None
        step = template.instantiate(context)
        if step:
            self._dynamic_steps_generated += 1
        return step

    def check_loop(self, step_id: str, variables: dict) -> LoopInfo:
        self._loop_tracker.record_state(step_id, variables)
        result = self._loop_tracker.detect_loop()
        if result.detected:
            self._loop_detections += 1
        return result

    def resolve_next_steps(
        self,
        current_step_id: str,
        variables: dict,
        default_next: list[str] | None = None,
    ) -> list[str]:
        for branch in self._branches.values():
            for cond in branch.conditions:
                if cond.target_step_id == current_step_id or current_step_id in [
                    c.target_step_id for c in branch.conditions
                ]:
                    return self.evaluate_branch(branch.branch_id, variables)

        return default_next or []

    def get_stats(self) -> dict:
        return {
            "branches_registered": len(self._branches),
            "templates_registered": len(self._templates),
            "dynamic_steps_generated": self._dynamic_steps_generated,
            "loop_detections": self._loop_detections,
            "branch_evaluations": self._branch_evaluations,
        }

    def reset_tracking(self) -> None:
        self._loop_tracker.reset()
        self._dynamic_steps_generated = 0
        self._loop_detections = 0
        self._branch_evaluations = 0
