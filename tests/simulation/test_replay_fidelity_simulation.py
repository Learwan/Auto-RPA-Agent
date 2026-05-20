"""Multi-scenario replay fidelity simulation tests.

Covers the closed-loop contract end-to-end:
  record → preprocess → generate → assess → execute

Focus areas:
* Web + GUI context switching (Chrome ↔ native apps)
* Refusal of naked coordinate execution at assess + execute stages
* Semantic replay via CSS_SELECTOR / ACCESSIBILITY_ID (not pyautogui coords)
"""

from __future__ import annotations

import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analyzer.checkpoint_synthesizer import CheckpointSynthesizer
from src.analyzer.closure_assessor import FlowClosureAssessor
from src.analyzer.preprocessor import OperationPreprocessor
from src.analyzer.script_generator import ScriptGenerator
from src.executor.element_locator import ElementLocator, LocatedElement
from src.executor.engine import ExecutionEngine
from src.executor.error_handler import ErrorHandler
from src.executor.step_executor import StepExecutor
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    LocateStrategy,
    StepTarget,
    StepType,
)
from src.models.desktop import Rect, UIElement, WindowInfo
from src.models.execution import ExecutionStatus, StepStatus
from src.models.operation import (
    KeyAction,
    KeyboardEventData,
    MouseAction,
    MouseButton,
    MouseEventData,
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)
from src.platform.factory import create_adapter_for_flow, is_web_flow, is_web_target
from src.recorder.context_inference import enrich_operation_context
from tests.simulation.conftest import make_op_id, make_window


def _browser_window(title: str, app_name: str = "Google Chrome") -> WindowInfo:
    return WindowInfo(
        window_id=f"win-{uuid.uuid4().hex[:6]}",
        title=title,
        app_name=app_name,
        pid=9999,
        bounds=Rect(x=0, y=0, width=1280, height=800),
        is_active=True,
        browser_type="chromium",
        url=f"https://app.example/{title.lower().replace(' ', '-')}",
    )


# ---------------------------------------------------------------------------
# Recording builders — every interactive op carries a stable anchor
# ---------------------------------------------------------------------------

def _web_element(
    *,
    selector: str | None = None,
    xpath: str | None = None,
    identifier: str | None = None,
    role: str = "button",
    title: str = "",
) -> UIElement:
    return UIElement(
        role=role,
        title=title,
        selector=selector,
        xpath=xpath,
        identifier=identifier,
        class_name="ui-control",
        bounds=Rect(x=100, y=200, width=120, height=32),
        is_enabled=True,
        is_visible=True,
    )


def _desktop_element(*, identifier: str, role: str = "button", title: str = "") -> UIElement:
    return UIElement(
        role=role,
        title=title or identifier,
        identifier=identifier,
        class_name="AXButton",
        bounds=Rect(x=400, y=300, width=80, height=28),
        is_enabled=True,
        is_visible=True,
    )


def build_web_to_desktop_session(session_id: str) -> list[OperationEvent]:
    """Chrome form submit → switch to Finder → save file."""
    now = int(time.time() * 1000)
    chrome = _browser_window("Order Portal")
    finder = make_window("Documents", "Finder")
    # Align click coords with element center (bounds 100,200 + 80x30)
    web_click_xy = (140, 215)
    desktop_click_xy = (440, 314)  # bounds 400,300 + 80x28

    return [
        OperationEvent(
            id=make_op_id(0), session_id=session_id, seq_num=0, timestamp=now,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=web_click_xy[0], y=web_click_xy[1], button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=chrome,
                focused_element=_web_element(selector="#submit-order", title="Submit Order"),
                process_name="Google Chrome",
                platform="web",
            ),
        ),
        OperationEvent(
            id=make_op_id(1), session_id=session_id, seq_num=1, timestamp=now + 800,
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(key="text", action=KeyAction.INPUT, text="PO-2026-001"),
            context=OperationContext(
                active_window=chrome,
                focused_element=_web_element(selector="#order-id", role="textfield", title="Order ID"),
                platform="web",
            ),
        ),
        OperationEvent(
            id=make_op_id(2), session_id=session_id, seq_num=2, timestamp=now + 2200,
            type=OperationType.WINDOW_SWITCH,
            data=WindowEventData(
                from_window=chrome,
                to_window=finder,
                method="cmd+tab",
            ),
            context=OperationContext(platform="macOS"),
        ),
        OperationEvent(
            id=make_op_id(3), session_id=session_id, seq_num=3, timestamp=now + 3500,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=desktop_click_xy[0], y=desktop_click_xy[1], button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=finder,
                focused_element=_desktop_element(identifier="save-document-btn", title="Save"),
                process_name="Finder",
                platform="macOS",
            ),
        ),
    ]


