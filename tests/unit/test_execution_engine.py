import asyncio
import base64

import pytest

from src.executor.engine import ExecutionEngine
from src.executor.element_locator import LocatedElement
from src.executor.error_handler import ErrorDecision
from src.executor.intelligent_recovery import HealingAction, HealingLevel
from src.executor.step_executor import StepResult
from src.models.automation import AutomationFlow, AutomationStep, ErrorAction, LocateStrategy, StepTarget, StepType
from src.models.desktop import Point, UIElement
from src.models.execution import ExecutionAdvice, ExecutionFragileStep, ExecutionStepLog, ExecutionSummary, StepStatus


class DummySafetyController:
    def reset(self):
        return None


class DummyStepExecutor:
    def __init__(self):
        self.safety_controller = DummySafetyController()

    async def execute_step(self, step, variables, execution_id, step_index):
        if step.id == "step-1":
            return StepResult(
                success=False,
                step_log=ExecutionStepLog(
                    id="log-1",
                    execution_id=execution_id,
                    step_id=step.id,
                    step_type=step.type.value,
                    step_index=step_index,
                    status=StepStatus.FAILED,
                    error_message="执行前验证失败: 窗口未找到 (企业微信)",
                ),
            )

        return StepResult(
            success=True,
            step_log=ExecutionStepLog(
                id="log-2",
                execution_id=execution_id,
                step_id=step.id,
                step_type=step.type.value,
                step_index=step_index,
                status=StepStatus.SUCCESS,
            ),
        )


class DummyErrorHandler:
    def __init__(self):
        self.checkpoint_manager = None

    async def handle_error(self, step, error, context):
        return ErrorDecision(
            action=ErrorAction.SKIP,
            message="Skipped missing window target",
        )


class DummyAdvisor:
    def __init__(self):
        self.step_insights = {
            "1": {
                "step_index": 1,
                "step_id": "step-1",
                "pre": {
                    "phase": "pre",
                    "level": "warning",
                    "title": "执行前检查",
                    "summary": "需要确认窗口状态",
                    "warnings": ["窗口可能未激活"],
                    "suggestions": ["先激活窗口"],
                    "provider": "rules",
                    "local_ai_used": False,
                }
            }
        }

    async def advise_before_step(self, step, step_index, total_steps):
        return ExecutionAdvice(
            phase="pre",
            level="warning",
            title="执行前检查",
            summary="需要确认窗口状态",
            warnings=["窗口可能未激活"],
            suggestions=["先激活窗口"],
        )

    async def advise_after_step(
        self,
        step,
        step_index,
        total_steps,
        success,
        error,
        verification_result,
        visual_comparison,
        screenshot_before=None,
        screenshot_after=None,
    ):
        return ExecutionAdvice(
            phase="post",
            level="success" if success else "error",
            title="结果检查",
            summary="步骤已完成",
            suggestions=["继续执行后续步骤"],
        )

    async def advise_assist(
        self,
        step,
        step_index,
        total_steps,
        error,
        verification_result,
        screenshot_before=None,
        screenshot_after=None,
    ):
        return ExecutionAdvice(
            phase="assist",
            level="warning",
            title="协助执行",
            summary="建议人工确认后重试",
            warnings=[error or "unknown"],
            suggestions=["重新聚焦目标窗口"],
        )

    async def build_summary(self, flow, record):
        return ExecutionSummary(
            overview="执行完成，建议优化窗口切换稳定性。",
            warnings=["窗口切换依赖运行时状态"],
            optimization_items=["给切换窗口步骤增加前置检查"],
            fragile_steps=[
                ExecutionFragileStep(
                    step_index=1,
                    step_id="step-1",
                    reason="窗口状态可能漂移",
                    recommendation="执行前先确认目标窗口已激活",
                )
            ],
        )


def _make_flow() -> AutomationFlow:
    return AutomationFlow(
        id="flow-1",
        name="Skip Missing Window",
        steps=[
            AutomationStep(
                id="step-1",
                type=StepType.SWITCH_WINDOW,
                action={"window_title": "企业微信"},
                target=StepTarget(window_title="企业微信", title="企业微信"),
                on_error=ErrorAction.RETRY,
                retry_count=0,
            ),
            AutomationStep(
                id="step-2",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(),
            ),
        ],
    )


@pytest.mark.asyncio
async def test_skipped_step_does_not_count_as_failed_and_execution_continues():
    engine = ExecutionEngine(
        flow=_make_flow(),
        step_executor=DummyStepExecutor(),
        error_handler=DummyErrorHandler(),
        execution_id="exec-skip",
    )

    record = await engine.execute()

    assert record.status.value == "completed"
    assert record.completed_steps == 1
    assert record.failed_steps == 0
    assert record.step_logs[0].status == StepStatus.SKIPPED
    assert record.step_logs[1].status == StepStatus.SUCCESS


