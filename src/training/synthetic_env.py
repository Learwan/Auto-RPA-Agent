from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.models.automation import AutomationFlow, AutomationStep

logger = logging.getLogger(__name__)


class StepOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    WRONG_ACTION = "wrong_action"


@dataclass
class EnvironmentState:
    step_index: int = 0
    last_action: str | None = None
    errors: list[str] = field(default_factory=list)
    completed: bool = False
    total_reward: float = 0.0
    step_history: list[dict] = field(default_factory=list)


@dataclass
class StepTransition:
    state: EnvironmentState
    reward: float
    done: bool
    info: dict = field(default_factory=dict)


class SyntheticEnvironment:
    def __init__(self, flow: AutomationFlow):
        self._flow = flow
        self._steps = flow.ordered_steps()
        self._state = EnvironmentState()

    def reset(self) -> EnvironmentState:
        self._state = EnvironmentState()
        return self._state

    def step(self, action: dict) -> StepTransition:
        if self._state.completed:
            return StepTransition(
                state=self._state,
                reward=0.0,
                done=True,
                info={"reason": "already_completed"},
            )

        reward = -0.01
        info: dict[str, Any] = {}
        done = False

        if self._state.step_index >= len(self._steps):
            self._state.completed = True
            reward = 1.0
            info["reason"] = "completed_all_steps"
            info["outcome"] = StepOutcome.SUCCESS.value
            self._state.step_history.append(
                {
                    "step_index": self._state.step_index,
                    "action": action,
                    "reward": reward,
                    "outcome": StepOutcome.SUCCESS.value,
                }
            )
            return StepTransition(state=self._state, reward=reward, done=True, info=info)

        expected_step = self._steps[self._state.step_index]
        action_type = action.get("type", "")

        if action_type == expected_step.type.value:
            reward = 0.1
            self._state.step_index += 1
            info["outcome"] = StepOutcome.SUCCESS.value
            if self._state.step_index >= len(self._steps):
                done = True
                reward = 1.0
                info["reason"] = "completed_all_steps"
                info["outcome"] = StepOutcome.SUCCESS.value
        elif action_type == "wait":
            reward = -0.005
            info["outcome"] = StepOutcome.TIMEOUT.value
        else:
            reward = -0.1
            self._state.errors.append(f"wrong_action_at_step_{self._state.step_index}")
            info["outcome"] = StepOutcome.WRONG_ACTION.value

        self._state.step_history.append(
            {
                "step_index": self._state.step_index,
                "action": action,
                "reward": reward,
                "outcome": info.get("outcome", "unknown"),
            }
        )

        if len(self._state.errors) >= 3:
            done = True
            reward = -1.0
            info["reason"] = "too_many_errors"
            info["outcome"] = StepOutcome.FAILURE.value

        self._state.last_action = action_type
        self._state.total_reward += reward
        self._state.completed = done
        return StepTransition(state=self._state, reward=reward, done=done, info=info)

    @property
    def current_step(self) -> AutomationStep | None:
        if self._state.step_index < len(self._steps):
            return self._steps[self._state.step_index]
        return None

    @property
    def total_steps(self) -> int:
        return len(self._steps)

    @property
    def state(self) -> EnvironmentState:
        return self._state