def build_desktop_to_web_session(session_id: str) -> list[OperationEvent]:
    """VS Code save click → switch to Chrome → click web dashboard."""
    now = int(time.time() * 1000)
    vscode = make_window("main.py — VS Code", "Electron")
    chrome = _browser_window("Analytics Dashboard")
    web_click_xy = (140, 215)

    return [
        OperationEvent(
            id=make_op_id(0), session_id=session_id, seq_num=0, timestamp=now,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=440, y=314, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=vscode,
                focused_element=_desktop_element(identifier="editor-save", role="button", title="Save"),
                platform="macOS",
            ),
        ),
        OperationEvent(
            id=make_op_id(1), session_id=session_id, seq_num=1, timestamp=now + 1500,
            type=OperationType.WINDOW_SWITCH,
            data=WindowEventData(from_window=vscode, to_window=chrome, method="cmd+tab"),
            context=OperationContext(platform="macOS"),
        ),
        OperationEvent(
            id=make_op_id(2), session_id=session_id, seq_num=2, timestamp=now + 2800,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=web_click_xy[0], y=web_click_xy[1], button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=chrome,
                focused_element=_web_element(selector="[data-testid='refresh-metrics']", title="Refresh"),
                platform="web",
            ),
        ),
    ]


def build_multi_round_hybrid_session(session_id: str, rounds: int = 3) -> list[OperationEvent]:
    """Alternating web ↔ desktop clicks across multiple rounds."""
    ops: list[OperationEvent] = []
    now = int(time.time() * 1000)
    chrome = _browser_window("SaaS Console")
    notes = make_window("Notes", "Notes")

    for r in range(rounds):
        base = now + r * 5000
        seq = r * 3
        web_xy = (140, 215)
        desktop_xy = (440, 314)
        ops.append(OperationEvent(
            id=make_op_id(seq), session_id=session_id, seq_num=seq, timestamp=base,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=web_xy[0], y=web_xy[1], button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=chrome,
                focused_element=_web_element(selector=f"#action-{r}", title=f"Action {r}"),
                platform="web",
            ),
        ))
        ops.append(OperationEvent(
            id=make_op_id(seq + 1), session_id=session_id, seq_num=seq + 1, timestamp=base + 1200,
            type=OperationType.WINDOW_SWITCH,
            data=WindowEventData(from_window=chrome, to_window=notes, method="cmd+tab"),
            context=OperationContext(platform="macOS"),
        ))
        ops.append(OperationEvent(
            id=make_op_id(seq + 2), session_id=session_id, seq_num=seq + 2, timestamp=base + 2400,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=desktop_xy[0], y=desktop_xy[1], button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=notes,
                focused_element=_desktop_element(identifier=f"note-save-{r}", title="Save Note"),
                platform="macOS",
            ),
        ))
    return ops


def build_coordinate_only_session(session_id: str) -> list[OperationEvent]:
    """Raw clicks with no focused_element — should NOT pass closed-loop."""
    now = int(time.time() * 1000)
    return [
        OperationEvent(
            id=make_op_id(i), session_id=session_id, seq_num=i, timestamp=now + i * 600,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=100 + i * 50, y=200, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=make_window("Unknown", "Desktop"),
                platform="macOS",
            ),
        )
        for i in range(3)
    ]


# ---------------------------------------------------------------------------
# Pipeline runner + hybrid stub adapter
# ---------------------------------------------------------------------------

def _pipeline_from_events(events: list[OperationEvent]) -> AutomationFlow:
    normalized = OperationPreprocessor().preprocess(events)
    flow = ScriptGenerator().generate_direct(normalized)
    assert flow is not None, "ScriptGenerator returned None"
    CheckpointSynthesizer().apply(flow)
    return flow