class SuccessfulStepExecutor:
    def __init__(self):
        self.safety_controller = DummySafetyController()

    async def execute_step(self, step, variables, execution_id, step_index):
        return StepResult(
            success=True,
            step_log=ExecutionStepLog(
                id=f"log-{step.id}",
                execution_id=execution_id,
                step_id=step.id,
                step_type=step.type.value,
                step_index=step_index,
                status=StepStatus.SUCCESS,
            ),
            screenshot_before=b"before-image",
            screenshot_after=b"after-image",
        )


class TelemetryStepExecutor:
    def __init__(self):
        self.safety_controller = DummySafetyController()

    async def execute_step(self, step, variables, execution_id, step_index):
        located = LocatedElement(
            element=UIElement(role="button", title="保存", bounds=None),
            strategy_used=LocateStrategy.IMAGE_MATCH,
            position=Point(x=640, y=360),
            confidence=0.93,
            provider="cloud_llm",
            provider_chain=["qwen_local", "cloud_llm"],
            fallback_chain=["cloud_llm"],
            attempted_providers=["qwen_local", "cloud_llm"],
            healed=True,
        )
        return StepResult(
            success=True,
            step_log=ExecutionStepLog(
                id=f"log-{step.id}",
                execution_id=execution_id,
                step_id=step.id,
                step_type=step.type.value,
                step_index=step_index,
                status=StepStatus.SUCCESS,
                metadata={
                    "timing": {
                        "locate_ms": 12.5,
                        "verify_ms": 3.2,
                        "locate_calls": 1,
                        "verify_calls": 1,
                    }
                },
            ),
            located_element=located,
        )


class RetryErrorHandler:
    def __init__(self):
        self.checkpoint_manager = None

    async def handle_error(self, step, error, context):
        return ErrorDecision(
            action=ErrorAction.RETRY,
            message="Retry once before healing",
        )


class HealingRecovery:
    def __init__(self):
        self.outcomes = []

    def analyze_and_heal(self, error, context=None):
        return HealingAction(
            level=HealingLevel.L2_PATH_REPLACE,
            action_type="wait_for_window",
            parameters={"timeout_ms": 100, "poll_interval_ms": 10},
            estimated_success_rate=0.8,
            description="等待目标窗口恢复并重新激活",
        )

    def record_outcome(self, step_id, error, action, success, duration_ms=0.0):
        self.outcomes.append((step_id, action.action_type, success))


class RecoveryCapableStepExecutor:
    def __init__(self):
        self.safety_controller = DummySafetyController()
        self.calls = 0

    async def execute_step(self, step, variables, execution_id, step_index):
        self.calls += 1
        if self.calls == 1:
            return StepResult(
                success=False,
                step_log=ExecutionStepLog(
                    id="log-recovery-fail",
                    execution_id=execution_id,
                    step_id=step.id,
                    step_type=step.type.value,
                    step_index=step_index,
                    status=StepStatus.FAILED,
                    error_message="执行前安全检查失败: 目标窗口不可用 (企业微信)",
                ),
            )

        return StepResult(
            success=True,
            step_log=ExecutionStepLog(
                id="log-recovery-success",
                execution_id=execution_id,
                step_id=step.id,
                step_type=step.type.value,
                step_index=step_index,
                status=StepStatus.SUCCESS,
            ),
        )

    async def recover_window_context(self, step, variables, timeout_ms=4000, poll_interval_ms=250):
        return True


class VisionAwareAdvisor(DummyAdvisor):
    def __init__(self):
        super().__init__()
        self.received_images = None

    async def advise_after_step(
        self,
        step,
        step_index,
        total_steps,
        success,
        error,
        verification_result,
        visual_comparison,
        screenshot_before=None,
        screenshot_after=None,
    ):
        self.received_images = (screenshot_before, screenshot_after)
        return await super().advise_after_step(
            step,
            step_index,
            total_steps,
            success,
            error,
            verification_result,
            visual_comparison,
            screenshot_before=screenshot_before,
            screenshot_after=screenshot_after,
        )


class SlowBeforeAdvisor(DummyAdvisor):
    async def advise_before_step(self, step, step_index, total_steps):
        await asyncio.sleep(0.05)
        return await super().advise_before_step(step, step_index, total_steps)


@pytest.mark.asyncio
async def test_execution_engine_emits_ai_feedback_and_summary():
    flow = AutomationFlow(
        id="flow-ai",
        name="AI Assisted Flow",
        steps=[
            AutomationStep(
                id="step-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(),
                description="Click target",
            )
        ],
    )
    feedback_events = []
    engine = ExecutionEngine(
        flow=flow,
        step_executor=SuccessfulStepExecutor(),
        error_handler=DummyErrorHandler(),
        execution_id="exec-ai",
        advisor=DummyAdvisor(),
    )
    engine.add_feedback_callback(lambda feedback: feedback_events.append(feedback))

    record = await engine.execute()

    assert record.ai_summary is not None
    assert record.ai_summary.optimization_items == ["给切换窗口步骤增加前置检查"]
    assert record.ai_step_insights is not None
    assert "1" in record.ai_step_insights
    assert any(event.ai_check for event in feedback_events)
    assert any(event.step_id == "step-1" for event in feedback_events)
    snapshot = engine._build_record()
    assert snapshot.ai_step_insights is not None
    assert "1" in snapshot.ai_step_insights


