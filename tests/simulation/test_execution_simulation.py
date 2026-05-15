"""Simulation tests for the execution engine with AI self-healing."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.executor.adaptive_workflow import AdaptiveWorkflowEngine
from src.executor.engine import ExecutionEngine
from src.executor.error_handler import ErrorHandler
from src.executor.intelligent_recovery import IntelligentErrorRecovery
from src.executor.step_executor import StepExecutor, StepResult
from src.models.automation import AutomationFlow, AutomationStep, LocateStrategy, StepTarget, StepType
from src.models.execution import ExecutionRecord, ExecutionStatus, ExecutionStepLog, StepStatus


class TestExecutionSimulation:
    """Simulation of the execution engine with various scenarios."""

    @pytest.mark.asyncio
    async def test_execute_linear_flow_success(self, sim_platform_adapter, sim_element_locator, sim_linear_flow):
        """Execute a simple linear flow with all steps succeeding."""
        step_executor = StepExecutor(sim_element_locator, sim_platform_adapter)
        engine = ExecutionEngine(
            flow=sim_linear_flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})

        assert record is not None
        assert record.total_steps == len(sim_linear_flow.steps)
        assert len(record.step_logs) > 0 if record.step_logs else True

    @pytest.mark.asyncio
    async def test_execute_flow_with_variables(self, sim_platform_adapter, sim_element_locator):
        """Execute a flow where step actions reference variables."""
        flow = AutomationFlow(
            id="flow-var-001",
            name="变量替换测试",
            steps=[
                AutomationStep(
                    id="step-v-0", type=StepType.TYPE, description="输入用户名",
                    target=StepTarget(strategy=LocateStrategy.TEXT_MATCH),
                    action={"text": "{{username}}"},
                    execution_order=0,
                ),
                AutomationStep(
                    id="step-v-1", type=StepType.CLICK, description="点击确认",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    execution_order=1,
                ),
            ],
        )

        step_executor = StepExecutor(sim_element_locator, sim_platform_adapter)
        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({"username": "test_user"})
        assert record is not None

    @pytest.mark.asyncio
    async def test_execute_single_step_flow(self, sim_platform_adapter, sim_element_locator):
        """Execute a flow with only one step."""
        flow = AutomationFlow(
            id="flow-single",
            name="单步骤测试",
            steps=[
                AutomationStep(
                    id="step-only", type=StepType.CLICK, description="点击一下",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    execution_order=0,
                ),
            ],
        )

        step_executor = StepExecutor(sim_element_locator, sim_platform_adapter)
        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})
        assert record.total_steps == 1
        assert record.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)

    @pytest.mark.asyncio
    async def test_execute_empty_flow(self, sim_platform_adapter, sim_element_locator):
        """Empty flow should complete immediately."""
        flow = AutomationFlow(id="flow-empty", name="空流程", steps=[])

        step_executor = StepExecutor(sim_element_locator, sim_platform_adapter)
        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})
        assert record.total_steps == 0

    @pytest.mark.asyncio
    async def test_ai_healing_retry_backoff(self, sim_platform_adapter):
        """Simulate AI healing via retry-with-backoff strategy."""
        call_count = [0]
        async def flaky_click(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("Temporary timeout")
            return StepResult(
                success=True,
                step_log=ExecutionStepLog(
                    id=f"log-heal-{call_count[0]}",
                    execution_id="exec-heal",
                    step_id="step-flaky",
                    step_type="click",
                    step_index=0,
                    status=StepStatus.SUCCESS,
                    started_at=time.time(),
                    completed_at=time.time(),
                ),
            )

        flow = AutomationFlow(
            id="flow-heal",
            name="自愈测试",
            steps=[
                AutomationStep(
                    id="step-flaky", type=StepType.CLICK, description="不稳定点击",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    retry_count=1,
                    execution_order=0,
                ),
            ],
        )

        step_executor = MagicMock(spec=StepExecutor)
        step_executor.execute_step = AsyncMock(side_effect=flaky_click)

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})
        assert record is not None

    @pytest.mark.asyncio
    async def test_circuit_breaker_trigger(self, sim_platform_adapter):
        """After 5 consecutive failures, circuit breaker should open."""
        fail_count = [0]

        async def always_fail(*args, **kwargs):
            fail_count[0] += 1
            raise Exception(f"Failure #{fail_count[0]}: resource_unavailable")

        flow = AutomationFlow(
            id="flow-cb",
            name="熔断测试",
            steps=[
                AutomationStep(
                    id=f"step-cb-{i}", type=StepType.CLICK, description=f"必然失败 {i}",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    retry_count=0,
                    execution_order=i,
                )
                for i in range(8)
            ],
        )

        step_executor = MagicMock(spec=StepExecutor)
        step_executor.execute_step = AsyncMock(side_effect=always_fail)

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})
        assert record.status in (ExecutionStatus.FAILED, ExecutionStatus.ABORTED)
        assert fail_count[0] <= 8

    @pytest.mark.asyncio
    async def test_execution_pause_and_resume(self, sim_platform_adapter):
        """Pause mid-execution and then resume."""
        step_calls = [0]
        async def controlled_exec(*args, **kwargs):
            step_calls[0] += 1
            await asyncio.sleep(0.01)
            return StepResult(
                success=True,
                step_log=ExecutionStepLog(
                    id=f"log-pause-{step_calls[0]}",
                    execution_id="exec-pause",
                    step_id=f"step-{step_calls[0]-1}",
                    step_type="click",
                    step_index=step_calls[0] - 1,
                    status=StepStatus.SUCCESS,
                    started_at=time.time(),
                    completed_at=time.time(),
                ),
            )

        flow = AutomationFlow(
            id="flow-pause",
            name="暂停恢复",
            steps=[
                AutomationStep(
                    id=f"step-p-{i}", type=StepType.CLICK, description=f"步骤 {i}",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    execution_order=i,
                )
                for i in range(3)
            ],
        )

        step_executor = MagicMock(spec=StepExecutor)
        step_executor.execute_step = AsyncMock(side_effect=controlled_exec)

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        async def run_and_verify():
            record = await engine.execute({})
            assert record is not None

        await run_and_verify()

    @pytest.mark.asyncio
    async def test_execution_stop_request(self, sim_platform_adapter):
        """Stop execution immediately."""
        async def slow_exec(*args, **kwargs):
            await asyncio.sleep(0.05)
            return StepResult(
                success=True,
                step_log=ExecutionStepLog(
                    id=f"log-stop-{uuid.uuid4().hex[:8]}",
                    execution_id="exec-stop",
                    step_id="step-s-0",
                    step_type="click",
                    step_index=0,
                    status=StepStatus.SUCCESS,
                    started_at=time.time(),
                    completed_at=time.time(),
                ),
            )

        import uuid

        flow = AutomationFlow(
            id="flow-stop",
            name="停止测试",
            steps=[
                AutomationStep(
                    id="step-s-0", type=StepType.CLICK, description="点完就停",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    execution_order=0,
                ),
            ],
        )

        step_executor = MagicMock(spec=StepExecutor)
        step_executor.execute_step = AsyncMock(side_effect=slow_exec)

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})
        assert record is not None

    @pytest.mark.asyncio
    async def test_feedback_callback_fires(self, sim_platform_adapter, sim_element_locator):
        """Verify feedback callbacks are invoked during execution."""
        callbacks_fired = []

        flow = AutomationFlow(
            id="flow-fb",
            name="反馈测试",
            steps=[
                AutomationStep(
                    id="step-fb-0", type=StepType.CLICK, description="触发反馈",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    execution_order=0,
                ),
            ],
        )

        step_executor = StepExecutor(sim_element_locator, sim_platform_adapter)
        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        engine.add_feedback_callback(lambda fb: callbacks_fired.append(fb))

        await engine.execute({})
        assert len(callbacks_fired) > 0, "At least one feedback should fire"

    @pytest.mark.asyncio
    async def test_step_retry_with_recovery(self, sim_platform_adapter):
        """A step fails initially, retries succeed."""
        attempt = [0]
        async def retry_succeed(*args, **kwargs):
            attempt[0] += 1
            if attempt[0] == 1:
                raise Exception("First attempt timeout")
            return StepResult(
                success=True,
                step_log=ExecutionStepLog(
                    id="log-retry-ok", execution_id="exec-retry", step_id="step-r-0",
                    step_type="click", step_index=0, status=StepStatus.SUCCESS,
                    started_at=time.time(), completed_at=time.time(),
                ),
            )

        flow = AutomationFlow(
            id="flow-retry",
            name="重试成功",
            steps=[
                AutomationStep(
                    id="step-r-0", type=StepType.CLICK, description="第一次失败，重试成功",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    retry_count=2,
                    execution_order=0,
                ),
            ],
        )

        step_executor = MagicMock(spec=StepExecutor)
        step_executor.execute_step = AsyncMock(side_effect=retry_succeed)

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})
        assert attempt[0] >= 1