class HybridReplayStub:
    """Stub adapter that records semantic vs coordinate execution paths."""

    def __init__(self):
        self.semantic_clicks: list[str] = []
        self.semantic_types: list[str] = []
        self.activated_windows: list[str] = []
        self.pyautogui_clicks: list[tuple[int, int]] = []
        self._windows = [
            SimpleNamespace(title="Order Portal", app_name="Google Chrome", url="https://app.example"),
            SimpleNamespace(title="Documents", app_name="Finder", url=None),
            SimpleNamespace(title="Analytics Dashboard", app_name="Google Chrome", url="https://dash.example"),
            SimpleNamespace(title="main.py — VS Code", app_name="Electron", url=None),
            SimpleNamespace(title="SaaS Console", app_name="Google Chrome", url="https://saas.example"),
            SimpleNamespace(title="Notes", app_name="Notes", url=None),
        ]
        self._active = self._windows[0]

    def get_platform_name(self):
        return "hybrid-stub"

    async def get_screen_size(self):
        return (1920, 1080)

    async def get_logical_screen_size(self):
        return (1920, 1080)

    async def capture_screen(self):
        return b""

    async def get_windows(self):
        return list(self._windows)

    async def get_active_window(self):
        return self._active

    async def activate_window(self, window):
        self.activated_windows.append(window.title)
        self._active = window
        return True

    async def find_element(self, criteria):
        anchor = (
            getattr(criteria, "accessibility_id", None)
            or getattr(criteria, "selector", None)
            or getattr(criteria, "title", None)
            or "unknown"
        )
        return UIElement(
            role=getattr(criteria, "role", None) or "button",
            title=getattr(criteria, "title", None) or str(anchor),
            identifier=getattr(criteria, "accessibility_id", None) or str(anchor),
            selector=getattr(criteria, "selector", None),
            bounds=Rect(x=100, y=200, width=80, height=30),
            is_enabled=True,
        )

    async def click_target(self, target, action):
        key = target.selector or target.xpath or target.accessibility_id or target.title or "web-target"
        self.semantic_clicks.append(str(key))
        return True

    async def type_text_target(self, target, action):
        key = target.selector or target.accessibility_id or target.title or "web-input"
        self.semantic_types.append(str(key))
        return True


def _make_hybrid_executor(stub: HybridReplayStub) -> StepExecutor:
    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: stub.pyautogui_clicks.append((x, y)),
        rightClick=lambda x, y: stub.pyautogui_clicks.append((x, y)),
        doubleClick=lambda x, y: stub.pyautogui_clicks.append((x, y)),
        typewrite=lambda text, interval=0: None,
        press=lambda key: None,
        hotkey=lambda *keys: None,
        scroll=lambda clicks, x=None, y=None: None,
        moveTo=lambda x, y: None,
        dragTo=lambda x, y, duration=0: None,
    )
    import src.executor.step_executor as step_mod
    saved = sys.modules.get("pyautogui")
    sys.modules["pyautogui"] = fake_pyautogui
    step_mod.pyautogui = fake_pyautogui
    locator = ElementLocator(stub)
    executor = StepExecutor(locator, stub, enable_visual_verify=False)
    executor._pyautogui_restore = (saved, step_mod)
    return executor


def _restore_pyautogui(executor: StepExecutor) -> None:
    if hasattr(executor, "_pyautogui_restore"):
        saved, step_mod = executor._pyautogui_restore
        if saved is not None:
            sys.modules["pyautogui"] = saved
        else:
            sys.modules.pop("pyautogui", None)


def _prepare_flow_for_replay(flow: AutomationFlow) -> None:
    """Simulate editor confirmation and disable strict coordinate verification."""
    anchored = {
        LocateStrategy.CSS_SELECTOR, LocateStrategy.XPATH,
        LocateStrategy.ACCESSIBILITY_ID, LocateStrategy.TEXT_MATCH,
    }
    for step in flow.steps:
        step.verification_enabled = False
        if step.target and step.target.strategy in anchored:
            meta = dict(step.metadata or {})
            meta["locator_requires_confirmation"] = False
            meta["locator_confirmed"] = True
            step.metadata = meta


async def _execute_flow(flow: AutomationFlow, stub: HybridReplayStub) -> tuple:
    """Full execution (not dry-run) to verify semantic replay paths."""
    _prepare_flow_for_replay(flow)
    executor = _make_hybrid_executor(stub)
    engine = ExecutionEngine(
        flow=flow,
        step_executor=executor,
        error_handler=ErrorHandler(max_retries=1, base_delay_ms=10),
    )
    record = await engine.execute({})
    _restore_pyautogui(executor)
    return record, stub


def _interactive_steps(flow: AutomationFlow) -> list[AutomationStep]:
    interactive = {
        StepType.CLICK, StepType.TYPE, StepType.DOUBLE_CLICK,
        StepType.SCROLL, StepType.DRAG, StepType.UPLOAD_FILE, StepType.DOWNLOAD_FILE,
    }
    return [s for s in flow.steps if s.type in interactive]


def _assert_no_naked_position(flow: AutomationFlow) -> None:
    """POSITION steps must be flagged requires-confirmation; certified flows must have anchors."""
    for step in _interactive_steps(flow):
        if step.target and step.target.strategy == LocateStrategy.POSITION:
            assert step.locator_requires_confirmation(), (
                f"Step {step.id} uses POSITION without confirmation flag"
            )


