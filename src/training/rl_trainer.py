from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.models.automation import AutomationFlow, AutomationStep

logger = logging.getLogger(__name__)


class TrainingStatus(StrEnum):
    IDLE = "idle"
    COLLECTING = "collecting"
    TRAINING = "training"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TrainingConfig:
    episodes: int = 1000
    learning_rate: float = 3e-4
    gamma: float = 0.99
    clip_range: float = 0.2
    batch_size: int = 64
    max_steps_per_episode: int = 50
    reward_success: float = 1.0
    reward_step_penalty: float = -0.01
    reward_failure: float = -1.0


@dataclass
class TrainingEpisode:
    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    steps_taken: int = 0
    total_reward: float = 0.0
    success: bool = False
    duration_ms: float = 0.0


@dataclass
class TrainingResult:
    session_id: str
    status: TrainingStatus
    episodes_completed: int = 0
    success_rate: float = 0.0
    avg_reward: float = 0.0
    enhanced_flow: AutomationFlow | None = None
    episodes: list[TrainingEpisode] = field(default_factory=list)


class SyntheticEnvironment:
    def __init__(self, flow: AutomationFlow):
        self._flow = flow
        self._current_step = 0
        self._state: dict[str, Any] = {}
        self._done = False

    def reset(self) -> dict[str, Any]:
        self._current_step = 0
        self._done = False
        self._state = {"step_index": 0, "last_action": None, "errors": []}
        return self._state.copy()

    def step(self, action: dict) -> tuple[dict[str, Any], float, bool, dict]:
        if self._done:
            return self._state.copy(), 0.0, True, {}

        reward = -0.01
        info: dict[str, Any] = {}

        expected_step = self._get_expected_step()
        if expected_step is None:
            self._done = True
            reward = 1.0
            info["reason"] = "completed_all_steps"
            return self._state.copy(), reward, True, info

        action_type = action.get("type", "")
        if action_type == expected_step.type.value:
            reward = 0.1
            self._current_step += 1
        elif action_type == "wait":
            reward = -0.005
        else:
            reward = -0.1
            self._state.setdefault("errors", []).append(f"wrong_action_at_step_{self._current_step}")

        if len(self._state.get("errors", [])) >= 3:
            self._done = True
            reward = -1.0
            info["reason"] = "too_many_errors"

        self._state["step_index"] = self._current_step
        self._state["last_action"] = action_type
        return self._state.copy(), reward, self._done, info

    def _get_expected_step(self) -> AutomationStep | None:
        steps = self._flow.ordered_steps()
        if self._current_step < len(steps):
            return steps[self._current_step]
        return None


class RLTrainer:
    def __init__(self, config: TrainingConfig | None = None):
        self._config = config or TrainingConfig()
        self._status = TrainingStatus.IDLE
        self._episodes: list[TrainingEpisode] = []

    async def train(
        self,
        flow: AutomationFlow,
        session_id: str,
        progress_callback: Any = None,
    ) -> TrainingResult:
        self._status = TrainingStatus.TRAINING
        self._episodes = []

        env = SyntheticEnvironment(flow)

        for ep in range(self._config.episodes):
            state = env.reset()
            total_reward = 0.0
            steps = 0

            for step_idx in range(self._config.max_steps_per_episode):
                action = self._select_action(state, flow, step_idx)
                next_state, reward, done, info = env.step(action)
                total_reward += reward
                steps += 1
                state = next_state
                if done:
                    break

            episode = TrainingEpisode(
                steps_taken=steps,
                total_reward=total_reward,
                success=total_reward > 0.5,
            )
            self._episodes.append(episode)

            if progress_callback and ep % 100 == 0:
                await progress_callback(ep, self._config.episodes)

        success_count = sum(1 for ep in self._episodes if ep.success)
        success_rate = success_count / max(len(self._episodes), 1)
        avg_reward = sum(ep.total_reward for ep in self._episodes) / max(len(self._episodes), 1)

        enhanced_flow = self._export_enhanced_flow(flow) if success_rate > 0.5 else None
        self._status = TrainingStatus.COMPLETED

        return TrainingResult(
            session_id=session_id,
            status=self._status,
            episodes_completed=len(self._episodes),
            success_rate=success_rate,
            avg_reward=avg_reward,
            enhanced_flow=enhanced_flow,
        )

    def _select_action(self, state: dict, flow: AutomationFlow, step_idx: int) -> dict:
        steps = flow.ordered_steps()
        if step_idx < len(steps):
            step = steps[step_idx]
            return {"type": step.type.value, "data": step.action}
        return {"type": "wait"}

    def _export_enhanced_flow(self, flow: AutomationFlow) -> AutomationFlow:
        enhanced = flow.model_copy(deep=True)
        enhanced.confidence = min(flow.confidence + 0.1, 1.0)
        for step in enhanced.steps:
            if step.retry_count < 3:
                step.retry_count += 1
        return enhanced

    @property
    def status(self) -> TrainingStatus:
        return self._status
