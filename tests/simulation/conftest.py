import random
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    ErrorAction,
    ErrorHandlingPolicy,
    FlowVariable,
    LocateStrategy,
    StepTarget,
    StepType,
)
from src.models.desktop import Rect, WindowInfo, UIElement, Point
from src.models.execution import (
    ExecutionRecord,
    ExecutionStatus,
    ExecutionStepLog,
    StepStatus,
    VerificationLevel,
    VerificationResult,
)
from src.models.operation import (
    KeyboardEventData,
    KeyAction,
    MouseAction,
    MouseButton,
    MouseEventData,
    NavigationEventData,
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)
from src.executor.element_locator import LocatedElement


def make_step_id(index: int) -> str:
    return f"step-{uuid.uuid4().hex[:8]}-{index:03d}"


def make_op_id(seq: int) -> str:
    return f"op-{uuid.uuid4().hex[:8]}-{seq:04d}"


def make_execution_id() -> str:
    return f"exec-{uuid.uuid4().hex[:12]}"


def make_window(title: str, app_name: str, pid: int = 9999) -> WindowInfo:
    return WindowInfo(
        window_id=f"win-{uuid.uuid4().hex[:6]}",
        title=title,
        app_name=app_name,
        pid=pid,
        bounds=Rect(x=0, y=0, width=1280, height=800),
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Fixtures: simulated user operation sequences
# ---------------------------------------------------------------------------

@pytest.fixture
def sim_session_id():
    return f"sim-session-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def sim_click_sequence(sim_session_id):
    """Simulate a user clicking three buttons in a form: [Open Dialog] → [Fill Name] → [Submit]"""
    ops = []
    now = int(time.time() * 1000)
    for i in range(3):
        ops.append(OperationEvent(
            id=make_op_id(i),
            session_id=sim_session_id,
            seq_num=i,
            timestamp=now + i * 800,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(
                x=200 + i * 100,
                y=300,
                button=MouseButton.LEFT,
                action=MouseAction.CLICK,
            ),
            context=OperationContext(
                active_window=make_window("Registration Form", "Chrome"),
                screenshot_path=f"/tmp/sim/screenshot_{i}.png",
                process_name="Google Chrome",
                platform="macOS",
            ),
        ))
    return ops


@pytest.fixture
def sim_typing_sequence(sim_session_id):
    """Simulate user typing into a text field."""
    ops = []
    now = int(time.time() * 1000)
    ops.append(OperationEvent(
        id=make_op_id(0),
        session_id=sim_session_id,
        seq_num=0,
        timestamp=now,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=150, y=200, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(
            active_window=make_window("Login", "Finder"),
            screenshot_path="/tmp/sim/login_click.png",
            platform="macOS",
        ),
    ))
    ops.append(OperationEvent(
        id=make_op_id(1),
        session_id=sim_session_id,
        seq_num=1,
        timestamp=now + 500,
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key="a", action=KeyAction.INPUT, text="admin@example.com"),
        context=OperationContext(
            active_window=make_window("Login", "Finder"),
            screenshot_path="/tmp/sim/login_type.png",
            platform="macOS",
        ),
    ))
    ops.append(OperationEvent(
        id=make_op_id(2),
        session_id=sim_session_id,
        seq_num=2,
        timestamp=now + 2000,
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key="Return", action=KeyAction.PRESS),
        context=OperationContext(
            active_window=make_window("Login", "Finder"),
            screenshot_path="/tmp/sim/login_enter.png",
            platform="macOS",
        ),
    ))
    return ops


@pytest.fixture
def sim_hotkey_sequence(sim_session_id):
    """Simulate cmd+C copy and cmd+V paste."""
    ops = []
    now = int(time.time() * 1000)
    ops.append(OperationEvent(
        id=make_op_id(0),
        session_id=sim_session_id,
        seq_num=0,
        timestamp=now,
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key="c", modifiers=["command"], action=KeyAction.HOTKEY),
        context=OperationContext(active_window=make_window("Editor", "VS Code")),
    ))
    ops.append(OperationEvent(
        id=make_op_id(1),
        session_id=sim_session_id,
        seq_num=1,
        timestamp=now + 1500,
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key="v", modifiers=["command"], action=KeyAction.HOTKEY),
        context=OperationContext(active_window=make_window("Editor", "VS Code")),
    ))
    return ops