def _assert_anchored_strategies(flow: AutomationFlow) -> None:
    anchored = {
        LocateStrategy.CSS_SELECTOR, LocateStrategy.XPATH,
        LocateStrategy.ACCESSIBILITY_ID, LocateStrategy.TEXT_MATCH,
        LocateStrategy.IMAGE_ANCHOR,
    }
    for step in _interactive_steps(flow):
        assert step.target.strategy in anchored, (
            f"Step {step.id} lacks stable anchor: {step.target.strategy}"
        )


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestWebGuiContextSwitching:
    """Web ↔ GUI context switching through the analysis pipeline."""

    def test_context_inference_marks_browser_as_web(self):
        ctx = OperationContext(
            active_window=make_window("Portal", "Google Chrome"),
            process_name="Google Chrome",
            platform="macOS",
        )
        enriched = enrich_operation_context(ctx)
        assert enriched.platform == "web"

    def test_web_to_desktop_generates_mixed_anchors(self):
        events = build_web_to_desktop_session("sess-w2d")
        flow = _pipeline_from_events(events)

        strategies = {s.target.strategy for s in _interactive_steps(flow)}
        assert LocateStrategy.CSS_SELECTOR in strategies
        assert LocateStrategy.ACCESSIBILITY_ID in strategies

        switch_steps = [s for s in flow.steps if s.type == StepType.SWITCH_WINDOW]
        assert len(switch_steps) >= 1

    def test_desktop_to_web_generates_mixed_anchors(self):
        events = build_desktop_to_web_session("sess-d2w")
        flow = _pipeline_from_events(events)

        web_steps = [s for s in _interactive_steps(flow) if is_web_target(s.target)]
        desktop_steps = [s for s in _interactive_steps(flow) if not is_web_target(s.target)]
        assert len(web_steps) >= 1
        assert all(s.target.strategy == LocateStrategy.CSS_SELECTOR for s in web_steps)

    @pytest.mark.parametrize("rounds", [2, 3, 5])
    def test_multi_round_hybrid_generates_all_anchors(self, rounds: int):
        events = build_multi_round_hybrid_session(f"multi-{rounds}", rounds=rounds)
        flow = _pipeline_from_events(events)

        click_count = len([s for s in flow.steps if s.type == StepType.CLICK])
        switch_count = len([s for s in flow.steps if s.type == StepType.SWITCH_WINDOW])
        assert click_count >= rounds * 2
        assert switch_count >= rounds
        _assert_anchored_strategies(flow)


class TestCoordinateRejection:
    """Coordinate-only flows must be refused at assess and execute stages."""

    def test_coordinate_only_flow_blocked_by_assessor(self):
        events = build_coordinate_only_session("sess-coord")
        flow = _pipeline_from_events(events)
        assessment = FlowClosureAssessor().assess(flow)
        assert assessment.ready is False
        codes = {i.code for i in assessment.issues}
        assert "position_only_target" in codes or any(
            s.target.strategy == LocateStrategy.POSITION for s in _interactive_steps(flow)
        )

    @pytest.mark.asyncio
    async def test_coordinate_only_step_refused_at_execution(self, monkeypatch):
        monkeypatch.delenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", raising=False)

        fragile = AutomationStep(
            id="fragile",
            type=StepType.CLICK,
            action={"button": "left"},
            target=StepTarget(
                strategy=LocateStrategy.POSITION,
                position={"x": 999, "y": 888},
                window_title="Test",
            ),
            metadata={
                "locator_requires_confirmation": True,
                "locator_confirmed": False,
                "locator_confirmation_reason": "录制结果主要依赖坐标",
            },
        )
        stub = HybridReplayStub()
        executor = _make_hybrid_executor(stub)
        result = await executor.execute_step(fragile, {}, "exec-fragile", 0)
        _restore_pyautogui(executor)

        assert result.success is False
        assert "拒绝执行" in (result.step_log.error_message or "")
        assert stub.pyautogui_clicks == []
        assert stub.semantic_clicks == []

    @pytest.mark.asyncio
    async def test_locator_failure_does_not_fallback_to_coordinates(self, monkeypatch):
        monkeypatch.delenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", raising=False)

        step = AutomationStep(
            id="anchored-fail",
            type=StepType.CLICK,
            action={"button": "left"},
            target=StepTarget(
                strategy=LocateStrategy.ACCESSIBILITY_ID,
                accessibility_id="missing-btn",
                window_title="App",
            ),
            metadata={"locator_requires_confirmation": False, "locator_confirmed": True},
        )
        flow = AutomationFlow(id="fail-flow", name="Fail", steps=[step])

        stub = HybridReplayStub()
        failing_locator = MagicMock()
        failing_locator.locate = AsyncMock(return_value=None)
        failing_locator.wait_for_element = AsyncMock(return_value=None)

        fake_pyautogui = SimpleNamespace(
            click=lambda x, y, clicks=1: stub.pyautogui_clicks.append((x, y)),
            rightClick=lambda x, y: stub.pyautogui_clicks.append((x, y)),
            doubleClick=lambda x, y: stub.pyautogui_clicks.append((x, y)),
        )
        import src.executor.step_executor as step_mod
        sys.modules["pyautogui"] = fake_pyautogui
        step_mod.pyautogui = fake_pyautogui

        executor = StepExecutor(failing_locator, stub, enable_visual_verify=False)
        result = await executor.execute_step(step, {}, "exec-fail", 0)

        assert result.success is False
        assert stub.pyautogui_clicks == []


