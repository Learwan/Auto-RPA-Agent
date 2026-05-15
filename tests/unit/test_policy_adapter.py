import pytest

from src.training.policy_adapter import PolicyAdapter, PolicyAction, PolicyState
from src.models.automation import AutomationFlow, AutomationStep, ErrorAction, StepType


def _make_flow(steps_count: int = 3) -> AutomationFlow:
    steps = []
    for i in range(steps_count):
        steps.append(
            AutomationStep(
                id=f"step_{i}",
                type=StepType.CLICK,
                delay=500,
                on_error=ErrorAction.ABORT,
                retry_count=3,
            )
        )
    return AutomationFlow(
        id="test-flow",
        name="Test Flow",
        steps=steps,
    )


class TestPolicyAction:
    def test_defaults(self):
        action = PolicyAction(step_type="click")
        assert action.step_type == "click"
        assert action.delay == 500
        assert action.confidence == 1.0


class TestPolicyState:
    def test_defaults(self):
        state = PolicyState()
        assert state.step_index == 0
        assert state.error_count == 0
        assert state.consecutive_successes == 0


class TestPolicyAdapter:
    def test_select_action(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=0)
        action = adapter.select_action(state)
        assert action.step_type == "click"
        assert action.delay == 500

    def test_select_action_beyond_steps(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=10)
        action = adapter.select_action(state)
        assert action.step_type == "wait"
        assert action.confidence == 0.0

    def test_observe_success(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=0)
        action = adapter.select_action(state)
        next_state = PolicyState(step_index=1, consecutive_successes=0)
        adapter.observe_outcome(state, action, 0.1, next_state)
        assert next_state.consecutive_successes == 1
        assert next_state.error_count == 0

    def test_observe_failure(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=0)
        action = adapter.select_action(state)
        next_state = PolicyState(step_index=0, consecutive_successes=5)
        adapter.observe_outcome(state, action, -0.1, next_state)
        assert next_state.consecutive_successes == 0
        assert next_state.error_count == 1

    def test_retry_adjustment_on_errors(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=0)
        action = adapter.select_action(state)
        next_state = PolicyState(step_index=0, error_count=3)
        adapter.observe_outcome(state, action, -0.1, next_state)
        adjustments = adapter.adjustments
        assert len(adjustments) > 0
        assert adjustments[0]["adjustment"] == "increase_retry"

    def test_delay_adjustment_on_successes(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=0)
        action = adapter.select_action(state)
        next_state = PolicyState(step_index=0, consecutive_successes=6)
        adapter.observe_outcome(state, action, 0.1, next_state)
        adjustments = adapter.adjustments
        delay_adj = [a for a in adjustments if a["adjustment"] == "reduce_delay"]
        assert len(delay_adj) > 0

    def test_apply_to_flow(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=0)
        action = adapter.select_action(state)
        next_state = PolicyState(step_index=0, error_count=3)
        adapter.observe_outcome(state, action, -0.1, next_state)
        enhanced = adapter.apply_to_flow(flow)
        assert enhanced.confidence >= flow.confidence

    def test_reset(self):
        flow = _make_flow(3)
        adapter = PolicyAdapter(flow)
        state = PolicyState(step_index=0)
        action = adapter.select_action(state)
        next_state = PolicyState(step_index=0, error_count=3)
        adapter.observe_outcome(state, action, -0.1, next_state)
        adapter.reset()
        assert len(adapter.adjustments) == 0
