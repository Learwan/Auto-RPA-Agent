import pytest

from src.training.synthetic_env import (
    EnvironmentState,
    StepOutcome,
    StepTransition,
    SyntheticEnvironment,
)
from src.models.automation import AutomationFlow, AutomationStep, StepType


def _make_flow(steps_count: int = 3) -> AutomationFlow:
    steps = []
    for i in range(steps_count):
        steps.append(
            AutomationStep(
                id=f"step_{i}",
                type=StepType.CLICK,
                delay=0,
            )
        )
    return AutomationFlow(
        id="test-flow",
        name="Test Flow",
        steps=steps,
    )


class TestEnvironmentState:
    def test_defaults(self):
        state = EnvironmentState()
        assert state.step_index == 0
        assert state.completed is False
        assert state.errors == []


class TestStepTransition:
    def test_creation(self):
        state = EnvironmentState()
        t = StepTransition(state=state, reward=0.1, done=False)
        assert t.reward == 0.1
        assert t.done is False


class TestSyntheticEnvironment:
    def test_reset(self):
        flow = _make_flow(3)
        env = SyntheticEnvironment(flow)
        state = env.reset()
        assert state.step_index == 0
        assert state.completed is False

    def test_correct_action_advances(self):
        flow = _make_flow(3)
        env = SyntheticEnvironment(flow)
        env.reset()
        action = {"type": "click"}
        t = env.step(action)
        assert t.reward > 0
        assert t.info.get("outcome") == StepOutcome.SUCCESS.value
        assert env.state.step_index == 1

    def test_wrong_action_penalty(self):
        flow = _make_flow(3)
        env = SyntheticEnvironment(flow)
        env.reset()
        action = {"type": "type"}
        t = env.step(action)
        assert t.reward < 0
        assert t.info.get("outcome") == StepOutcome.WRONG_ACTION.value

    def test_wait_action(self):
        flow = _make_flow(3)
        env = SyntheticEnvironment(flow)
        env.reset()
        action = {"type": "wait"}
        t = env.step(action)
        assert t.reward == -0.005
        assert t.info.get("outcome") == StepOutcome.TIMEOUT.value

    def test_completion(self):
        flow = _make_flow(2)
        env = SyntheticEnvironment(flow)
        env.reset()
        t1 = env.step({"type": "click"})
        assert not t1.done
        t2 = env.step({"type": "click"})
        assert t2.done is True
        assert t2.reward > 0

    def test_too_many_errors(self):
        flow = _make_flow(5)
        env = SyntheticEnvironment(flow)
        env.reset()
        for _ in range(3):
            t = env.step({"type": "wrong"})
        assert t.done is True
        assert t.info.get("outcome") == StepOutcome.FAILURE.value

    def test_current_step(self):
        flow = _make_flow(3)
        env = SyntheticEnvironment(flow)
        env.reset()
        assert env.current_step is not None
        assert env.current_step.id == "step_0"

    def test_total_steps(self):
        flow = _make_flow(5)
        env = SyntheticEnvironment(flow)
        assert env.total_steps == 5

    def test_step_after_completion(self):
        flow = _make_flow(1)
        env = SyntheticEnvironment(flow)
        env.reset()
        t1 = env.step({"type": "click"})
        assert t1.done is True
        t2 = env.step({"type": "click"})
        assert t2.done is True
        assert t2.reward == 0.0