@pytest.fixture
def sim_window_switch_sequence(sim_session_id):
    """Simulate switching between two apps."""
    ops = []
    now = int(time.time() * 1000)
    ops.append(OperationEvent(
        id=make_op_id(0),
        session_id=sim_session_id,
        seq_num=0,
        timestamp=now,
        type=OperationType.WINDOW_SWITCH,
        data=WindowEventData(
            from_window=make_window("Browser", "Chrome"),
            to_window=make_window("Terminal", "iTerm2"),
            method="cmd+tab",
        ),
        context=OperationContext(platform="macOS"),
    ))
    ops.append(OperationEvent(
        id=make_op_id(1),
        session_id=sim_session_id,
        seq_num=1,
        timestamp=now + 2000,
        type=OperationType.WINDOW_SWITCH,
        data=WindowEventData(
            from_window=make_window("Terminal", "iTerm2"),
            to_window=make_window("Browser", "Chrome"),
            method="cmd+tab",
        ),
        context=OperationContext(platform="macOS"),
    ))
    return ops


@pytest.fixture
def sim_mixed_sequence(sim_session_id):
    """Complete realistic workflow: click → type → hotkey → window switch."""
    ops = []
    now = int(time.time() * 1000)
    ts = [now, now + 600, now + 1200, now + 2500, now + 3500, now + 4200]

    ops.append(OperationEvent(
        id=make_op_id(0), session_id=sim_session_id, seq_num=0, timestamp=ts[0],
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=100, y=50, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(
            active_window=make_window("Chrome", "Google Chrome"),
            screenshot_path="/tmp/sim/mixed_0.png",
        ),
    ))
    ops.append(OperationEvent(
        id=make_op_id(1), session_id=sim_session_id, seq_num=1, timestamp=ts[1],
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key="t", action=KeyAction.INPUT, text="https://github.com"),
        context=OperationContext(active_window=make_window("Chrome", "Google Chrome")),
    ))
    ops.append(OperationEvent(
        id=make_op_id(2), session_id=sim_session_id, seq_num=2, timestamp=ts[2],
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key="Return", action=KeyAction.PRESS),
        context=OperationContext(active_window=make_window("Chrome", "Google Chrome")),
    ))
    ops.append(OperationEvent(
        id=make_op_id(3), session_id=sim_session_id, seq_num=3, timestamp=ts[3],
        type=OperationType.WINDOW_SWITCH,
        data=WindowEventData(
            from_window=make_window("Chrome", "Google Chrome"),
            to_window=make_window("VS Code", "Electron"),
            method="cmd+tab",
        ),
        context=OperationContext(platform="macOS"),
    ))
    ops.append(OperationEvent(
        id=make_op_id(4), session_id=sim_session_id, seq_num=4, timestamp=ts[4],
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key="s", modifiers=["command"], action=KeyAction.HOTKEY),
        context=OperationContext(active_window=make_window("VS Code", "Electron")),
    ))
    ops.append(OperationEvent(
        id=make_op_id(5), session_id=sim_session_id, seq_num=5, timestamp=ts[5],
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=800, y=600, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(active_window=make_window("VS Code", "Electron")),
    ))
    return ops


# ---------------------------------------------------------------------------
# Fixtures: simulated flows
# ---------------------------------------------------------------------------

@pytest.fixture
def sim_linear_flow():
    """A realistic 5-step linear flow."""
    flow_id = f"flow-{uuid.uuid4().hex[:8]}"
    steps = [
        AutomationStep(
            id=make_step_id(0), type=StepType.CLICK, description="点击搜索框",
            target=StepTarget(strategy=LocateStrategy.POSITION),
            action={"button": "left", "clicks": 1},
            execution_order=0,
        ),
        AutomationStep(
            id=make_step_id(1), type=StepType.TYPE, description="输入关键词",
            target=StepTarget(strategy=LocateStrategy.TEXT_MATCH, text_contains="search"),
            action={"text": "test query", "interval": 0.05},
            execution_order=1,
        ),
        AutomationStep(
            id=make_step_id(2), type=StepType.HOTKEY, description="按回车搜索",
            action={"keys": ["Return"]},
            execution_order=2,
        ),
        AutomationStep(
            id=make_step_id(3), type=StepType.WAIT, description="等待结果加载",
            action={"duration": 2.0},
            delay=2000,
            execution_order=3,
        ),
        AutomationStep(
            id=make_step_id(4), type=StepType.CLICK, description="点击第一个结果",
            target=StepTarget(strategy=LocateStrategy.POSITION),
            action={"button": "left", "clicks": 1},
            execution_order=4,
        ),
    ]
    return AutomationFlow(id=flow_id, name="百度搜索", description="搜索并打开第一个结果", steps=steps)


