"""Closed-loop smoke test for the Auto Agent Workflow pipeline.

This script exercises the full record → analyze → generate → assess → dry-run
loop with a fully in-memory operation log.  It is the canonical proof that:

* the data-model layer (``src/models``) is intact;
* the analyzer can turn raw operations into an automation flow;
* the closure assessor correctly refuses fragile (coordinate-only) flows;
* a reinforced flow can be marked closed-loop ready and dry-run end-to-end.

Run it any time you want to verify the pipeline:

    .venv/bin/python scripts/closure_smoke.py

It exits 0 on success and non-zero on the first failed assertion, which makes
it suitable as a CI smoke check as well.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# Ensure ``src`` is importable when the script is run directly as a CLI.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.analyzer.checkpoint_synthesizer import CheckpointSynthesizer
from src.analyzer.closure_assessor import FlowClosureAssessor
from src.analyzer.preprocessor import NormalizedOperation, OperationPreprocessor
from src.analyzer.script_generator import ScriptGenerator
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    ErrorAction,
    ErrorHandlingPolicy,
    LocateStrategy,
    StepCondition,
    StepTarget,
    StepType,
)
from src.models.desktop import UIElement, WindowInfo
from src.models.execution import (
    ExecutionRecord,
    ExecutionStatus,
    ExecutionStepLog,
    StepStatus,
)
from src.models.operation import (
    KeyAction,
    KeyboardEventData,
    MouseAction,
    MouseButton,
    MouseEventData,
    OperationContext,
    OperationEvent,
    OperationType,
)


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def _ok(message: str) -> None:
    print(f"  ok  {message}")


def _section(title: str) -> None:
    print(f"\n== {title} ==")


def _build_raw_session() -> list[OperationEvent]:
    """Build a representative recording: click + type + click."""
    ts = int(time.time() * 1000)
    window = WindowInfo(title="编辑订单 - 企业管理系统", app_name="企业管理系统")
    context_click = OperationContext(
        active_window=window,
        focused_element=UIElement(
            role="button",
            title="保存订单",
            identifier="save-order-btn",
            class_name="primary-button",
            selector="#save-order-btn",
        ),
        platform="desktop",
    )
    context_type = OperationContext(
        active_window=window,
        focused_element=UIElement(
            role="textfield",
            title="备注",
            identifier="remark-input",
            class_name="form-input",
            selector="#remark-input",
        ),
        platform="desktop",
    )

    return [
        OperationEvent(
            id="op-1",
            session_id="session-smoke",
            seq_num=1,
            timestamp=ts,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=420, y=210, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=context_click,
        ),
        OperationEvent(
            id="op-2",
            session_id="session-smoke",
            seq_num=2,
            timestamp=ts + 500,
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(key="text_input", action=KeyAction.INPUT, text="紧急订单"),
            context=context_type,
        ),
        OperationEvent(
            id="op-3",
            session_id="session-smoke",
            seq_num=3,
            timestamp=ts + 1200,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=420, y=210, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=context_click,
        ),
    ]


def _normalize_via_pipeline(events: list[OperationEvent]) -> list[NormalizedOperation]:
    return OperationPreprocessor().preprocess(events)


def _generate_flow(normalized: list[NormalizedOperation]) -> AutomationFlow:
    generator = ScriptGenerator()
    flow = generator.generate_direct(normalized)
    if flow is None:
        _fail("ScriptGenerator.generate_direct returned None for a normal recording")
    return flow


def _assess_and_synthesize(flow: AutomationFlow) -> dict:
    assessor = FlowClosureAssessor()
    synth = CheckpointSynthesizer()
    synth.apply(flow)
    return assessor.assess(flow).model_dump(mode="json")


def _build_fragile_flow() -> AutomationFlow:
    """Hand-build a fragile coordinate-only flow to exercise the refusal path."""
    fragile_step = AutomationStep(
        id="fragile-click",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.POSITION, position={"x": 500, "y": 320}),
        description="Pure pixel click (should be refused)",
    )
    return AutomationFlow(
        id="fragile-flow",
        name="Fragile Flow",
        description="coordinate-only flow to verify the closed-loop refusal path",
        steps=[fragile_step],
        confidence=0.4,
    )


def _build_reinforced_flow() -> AutomationFlow:
    """Build a reinforced flow that should pass the closure assessor."""
    reinforced_step = AutomationStep(
        id="reinforced-click",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(
            strategy=LocateStrategy.CSS_SELECTOR,
            selector="#save-order-btn",
            accessibility_id="save-order-btn",
            title="保存订单",
            role="button",
            class_name="primary-button",
            window_title="编辑订单 - 企业管理系统",
            url="https://erp.example/orders/1",
        ),
        preconditions=[
            StepCondition(field="window_exists", operator="truthy", value=True, timeout_ms=5000),
        ],
        metadata={
            "postconditions": [
                {"field": "page_stable", "operator": "truthy", "value": True, "timeout_ms": 3000, "critical": False},
            ]
        },
        description="保存订单",
    )
    return AutomationFlow(
        id="reinforced-flow",
        name="Reinforced Flow",
        description="reinforced flow that meets the closed-loop contract",
        steps=[reinforced_step],
        error_handling=ErrorHandlingPolicy(),
        confidence=0.95,
    )


async def _dry_run_with_stub(flow: AutomationFlow) -> ExecutionRecord:
    """Dry-run a flow through a stub locator/adapter pair.

    Avoids importing the real platform adapter (which depends on pyautogui /
    the display) so the smoke test can run in headless CI environments.
    """

    class _StubAdapter:
        def get_platform_name(self):
            return "web"

        async def get_screen_size(self):
            return (1920, 1080)

        async def get_logical_screen_size(self):
            return (1920, 1080)

        async def find_element(self, criteria):
            from src.models.desktop import Rect, UIElement
            return UIElement(
                role=getattr(criteria, "role", None) or "button",
                title=getattr(criteria, "title", None) or "保存订单",
                identifier=getattr(criteria, "accessibility_id", None) or "save-order-btn",
                selector=getattr(criteria, "selector", None) or "#save-order-btn",
                xpath=getattr(criteria, "xpath", None),
                class_name=getattr(criteria, "class_name", None) or "primary-button",
                bounds=Rect(x=100, y=200, width=80, height=30),
                is_enabled=True,
                is_focused=True,
            )

        async def capture_screen(self):
            return b""

        async def get_active_window(self):
            return SimpleNamespace(title="编辑订单 - 企业管理系统", app_name="企业管理系统")

        async def get_windows(self):
            return [SimpleNamespace(title="编辑订单 - 企业管理系统", app_name="企业管理系统", url=None)]

        async def click_target(self, target, action):  # pragma: no cover - stub
            return True

    from src.executor.element_locator import ElementLocator
    from src.executor.engine import ExecutionEngine
    from src.executor.error_handler import ErrorHandler
    from src.executor.step_executor import StepExecutor

    adapter = _StubAdapter()
    locator = ElementLocator(adapter)
    step_executor = StepExecutor(locator, adapter, enable_visual_verify=False)
    error_handler = ErrorHandler(max_retries=1, base_delay_ms=10)
    engine = ExecutionEngine(flow=flow, step_executor=step_executor, error_handler=error_handler)
    return await engine.dry_run({})


async def _run() -> int:
    _section("Stage 1 — Recording capture")
    events = _build_raw_session()
    if len(events) != 3:
        _fail(f"expected 3 recorded events, got {len(events)}")
    _ok(f"captured {len(events)} operation events")

    _section("Stage 2 — Preprocess and normalize")
    normalized = _normalize_via_pipeline(events)
    if not normalized:
        _fail("preprocessor returned no normalized operations")
    op_types = {n.op_type for n in normalized}
    if "mouse_click" not in op_types:
        _fail(f"expected at least one click after normalization, got {op_types}")
    _ok(f"normalized into {len(normalized)} operations ({sorted(op_types)})")

    _section("Stage 3 — Flow generation")
    flow = _generate_flow(normalized)
    if not flow.steps:
        _fail("generated flow has no steps")
    fragile_steps = [s for s in flow.steps if s.target and s.target.strategy == LocateStrategy.POSITION]
    if fragile_steps:
        _ok(
            f"generator emitted {len(fragile_steps)} POSITION step(s); each is flagged as "
            "requires-confirmation"
        )
        for step in fragile_steps:
            if not step.locator_requires_confirmation():
                _fail(f"POSITION step {step.id} was not flagged as requires-confirmation")
    else:
        _ok("generator emitted no POSITION-only steps")

    _section("Stage 4 — Closure assessment refuses fragile flow")
    fragile_assessment = _assess_and_synthesize(_build_fragile_flow())
    if fragile_assessment.get("ready"):
        _fail("fragile coordinate-only flow was marked closed-loop ready (regression!)")
    _ok(f"fragile flow rejected: status={fragile_assessment['status']!r}")
    fragile_codes = {issue["code"] for issue in fragile_assessment["issues"]}
    if "position_only_target" not in fragile_codes:
        _fail(f"expected 'position_only_target' in fragile issues, got {fragile_codes}")
    _ok("fragile issues include 'position_only_target'")

    _section("Stage 5 — Reinforced flow is closed-loop ready")
    reinforced_flow = _build_reinforced_flow()
    reinforced_assessment = _assess_and_synthesize(reinforced_flow)
    if not reinforced_assessment["ready"]:
        _fail(
            "reinforced flow was not marked closed-loop ready: "
            f"status={reinforced_assessment['status']}, issues={reinforced_assessment['issues']}"
        )
    _ok(f"reinforced flow accepted: score={reinforced_assessment['score']}")

    _section("Stage 6 — Dry-run executes end-to-end")
    record = await _dry_run_with_stub(reinforced_flow)
    if record.status != ExecutionStatus.COMPLETED:
        _fail(f"dry-run did not complete: status={record.status}, error={record.error_summary}")
    if record.failed_steps:
        _fail(f"dry-run reported {record.failed_steps} failed step(s)")
    _ok(f"dry-run completed: {record.completed_steps} step(s), no failures")

    _section("Stage 7 — Execution step log is structurally complete")
    if not record.step_logs:
        _fail("dry-run produced no step logs")
    for log in record.step_logs:
        if not isinstance(log, ExecutionStepLog):
            _fail(f"step log is not an ExecutionStepLog: {type(log)}")
        if log.status not in {StepStatus.SUCCESS, StepStatus.SKIPPED}:
            _fail(f"unexpected step status {log.status.value} for step {log.step_id}")
    _ok(f"all {len(record.step_logs)} step log(s) are well-formed")

    print("\nALL STAGES PASSED — record→analyze→generate→assess→dry-run loop is closed.")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - top-level safety net
        print(f"FAIL: smoke test crashed with unexpected exception: {exc!r}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
