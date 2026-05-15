import pytest

import src.executor.execution_advisor as execution_advisor_module
from src.executor.execution_advisor import ExecutionAdvisor
from src.models.automation import AutomationFlow, AutomationStep, LocateStrategy, StepTarget, StepType
from src.models.execution import (
    ExecutionAIOptions,
    ExecutionRecord,
    ExecutionStatus,
    ExecutionStepLog,
    StepStatus,
)


@pytest.mark.asyncio
async def test_execution_advisor_precheck_flags_position_and_missing_verification():
    advisor = ExecutionAdvisor(ExecutionAIOptions(enabled=True, mode="smart"))
    step = AutomationStep(
        id="step-risky",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.POSITION, position={"x": 10, "y": 20}),
        verification_enabled=False,
        description="Risky click",
    )

    advice = await advisor.advise_before_step(step, 0, 1)

    assert advice is not None
    assert advice.level == "warning"
    assert any("坐标定位" in item for item in advice.warnings)
    assert any("执行验证" in item for item in advice.warnings)


@pytest.mark.asyncio
async def test_execution_advisor_builds_rule_summary():
    advisor = ExecutionAdvisor(ExecutionAIOptions(enabled=True, mode="smart"))
    flow = AutomationFlow(
        id="flow-risky",
        name="Risky Flow",
        steps=[
            AutomationStep(
                id="step-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(strategy=LocateStrategy.POSITION, position={"x": 10, "y": 20}),
                verification_enabled=False,
            )
        ],
    )
    record = ExecutionRecord(
        id="exec-1",
        automation_id=flow.id,
        status=ExecutionStatus.FAILED,
        total_steps=1,
        completed_steps=0,
        failed_steps=1,
        error_summary="Execution failed: 1 step failed",
        step_logs=[
            ExecutionStepLog(
                id="log-1",
                execution_id="exec-1",
                step_id="step-1",
                step_type="click",
                step_index=0,
                status=StepStatus.FAILED,
                error_message="元素未找到",
            )
        ],
    )

    summary = await advisor.build_summary(flow, record)

    assert summary is not None
    assert any("失败步骤" in item or "失败" in item for item in summary.warnings)
    assert any("坐标定位" in item for item in summary.optimization_items)
    assert summary.fragile_steps


@pytest.mark.asyncio
async def test_execution_advisor_uses_images_for_failed_postcheck(monkeypatch):
    monkeypatch.setattr(execution_advisor_module.settings, "LOCAL_LLM_ENABLED", True)
    monkeypatch.setattr(execution_advisor_module.settings, "VISION_ENABLED", True)

    advisor = ExecutionAdvisor(ExecutionAIOptions(enabled=True, mode="smart"))
    step = AutomationStep(
        id="step-vision",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.POSITION, position={"x": 10, "y": 20}),
        description="Vision step",
    )
    calls = {}

    async def fake_run_with_images(system_prompt, payload, image_base64s, timeout_s=None):
        calls["image_base64s"] = image_base64s
        calls["payload"] = payload
        return {
            "title": "视觉复盘",
            "summary": "从截图看，按钮状态未变化。",
            "level": "warning",
            "warnings": ["界面变化不足"],
            "suggestions": ["检查目标元素是否被遮挡"],
        }

    monkeypatch.setattr(advisor, "_run_local_ai_with_images", fake_run_with_images)

    advice = await advisor.advise_after_step(
        step=step,
        step_index=0,
        total_steps=1,
        success=False,
        error="点击后无响应",
        verification_result=None,
        visual_comparison={"is_significant": False},
        screenshot_before="before-b64",
        screenshot_after="after-b64",
    )

    assert advice is not None
    assert advice.local_ai_used is True
    assert advice.title == "视觉复盘"
    assert calls["image_base64s"] == ["before-b64", "after-b64"]
    assert calls["payload"]["visual_inputs"]["image_count"] == 2


@pytest.mark.asyncio
async def test_execution_advisor_precheck_falls_back_when_local_ai_times_out(monkeypatch):
    monkeypatch.setattr(execution_advisor_module.settings, "LOCAL_LLM_ENABLED", True)
    monkeypatch.setitem(execution_advisor_module.LOCAL_AI_TIMEOUT_S, "pre", 0.01)

    class SlowEngine:
        async def generate(self, messages, temperature=None, max_tokens=None):
            await execution_advisor_module.asyncio.sleep(0.05)
            return type("Result", (), {"text": '{"title":"slow"}'})()

    async def fake_get_instance():
        return SlowEngine()

    monkeypatch.setattr(execution_advisor_module.LocalLLMEngine, "get_instance", staticmethod(fake_get_instance))

    advisor = ExecutionAdvisor(ExecutionAIOptions(enabled=True, mode="always"))
    step = AutomationStep(
        id="step-timeout",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.POSITION, position={"x": 10, "y": 20}),
        description="Slow precheck",
    )

    advice = await advisor.advise_before_step(step, 0, 1)

    assert advice is not None
    assert advice.local_ai_used is False
    assert "坐标定位" in " ".join(advice.warnings)