@pytest.fixture
def sim_conditional_flow():
    """A flow with conditional branching."""
    flow_id = f"flow-{uuid.uuid4().hex[:8]}"
    steps = [
        AutomationStep(
            id=make_step_id(0), type=StepType.CONDITION, description="检查是否登录",
            action={"expression": "is_logged_in"},
            execution_order=0,
            on_error=ErrorAction.SKIP,
        ),
        AutomationStep(
            id=make_step_id(1), type=StepType.CLICK, description="点击登录按钮",
            action={"button": "left"}, execution_order=1,
        ),
        AutomationStep(
            id=make_step_id(2), type=StepType.TYPE, description="输入用户名",
            action={"text": "user@test.com"}, execution_order=2,
        ),
        AutomationStep(
            id=make_step_id(3), type=StepType.CLICK, description="进入主页",
            action={"button": "left"}, execution_order=3,
        ),
    ]
    flow = AutomationFlow(id=flow_id, name="条件登录流程", description="判断登录状态后执行对应操作", steps=steps)
    return flow


@pytest.fixture
def sim_large_flow():
    """A 50-step flow for stress testing."""
    flow_id = f"flow-{uuid.uuid4().hex[:8]}"
    steps = []
    for i in range(50):
        stype = StepType.CLICK if i % 2 == 0 else StepType.TYPE
        steps.append(AutomationStep(
            id=make_step_id(i),
            type=stype,
            description=f"步骤 {i}",
            target=StepTarget(strategy=LocateStrategy.POSITION),
            action={"text": f"val_{i}"} if stype == StepType.TYPE else {"button": "left"},
            execution_order=i,
        ))
    return AutomationFlow(
        id=flow_id, name="大流程压力测试", description="50步重复操作",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Fixtures: simulated AI/LLM responses
# ---------------------------------------------------------------------------

@pytest.fixture
def sim_llm_analysis_response():
    return """## Flow Analysis

### Quality Assessment
This flow is well-structured with clear step progression. The operations have high confidence scores and reliable element targets.

### Risks
- Step 3 (wait) may fail if the page loads slower than expected
- Step 4 (click first result) depends on successful navigation

### Recommendations
1. Consider increasing the wait duration to 3 seconds for slow connections
2. Add a fallback locator strategy for step 4 (use xpath or text_match as backup)
"""


@pytest.fixture
def sim_llm_name_response():
    return "百度搜索 - 自动化测试查询流程"


@pytest.fixture
def sim_mock_llm_service(sim_llm_analysis_response, sim_llm_name_response):
    llm = MagicMock()
    llm.is_configured = True
    llm.analyze_flow = AsyncMock(return_value=sim_llm_analysis_response)
    llm.analyze_flow_artifacts = AsyncMock(return_value={
        "suggested_name": sim_llm_name_response,
        "summary": "This workflow automates a search query and result selection.",
        "risks": ["Step 3 (wait) may fail if page loads slowly"],
        "improvements": ["Add fallback locator strategy for step 4"],
        "reliability_assessment": "well-structured with clear step progression",
        "confidence": 0.85,
        "step_analyses": [],
    })
    llm.suggest_flow_name = AsyncMock(return_value=sim_llm_name_response)
    llm.explain_operations = AsyncMock(return_value="This workflow automates a search query and result selection.")
    return llm


# ---------------------------------------------------------------------------
# Fixtures: simulated platform adapter
# ---------------------------------------------------------------------------

@pytest.fixture
def sim_element_locator():
    element = UIElement(
        role="button",
        title="Test Button",
        bounds=Rect(x=490, y=280, width=20, height=30),
        is_enabled=True,
        is_visible=True,
    )
    located = LocatedElement(
        element=element,
        strategy_used=LocateStrategy.POSITION,
        position=Point(x=500, y=295),
        confidence=0.95,
    )
    locator = MagicMock()
    locator.locate = AsyncMock(return_value=located)
    locator.locate_all = AsyncMock(return_value=[])
    return locator


@pytest.fixture
def sim_platform_adapter():
    adapter = MagicMock()
    adapter.get_screen_size.return_value = (1920, 1080)
    adapter.get_logical_screen_size.return_value = (1920, 1080)
    adapter.move.return_value = True
    adapter.click = AsyncMock(return_value=True)
    adapter.type_text = AsyncMock(return_value=True)
    adapter.press_keys = AsyncMock(return_value=True)
    adapter.scroll = AsyncMock(return_value=True)
    adapter.take_screenshot = AsyncMock(return_value="/tmp/sim/exec_screenshot.png")
    adapter.locate = AsyncMock(return_value={"x": 500, "y": 300, "width": 100, "height": 30})
    adapter.analyze_screen = AsyncMock(return_value={
        "ui_state_description": "Login page with username and password fields",
        "actionable_elements": [
            {"label": "Username input", "element_type": "input", "confidence": 0.95, "bounding_box": {"x": 200, "y": 150, "width": 300, "height": 30}},
            {"label": "Login button", "element_type": "button", "confidence": 0.92, "bounding_box": {"x": 250, "y": 250, "width": 100, "height": 40}},
        ],
        "user_intent": "Log into the application",
    })
    return adapter


# ---------------------------------------------------------------------------
# Fixtures: simulated execution records
# ---------------------------------------------------------------------------

@pytest.fixture
def sim_successful_execution():
    eid = make_execution_id()
    now = time.time()
    return ExecutionRecord(
        id=eid,
        automation_id="flow-test-001",
        status=ExecutionStatus.COMPLETED,
        started_at=now - 5.0,
        completed_at=now,
        total_steps=5,
        completed_steps=5,
        failed_steps=0,
        step_logs=[
            ExecutionStepLog(
                id=f"log-{i}", execution_id=eid, step_id=f"step-{i}",
                step_type="click" if i % 2 == 0 else "type", step_index=i,
                status=StepStatus.SUCCESS,
                started_at=now - 5.0 + i,
                completed_at=now - 5.0 + i + 0.8,
            )
            for i in range(5)
        ],
    )


@pytest.fixture
def sim_failed_execution():
    eid = make_execution_id()
    now = time.time()
    return ExecutionRecord(
        id=eid,
        automation_id="flow-test-002",
        status=ExecutionStatus.FAILED,
        started_at=now - 3.0,
        completed_at=now,
        total_steps=5,
        completed_steps=2,
        failed_steps=3,
        error_summary="3 errors: [click] Element not found; [type] Timeout; [click] Verification failed",
        step_logs=[
            ExecutionStepLog(
                id="log-0", execution_id=eid, step_id="step-0",
                step_type="click", step_index=0, status=StepStatus.SUCCESS,
                started_at=now - 3.0, completed_at=now - 2.0,
            ),
            ExecutionStepLog(
                id="log-1", execution_id=eid, step_id="step-1",
                step_type="type", step_index=1, status=StepStatus.SUCCESS,
                started_at=now - 2.0, completed_at=now - 1.5,
            ),
            ExecutionStepLog(
                id="log-2", execution_id=eid, step_id="step-2",
                step_type="click", step_index=2, status=StepStatus.FAILED,
                started_at=now - 1.5, completed_at=now - 1.2,
                error_message="Element not found: #search-button",
            ),
            ExecutionStepLog(
                id="log-3", execution_id=eid, step_id="step-3",
                step_type="type", step_index=3, status=StepStatus.FAILED,
                error_message="Timeout: waited 5000ms for element",
            ),
            ExecutionStepLog(
                id="log-4", execution_id=eid, step_id="step-4",
                step_type="click", step_index=4, status=StepStatus.FAILED,
                error_message="Verification failed: screenshot mismatch (SSIM=0.42)",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Fixtures: operation pairs for duplicate detection
# ---------------------------------------------------------------------------

@pytest.fixture
def sim_repeating_click_ops(sim_session_id):
    """Simulate the user clicking the same button 10 times in a loop (like pagination)."""
    ops = []
    now = int(time.time() * 1000)
    for i in range(10):
        ops.append(OperationEvent(
            id=make_op_id(i),
            session_id=sim_session_id,
            seq_num=i,
            timestamp=now + i * 1500,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=900, y=650, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=make_window("Data Grid - Page " + str(i + 1), "Chrome"),
                screenshot_path=f"/tmp/sim/page_{i}.png",
            ),
        ))
    return ops


@pytest.fixture
def sim_noise_ops(sim_session_id):
    """Simulate random noise: accidental clicks, meaningless movements."""
    ops = []
    now = int(time.time() * 1000)
    for i in range(20):
        ops.append(OperationEvent(
            id=make_op_id(i),
            session_id=sim_session_id,
            seq_num=i,
            timestamp=now + i * 200,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(
                x=random.randint(0, 1920),
                y=random.randint(0, 1080),
                button=random.choice([MouseButton.LEFT, MouseButton.RIGHT]),
                action=MouseAction.CLICK,
            ),
            context=OperationContext(active_window=make_window("Random Noise", "Desktop")),
        ))
    return ops