@pytest.mark.asyncio
async def test_execution_engine_emits_locator_telemetry_in_logs_and_feedback():
    flow = AutomationFlow(
        id="flow-locator-telemetry",
        name="Locator Telemetry",
        steps=[
            AutomationStep(
                id="step-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(strategy=LocateStrategy.IMAGE_MATCH, image_path="template.png"),
                description="Click target",
            )
        ],
    )
    feedback_events = []
    engine = ExecutionEngine(
        flow=flow,
        step_executor=TelemetryStepExecutor(),
        error_handler=DummyErrorHandler(),
        execution_id="exec-locator-telemetry",
    )
    engine.add_feedback_callback(lambda feedback: feedback_events.append(feedback))

    record = await engine.execute()

    assert record.step_logs[0].metadata["locator"]["provider"] == "cloud_llm"
    success_feedback = next(
        event for event in feedback_events if event.step_id == "step-1" and event.step_status == StepStatus.SUCCESS
    )
    assert success_feedback.locator is not None
    assert success_feedback.locator["attempted_providers"] == ["qwen_local", "cloud_llm"]
    assert success_feedback.locator["healed"] is True
    assert success_feedback.timing is not None
    assert success_feedback.timing["locate_calls"] == 1
    assert success_feedback.timing["verify_ms"] == 3.2


@pytest.mark.asyncio
async def test_execution_engine_passes_base64_screenshots_to_advisor():
    flow = AutomationFlow(
        id="flow-vision",
        name="Vision Flow",
        steps=[
            AutomationStep(
                id="step-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(),
                description="Click target",
            )
        ],
    )
    advisor = VisionAwareAdvisor()
    engine = ExecutionEngine(
        flow=flow,
        step_executor=SuccessfulStepExecutor(),
        error_handler=DummyErrorHandler(),
        execution_id="exec-vision",
        advisor=advisor,
    )

    record = await engine.execute()

    assert advisor.received_images == (
        base64.b64encode(b"before-image").decode("utf-8"),
        base64.b64encode(b"after-image").decode("utf-8"),
    )
    assert record.step_logs[0].screenshot_before == advisor.received_images[0]
    assert record.step_logs[0].screenshot_after == advisor.received_images[1]


@pytest.mark.asyncio
async def test_execution_engine_waits_for_window_and_reports_healing_success():
    flow = AutomationFlow(
        id="flow-healing",
        name="Healing Flow",
        steps=[
            AutomationStep(
                id="step-window",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(window_title="企业微信"),
                retry_count=0,
                description="Click in WeCom",
            )
        ],
    )
    feedback_events = []
    recovery = HealingRecovery()
    engine = ExecutionEngine(
        flow=flow,
        step_executor=RecoveryCapableStepExecutor(),
        error_handler=RetryErrorHandler(),
        execution_id="exec-healing",
        recovery=recovery,
        advisor=DummyAdvisor(),
    )
    engine.add_feedback_callback(lambda feedback: feedback_events.append(feedback))

    record = await engine.execute()

    assert record.status.value == "completed"
    assert engine.ai_healing_stats["healing_applied"] == 1
    assert engine.ai_healing_stats["healing_attempts"] == 1
    assert engine.ai_healing_stats["healing_failures"] == 0
    assert recovery.outcomes == [("step-window", "wait_for_window", True)]
    assert any(
        (event.ai_assist or {}).get("title") == "AI 自愈成功"
        for event in feedback_events
    )
    assert record.ai_summary is not None
    assert any("AI 已成功自愈 1 次" in warning for warning in record.ai_summary.warnings)


@pytest.mark.asyncio
async def test_execution_engine_emits_step_running_feedback_before_slow_ai_check():
    flow = AutomationFlow(
        id="flow-slow-pre",
        name="Slow Precheck Flow",
        steps=[
            AutomationStep(
                id="step-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(),
                description="Click target",
            )
        ],
    )
    feedback_events = []
    engine = ExecutionEngine(
        flow=flow,
        step_executor=SuccessfulStepExecutor(),
        error_handler=DummyErrorHandler(),
        execution_id="exec-slow-pre",
        advisor=SlowBeforeAdvisor(),
    )
    engine.add_feedback_callback(lambda feedback: feedback_events.append(feedback))

    await engine.execute()

    step_running_events = [event for event in feedback_events if event.step_id == "step-1" and event.step_status == StepStatus.RUNNING]
    assert step_running_events
    assert step_running_events[0].ai_check is None
    assert any(event.ai_check for event in step_running_events[1:])