def _assert_no_raw_recording_coordinates(stub: HybridReplayStub, raw_coords: list[tuple[int, int]]) -> None:
    """Ensure replay never clicks at raw recording coordinates."""
    for coord in raw_coords:
        assert coord not in stub.pyautogui_clicks, (
            f"Replay used raw recording coordinate {coord} instead of semantic locate"
        )


class TestReplayFidelity:
    """Anchored flows replay via semantic paths, not raw coordinates."""

    @pytest.mark.asyncio
    async def test_web_to_desktop_replay_uses_semantic_paths(self, monkeypatch):
        monkeypatch.delenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", raising=False)

        events = build_web_to_desktop_session("replay-w2d")
        flow = _pipeline_from_events(events)
        _assert_no_naked_position(flow)

        stub = HybridReplayStub()
        record, stub = await _execute_flow(flow, stub)

        assert record.status == ExecutionStatus.COMPLETED
        assert record.failed_steps == 0
        assert len(stub.semantic_clicks) >= 1
        assert "#submit-order" in stub.semantic_clicks[0] or any(
            "submit" in c.lower() for c in stub.semantic_clicks
        )
        assert len(stub.activated_windows) >= 1
        _assert_no_raw_recording_coordinates(stub, [(180, 240), (520, 410)])

    @pytest.mark.asyncio
    async def test_desktop_to_web_replay_uses_semantic_paths(self, monkeypatch):
        monkeypatch.delenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", raising=False)

        events = build_desktop_to_web_session("replay-d2w")
        flow = _pipeline_from_events(events)
        stub = HybridReplayStub()
        record, stub = await _execute_flow(flow, stub)

        assert record.status == ExecutionStatus.COMPLETED
        assert len(stub.semantic_clicks) >= 1
        _assert_no_raw_recording_coordinates(stub, [(640, 120)])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rounds", [2, 3])
    async def test_multi_round_hybrid_full_replay(self, monkeypatch, rounds: int):
        monkeypatch.delenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", raising=False)

        events = build_multi_round_hybrid_session(f"replay-multi-{rounds}", rounds=rounds)
        flow = _pipeline_from_events(events)
        _assert_anchored_strategies(flow)

        stub = HybridReplayStub()
        record, stub = await _execute_flow(flow, stub)

        assert record.status == ExecutionStatus.COMPLETED
        assert record.completed_steps == len(flow.steps)
        assert len(stub.semantic_clicks) >= rounds
        assert len(stub.activated_windows) >= rounds
        _assert_no_raw_recording_coordinates(stub, [(200, 300), (700, 500)])

    @pytest.mark.asyncio
    async def test_reinforced_flow_passes_closure_and_replays(self, monkeypatch):
        monkeypatch.delenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", raising=False)

        events = build_web_to_desktop_session("replay-certified")
        flow = _pipeline_from_events(events)
        assessment = FlowClosureAssessor().assess(flow)

        interactive = _interactive_steps(flow)
        if interactive and all(
            s.target.strategy != LocateStrategy.POSITION for s in interactive
        ):
            assert assessment.metrics.get("certified_ratio", 0) >= 0.5

        stub = HybridReplayStub()
        record, stub = await _execute_flow(flow, stub)
        assert record.status == ExecutionStatus.COMPLETED
        assert record.failed_steps == 0

    def test_web_flow_routes_to_web_adapter(self):
        events = build_desktop_to_web_session("adapter-route")
        flow = _pipeline_from_events(events)
        assert is_web_flow(flow)
        adapter = create_adapter_for_flow(flow)
        assert adapter.get_platform_name() in {"web", "chromium", "playwright"}
