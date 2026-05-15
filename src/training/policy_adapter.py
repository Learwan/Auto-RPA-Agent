from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.models.automation import AutomationFlow, ErrorAction

logger = logging.getLogger(__name__)


@dataclass
class PolicyAction:
    step_type: str
    action_data: dict = field(default_factory=dict)
    delay: int = 500
    on_error: ErrorAction = ErrorAction.ABORT
    retry_count: int = 3
    confidence: float = 1.0


@dataclass
class PolicyState:
    step_index: int = 0
    error_count: int = 0
    consecutive_successes: int = 0
    flow_confidence: float = 0.0


class PolicyAdapter:
    def __init__(self, flow: AutomationFlow):
        self._flow = flow
        self._steps = flow.ordered_steps()
        self._state = PolicyState(flow_confidence=flow.confidence)
        self._adjustments: list[dict] = []

    def select_action(self, state: PolicyState) -> PolicyAction:
        if state.step_index >= len(self._steps):
            return PolicyAction(step_type="wait", confidence=0.0)

        step = self._steps[state.step_index]
        return PolicyAction(
            step_type=step.type.value,
            action_data=step.action,
            delay=step.delay,
            on_error=step.on_error,
            retry_count=step.retry_count,
            confidence=1.0,
        )

    def observe_outcome(
        self,
        state: PolicyState,
        action: PolicyAction,
        reward: float,
        next_state: PolicyState,
    ) -> None:
        if reward > 0:
            next_state.consecutive_successes += 1
        else:
            next_state.consecutive_successes = 0
            next_state.error_count += 1

        if next_state.error_count > 2 and action.retry_count < 5:
            self._adjustments.append(
                {
                    "step_index": state.step_index,
                    "adjustment": "increase_retry",
                    "old_retry_count": action.retry_count,
                    "new_retry_count": action.retry_count + 1,
                }
            )

        if next_state.consecutive_successes > 5 and action.delay > 200:
            self._adjustments.append(
                {
                    "step_index": state.step_index,
                    "adjustment": "reduce_delay",
                    "old_delay": action.delay,
                    "new_delay": max(100, action.delay - 100),
                }
            )

    def apply_to_flow(self, flow: AutomationFlow) -> AutomationFlow:
        enhanced = flow.model_copy(deep=True)
        steps = enhanced.ordered_steps()

        for adj in self._adjustments:
            idx = adj["step_index"]
            if idx < len(steps):
                step = steps[idx]
                if adj["adjustment"] == "increase_retry":
                    step.retry_count = adj["new_retry_count"]
                    step.on_error = ErrorAction.RETRY
                elif adj["adjustment"] == "reduce_delay":
                    step.delay = adj["new_delay"]

        enhanced.confidence = min(flow.confidence + 0.05, 1.0)
        return enhanced

    @property
    def adjustments(self) -> list[dict]:
        return list(self._adjustments)

    def reset(self) -> None:
        self._state = PolicyState(flow_confidence=self._flow.confidence)
        self._adjustments = []
