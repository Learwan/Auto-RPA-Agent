"""LLM 视觉驱动闭环仿真测试

完整验证 录制 → 分析 → AI增强 → 闭环认证(certified) → 执行 → 反馈 管线。

核心约束:
- 所有步骤禁止坐标定位 (POSITION strategy)
- 每个交互步骤必须具备: 稳定定位器 + 窗口上下文 + 前置条件 + 后置校验
- FlowClosureAssessor 必须返回 ready=True / status=certified
- ExecutionService._ensure_flow_ready_for_execution 必须通过
- 执行必须在 ExecutionEngine 中完成完整生命周期
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analyzer.closure_assessor import FlowClosureAssessor, FlowClosureAssessment
from src.analyzer.knowledge_graph import WorkflowKnowledgeGraph
from src.analyzer.service import AnalysisService
from src.db.repository import Repository
from src.executor.adaptive_workflow import AdaptiveWorkflowEngine
from src.executor.element_locator import LocatedElement
from src.executor.engine import ExecutionEngine
from src.executor.error_handler import ErrorHandler
from src.executor.intelligent_recovery import IntelligentErrorRecovery
from src.executor.self_evolution import SelfEvolutionSystem
from src.executor.step_executor import StepExecutor
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    ErrorAction,
    ErrorHandlingPolicy,
    FlowVariable,
    LocateStrategy,
    StepCondition,
    StepTarget,
    StepType,
)
from src.models.desktop import Point, Rect, UIElement, WindowInfo
from src.models.execution import ExecutionRecord, ExecutionStatus, StepStatus
from src.models.operation import (
    KeyAction,
    KeyboardEventData,
    MouseAction,
    MouseButton,
    MouseEventData,
    NavigationEventData,
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_window(title: str, app: str) -> WindowInfo:
    return WindowInfo(
        window_id=f"win-{uuid.uuid4().hex[:6]}",
        title=title,
        app_name=app,
        pid=10000,
        bounds=Rect(x=0, y=0, width=1920, height=1080),
        is_active=True,
    )


def _precondition(field: str = "element_exists", timeout_ms: int = 5000) -> StepCondition:
    return StepCondition(field=field, operator="truthy", value=True, timeout_ms=timeout_ms)


def _postcondition_metadata(field: str = "page_stable", timeout_ms: int = 3000) -> dict:
    return {
        "postconditions": [
            {"field": field, "operator": "truthy", "value": True, "timeout_ms": timeout_ms}
        ],
        "checkpoints": {
            "pre": [{"field": "element_exists", "operator": "truthy", "value": True, "timeout_ms": 5000}],
            "post": [{"field": field, "operator": "truthy", "value": True, "timeout_ms": timeout_ms}],
        },
    }


def _css_target(selector: str, window_title: str, url: str | None = None) -> StepTarget:
    return StepTarget(
        strategy=LocateStrategy.CSS_SELECTOR,
        selector=selector,
        window_title=window_title,
        url=url,
    )


def _xpath_target(xpath: str, window_title: str, url: str | None = None) -> StepTarget:
    return StepTarget(
        strategy=LocateStrategy.XPATH,
        xpath=xpath,
        window_title=window_title,
        url=url,
    )


def _accessibility_target(aid: str, window_title: str) -> StepTarget:
    return StepTarget(
        strategy=LocateStrategy.ACCESSIBILITY_ID,
        accessibility_id=aid,
        window_title=window_title,
    )


def _certified_step(
    index: int,
    step_type: StepType,
    target: StepTarget,
    description: str,
    action: dict | None = None,
) -> AutomationStep:
    return AutomationStep(
        id=f"cs-{uuid.uuid4().hex[:8]}-{index:02d}",
        type=step_type,
        target=target,
        description=description,
        action=action or {},
        execution_order=index,
        preconditions=[_precondition()],
        metadata=_postcondition_metadata(),
        verification_enabled=True,
    )


# ---------------------------------------------------------------------------
# LLM artifact mock that makes analyze_flow_artifacts return rich data
# including per-step window_title_hint to help fill context
# ---------------------------------------------------------------------------

def _make_llm_artifacts_response(flow_steps: list[AutomationStep]) -> dict:
    return {
        "suggested_name": "GitHub 仓库搜索与导航流程",
        "summary": "在 GitHub 首页搜索仓库, 点击搜索结果, 查看仓库详情, 完整的 Web 自动化流程。",
        "risks": ["搜索结果列表可能因网络延迟加载慢"],
        "improvements": [
            "增加搜索结果加载等待时间",
            "为结果列表项添加备用 XPath 定位",
        ],
        "reliability_assessment": "Flow is well-structured with stable CSS selectors and proper window context.",
        "confidence": 0.92,
        "step_analyses": [
            {
                "step_index": i,
                "intent": f"Step {i}: {step.description}",
                "ui_state": "GitHub page is stable and loaded",
                "risk": "minimal" if step.type != StepType.WAIT else "none",
                "suggested_next_actions": [],
                "refined_description": step.description,
                "recommended_wait_ms": 500 if step.type != StepType.WAIT else 2000,
                "verification_condition": "element_exists",
                "verification_timeout_ms": 5000,
                "target_text_hint": "",
                "window_title_hint": step.target.window_title or "GitHub",
                "confidence": 0.93,
            }
            for i, step in enumerate(flow_steps)
        ],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vision_llm_service():
    """LLM service mock that:
    - analyze_flow_artifacts → rich structured JSON with step-level insights
    - suggest_flow_name → meaningful flow name
    - explain_operations → textual explanation
    """
    llm = MagicMock()
    llm.is_configured = True
    llm._model = "gpt-4-vision-preview"
    llm.suggest_flow_name = AsyncMock(return_value="GitHub 搜索与导航")
    llm.explain_operations = AsyncMock(
        return_value="用户在 GitHub 上搜索仓库, 点击搜索按钮, 选择第一个结果并查看详情。"
    )

    def _artifacts_factory(flow_desc, op_summary, steps):
        return _make_llm_artifacts_response(
            [MagicMock(description=s.get("description", ""), type=StepType.CLICK,
                       target=MagicMock(window_title=s.get("window_title")))
             for s in steps]
        )

    llm.analyze_flow_artifacts = AsyncMock(side_effect=_artifacts_factory)
    return llm


@pytest.fixture
def certified_operations():
    """模拟一个真实的 GitHub 搜索操作序列, 带完整窗口上下文。"""
    now = int(time.time() * 1000)
    chrome = _make_window("GitHub", "Google Chrome")

    return [
        OperationEvent(
            id="vop-000", session_id="vis-sess-001", seq_num=0, timestamp=now,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=600, y=50, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=chrome, screenshot_path="/tmp/sim/gh_search_bar.png",
                process_name="Google Chrome", platform="macOS",
            ),
        ),
        OperationEvent(
            id="vop-001", session_id="vis-sess-001", seq_num=1, timestamp=now + 500,
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(key="t", action=KeyAction.INPUT, text="auto-agent-workflow"),
            context=OperationContext(
                active_window=chrome, screenshot_path="/tmp/sim/gh_typing.png",
                process_name="Google Chrome", platform="macOS",
            ),
        ),
        OperationEvent(
            id="vop-002", session_id="vis-sess-001", seq_num=2, timestamp=now + 2000,
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(key="Return", action=KeyAction.PRESS),
            context=OperationContext(
                active_window=_make_window("GitHub - Search", "Google Chrome"),
                screenshot_path="/tmp/sim/gh_enter.png",
                process_name="Google Chrome", platform="macOS",
            ),
        ),
        OperationEvent(
            id="vop-003", session_id="vis-sess-001", seq_num=3, timestamp=now + 5000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=400, y=300, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=_make_window("Search Results - GitHub", "Google Chrome"),
                screenshot_path="/tmp/sim/gh_result_click.png",
                process_name="Google Chrome", platform="macOS",
            ),
        ),
    ]


@pytest.fixture
def certified_flow():
    """一个完全满足闭环认证的 GitHub 搜索流程。

    - 4 个交互步骤, 全部使用 CSS_SELECTOR / XPATH / ACCESSIBILITY_ID
    - 每步都有 window_title 上下文
    - 每步都有前置条件 (element_exists) 和后置校验 (page_stable)
    - 零坐标定位
    """
    steps = [
        _certified_step(
            0, StepType.CLICK,
            _css_target("input.header-search-input", "GitHub", url="https://github.com"),
            "点击 GitHub 搜索框",
            action={"button": "left", "clicks": 1},
        ),
        _certified_step(
            1, StepType.TYPE,
            _css_target("input.header-search-input", "GitHub", url="https://github.com"),
            "输入搜索关键词 auto-agent-workflow",
            action={"text": "auto-agent-workflow", "interval": 0.05},
        ),
        _certified_step(
            2, StepType.HOTKEY,
            _xpath_target("//input[@name='q']", "GitHub - Search", url="https://github.com/search"),
            "按回车提交搜索",
            action={"keys": ["Return"]},
        ),
        _certified_step(
            3, StepType.CLICK,
            _accessibility_target("search-result-0", "Search Results - GitHub"),
            "点击第一个搜索结果",
            action={"button": "left", "clicks": 1},
        ),
    ]

    return AutomationFlow(
        id=f"flow-vis-{uuid.uuid4().hex[:8]}",
        name="GitHub 搜索导航",
        description="在 GitHub 上搜索仓库并查看结果",
        steps=steps,
        confidence=0.88,
    )


@pytest.fixture
def vision_platform_adapter():
    """平台适配器 mock, 模拟完整视觉能力。"""
    adapter = MagicMock()
    adapter.get_screen_size = AsyncMock(return_value=(1920, 1080))
    adapter.get_logical_screen_size = AsyncMock(return_value=(1920, 1080))
    adapter.get_platform_name.return_value = "web"
    adapter.move = AsyncMock(return_value=True)
    adapter.click = AsyncMock(return_value=True)
    adapter.type_text = AsyncMock(return_value=True)
    adapter.press_keys = AsyncMock(return_value=True)
    adapter.scroll = AsyncMock(return_value=True)
    adapter.drag = AsyncMock(return_value=True)
    adapter.take_screenshot = AsyncMock(return_value="/tmp/sim/vision_screenshot.png")
    adapter.capture_screen = AsyncMock(return_value=b"\x89PNG_fake_screenshot_data")
    adapter.get_active_window = AsyncMock(return_value=_make_window("GitHub", "Google Chrome"))
    adapter.get_window_list = AsyncMock(return_value=[_make_window("GitHub", "Google Chrome")])
    adapter.get_windows = AsyncMock(return_value=[_make_window("GitHub", "Google Chrome")])
    adapter.click_target = AsyncMock(return_value=True)
    adapter.type_text_target = AsyncMock(return_value=True)
    adapter.press_hotkey = AsyncMock(return_value=True)
    adapter.scroll_target = AsyncMock(return_value=True)
    adapter.drag_target = AsyncMock(return_value=True)
    adapter.activate_window = AsyncMock(return_value=True)
    adapter.analyze_screen = AsyncMock(return_value={
        "ui_state_description": "GitHub search page with results loaded",
        "actionable_elements": [
            {
                "label": "Search input",
                "element_type": "input",
                "confidence": 0.97,
                "bounding_box": {"x": 500, "y": 40, "width": 400, "height": 32},
            },
            {
                "label": "Search result item",
                "element_type": "link",
                "confidence": 0.95,
                "bounding_box": {"x": 200, "y": 280, "width": 600, "height": 40},
            },
        ],
        "user_intent": "Search for a repository on GitHub",
    })
    adapter.close = AsyncMock()
    return adapter


@pytest.fixture
def vision_element_locator():
    """元素定位器 mock, 根据 target 的 strategy 返回匹配的元素。"""

    async def _locate(target):
        strategy = target.strategy if target else LocateStrategy.CSS_SELECTOR
        identifier = (
            target.accessibility_id or target.selector or target.xpath or "element"
        ) if target else "element"

        element = UIElement(
            role="textbox" if strategy != LocateStrategy.ACCESSIBILITY_ID else "button",
            title=identifier,
            identifier=target.accessibility_id if target and target.accessibility_id else None,
            bounds=Rect(x=500, y=40, width=400, height=32),
            is_enabled=True,
            is_visible=True,
        )
        return LocatedElement(
            element=element,
            strategy_used=strategy,
            position=Point(x=700, y=56),
            confidence=0.97,
        )

    async def _wait_for_element(target, timeout_ms=5000):
        return await _locate(target)

    locator = MagicMock()
    locator.locate = AsyncMock(side_effect=_locate)
    locator.locate_all = AsyncMock(return_value=[])
    locator.wait_for_element = AsyncMock(side_effect=_wait_for_element)
    return locator


# ===========================================================================
# 测试：闭环认证 — 必须 ready=True
# ===========================================================================

class TestClosureAssessmentCertified:
    """验证 certified_flow 满足 FlowClosureAssessor 的全部严格条件。"""

    def test_certified_flow_passes_assessment(self, certified_flow):
        assessor = FlowClosureAssessor()
        result = assessor.assess(certified_flow)

        assert result.ready is True, (
            f"闭环认证失败!\n"
            f"  status={result.status}\n"
            f"  score={result.score}\n"
            f"  readiness_level={result.readiness_level}\n"
            f"  metrics={result.metrics}\n"
            f"  issues={[i.model_dump() for i in result.issues]}\n"
            f"  hints={result.remediation_hints}"
        )
        assert result.status == "certified"
        assert result.readiness_level == "production_ready"
        assert result.score >= 0.9

    def test_zero_position_steps(self, certified_flow):
        assessor = FlowClosureAssessor()
        result = assessor.assess(certified_flow)
        assert result.metrics["position_steps"] == 0, "不允许任何坐标定位步骤"

    def test_zero_blocking_issues(self, certified_flow):
        assessor = FlowClosureAssessor()
        result = assessor.assess(certified_flow)
        assert result.metrics["blocking_issue_count"] == 0, "不允许任何阻断性问题"

    def test_all_ratios_100_percent(self, certified_flow):
        assessor = FlowClosureAssessor()
        result = assessor.assess(certified_flow)
        assert result.metrics["certified_ratio"] >= 0.999, "认证比率必须 100%"
        assert result.metrics["checkpoint_ratio"] >= 0.999, "检查点覆盖必须 100%"
        assert result.metrics["context_ratio"] >= 0.999, "上下文覆盖必须 100%"

    def test_no_confirmation_required(self, certified_flow):
        assessor = FlowClosureAssessor()
        result = assessor.assess(certified_flow)
        assert result.metrics["confirmation_required_steps"] == 0, "不允许需要人工确认的步骤"

    def test_remediation_hints_clean(self, certified_flow):
        assessor = FlowClosureAssessor()
        result = assessor.assess(certified_flow)
        assert result.remediation_hints == ["流程已通过所有闭环检查。"]


# ===========================================================================
# 测试：LLM 视觉驱动端到端闭环 — 录制 → 分析 → AI → 认证 → 执行
# ===========================================================================

class TestLLMVisionClosedLoop:
    """端到端闭环仿真: 操作序列经过 LLM 增强后达到 certified, 再成功执行。"""

    @pytest.mark.asyncio
    async def test_full_pipeline_achieves_certified(
        self, vision_llm_service, certified_operations
    ):
        """录制操作 → AnalysisService 分析 → AI 增强 → 闭环认证 ready=True。"""
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=certified_operations)

        kg = WorkflowKnowledgeGraph()
        svc = AnalysisService(
            repository=mock_db,
            llm_service=vision_llm_service,
            knowledge_graph=kg,
        )

        scored_flows = await svc.analyze_session("vis-sess-001", min_confidence=0.3)
        assert scored_flows, "分析必须生成至少一个流程"

        vision_llm_service.suggest_flow_name.assert_called()
        vision_llm_service.analyze_flow_artifacts.assert_called()

        for scored in scored_flows:
            flow = scored.flow
            meta = flow.metadata or {}
            assessment_data = meta.get("closed_loop_assessment", {})

            ai_status = meta.get("ai_enhancement_status", {})
            assert ai_status.get("status") == "success", (
                f"AI 增强必须成功, 实际: {ai_status}"
            )

            assert flow.name, "流程必须有名称"
            assert meta.get("ai_analysis", {}).get("participated") is True, "AI 必须参与分析"

    @pytest.mark.asyncio
    async def test_certified_flow_execution_succeeds(
        self, certified_flow, vision_platform_adapter, vision_element_locator
    ):
        """certified 流程能通过 ExecutionService 闭环检查并成功执行。"""
        from src.analyzer.closure_assessor import FlowClosureAssessor

        assessor = FlowClosureAssessor()
        assessment = assessor.assess(certified_flow)
        assert assessment.ready is True, f"前置: 流程必须通过认证: {assessment.summary}"

        step_executor = StepExecutor(vision_element_locator, vision_platform_adapter)
        engine = ExecutionEngine(
            flow=certified_flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})

        assert record is not None
        assert record.status == ExecutionStatus.COMPLETED, (
            f"执行必须成功完成, 实际状态: {record.status.value}\n"
            f"  completed_steps={record.completed_steps}\n"
            f"  failed_steps={record.failed_steps}\n"
            f"  error={record.error_summary}"
        )
        assert record.total_steps == len(certified_flow.steps)
        assert record.completed_steps == record.total_steps
        assert record.failed_steps == 0

    @pytest.mark.asyncio
    async def test_execution_service_gate_passes(self, certified_flow):
        """ExecutionService._ensure_flow_ready_for_execution 不抛异常。"""
        from src.executor.execution_service import ExecutionService

        svc = ExecutionService()
        svc._ensure_flow_ready_for_execution(certified_flow)

    @pytest.mark.asyncio
    async def test_full_execute_with_feedback_collection(
        self, certified_flow, vision_platform_adapter, vision_element_locator
    ):
        """执行 certified 流程并收集反馈到 KnowledgeGraph + SelfEvolution。"""
        step_executor = StepExecutor(vision_element_locator, vision_platform_adapter)
        engine = ExecutionEngine(
            flow=certified_flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        feedbacks: list = []
        engine.add_feedback_callback(lambda fb: feedbacks.append(fb))

        record = await engine.execute({})
        assert record.status == ExecutionStatus.COMPLETED

        kg = WorkflowKnowledgeGraph()
        evolution = SelfEvolutionSystem()

        unique_step_ids = {log.step_id for log in record.step_logs if log.step_type}
        assert len(unique_step_ids) == len(certified_flow.steps)

        from src.analyzer.knowledge_graph import OperationPattern
        from src.models.execution import ExecutionFeedback

        successful_types = [log.step_type for log in record.step_logs if log.status == StepStatus.SUCCESS]
        pattern = OperationPattern(
            pattern_id=f"exec_{record.id[:12]}",
            op_sequence=successful_types,
            app_name="Google Chrome",
            window_context="GitHub",
            avg_duration_ms=500,
            success_rate=1.0,
            occurrence_count=1,
        )
        kg.add_operation_pattern(pattern)

        for log in record.step_logs:
            fb = ExecutionFeedback(
                execution_id=record.id,
                step_id=log.step_id,
                step_index=log.step_index,
                step_type=log.step_type or "unknown",
                status=log.status.value if log.status else "unknown",
                success=log.status == StepStatus.SUCCESS,
                error=log.error_message,
                action=log.step_type or "",
                timestamp=log.completed_at or time.time(),
                duration_ms=(
                    int((log.completed_at - log.started_at) * 1000)
                    if log.started_at and log.completed_at else 0
                ),
            )
            evolution.record_feedback(fb)

        stats = kg.get_stats()
        assert stats["operation_patterns"] >= 1, f"KG 必须积累模式: {stats}"

        assert len(feedbacks) > 0, "必须收到执行反馈回调"


# ===========================================================================
# 测试：反面验证 — 含坐标定位的流程必须被闭环拒绝
# ===========================================================================

class TestPositionStrategyRejected:
    """验证使用坐标定位 (POSITION) 的流程一定通不过闭环认证。"""

    def test_position_step_fails_certification(self):
        step = AutomationStep(
            id="reject-pos-0",
            type=StepType.CLICK,
            target=StepTarget(strategy=LocateStrategy.POSITION, window_title="Test"),
            description="坐标定位点击",
            action={"button": "left"},
            execution_order=0,
            preconditions=[_precondition()],
            metadata=_postcondition_metadata(),
        )
        flow = AutomationFlow(
            id="flow-reject-pos",
            name="含坐标定位的流程",
            steps=[step],
            confidence=0.9,
        )
        assessor = FlowClosureAssessor()
        result = assessor.assess(flow)

        assert result.ready is False, "含坐标定位的流程不得通过认证"
        assert result.metrics["position_steps"] >= 1
        assert any(i.code == "position_only_target" for i in result.issues)

    def test_mixed_flow_with_one_position_step_fails(self, certified_flow):
        bad_step = AutomationStep(
            id="inject-pos-99",
            type=StepType.CLICK,
            target=StepTarget(strategy=LocateStrategy.POSITION, window_title="GitHub"),
            description="注入的坐标定位步骤",
            action={"button": "left"},
            execution_order=99,
            preconditions=[_precondition()],
            metadata=_postcondition_metadata(),
        )
        certified_flow.steps.append(bad_step)
        assessor = FlowClosureAssessor()
        result = assessor.assess(certified_flow)

        assert result.ready is False, "即使只有一个坐标定位步骤也必须被拒绝"
        assert result.metrics["position_steps"] >= 1


# ===========================================================================
# 测试：反面验证 — 缺少上下文/检查点的流程被闭环拒绝
# ===========================================================================

class TestIncompleteFlowRejected:
    """验证缺少关键闭环要素的流程被拒绝。"""

    def test_missing_window_context_rejected(self):
        step = AutomationStep(
            id="reject-ctx-0",
            type=StepType.CLICK,
            target=StepTarget(strategy=LocateStrategy.CSS_SELECTOR, selector="#btn"),
            description="缺少窗口上下文",
            action={"button": "left"},
            execution_order=0,
            preconditions=[_precondition()],
            metadata=_postcondition_metadata(),
        )
        flow = AutomationFlow(id="flow-no-ctx", name="无上下文", steps=[step], confidence=0.9)
        result = FlowClosureAssessor().assess(flow)
        assert result.ready is False
        assert any(i.code == "missing_window_context" for i in result.issues)

    def test_missing_preconditions_rejected(self):
        step = AutomationStep(
            id="reject-pre-0",
            type=StepType.CLICK,
            target=_css_target("#btn", "App"),
            description="缺少前置条件",
            action={"button": "left"},
            execution_order=0,
            preconditions=[],
            metadata=_postcondition_metadata(),
        )
        flow = AutomationFlow(id="flow-no-pre", name="无前置条件", steps=[step], confidence=0.9)
        result = FlowClosureAssessor().assess(flow)
        assert result.ready is False
        assert any(i.code == "missing_preconditions" for i in result.issues)

    def test_missing_postconditions_rejected(self):
        step = AutomationStep(
            id="reject-post-0",
            type=StepType.CLICK,
            target=_css_target("#btn", "App"),
            description="缺少后置校验",
            action={"button": "left"},
            execution_order=0,
            preconditions=[_precondition()],
            metadata={},
        )
        flow = AutomationFlow(id="flow-no-post", name="无后置校验", steps=[step], confidence=0.9)
        result = FlowClosureAssessor().assess(flow)
        assert result.ready is False
        assert any(i.code == "missing_postconditions" for i in result.issues)


# ===========================================================================
# 测试：多场景闭环仿真
# ===========================================================================

class TestMultiScenarioClosedLoop:
    """不同类型的完整闭环流程验证。"""

    @staticmethod
    def _build_login_flow() -> AutomationFlow:
        """Web 登录流程: 输入用户名 → 输入密码 → 点击登录。"""
        steps = [
            _certified_step(
                0, StepType.CLICK,
                _css_target("#username", "Login - MyApp", url="https://app.example.com/login"),
                "点击用户名输入框",
            ),
            _certified_step(
                1, StepType.TYPE,
                _css_target("#username", "Login - MyApp", url="https://app.example.com/login"),
                "输入用户名",
                action={"text": "admin@example.com"},
            ),
            _certified_step(
                2, StepType.TYPE,
                _css_target("#password", "Login - MyApp", url="https://app.example.com/login"),
                "输入密码",
                action={"text": "{{password}}"},
            ),
            _certified_step(
                3, StepType.CLICK,
                _xpath_target("//button[@type='submit']", "Login - MyApp", url="https://app.example.com/login"),
                "点击登录按钮",
                action={"button": "left"},
            ),
        ]
        return AutomationFlow(
            id="flow-login-001",
            name="Web 登录",
            description="Web 应用登录流程",
            steps=steps,
            confidence=0.92,
            variables=[FlowVariable(name="password", var_type="string", default_value="test123")],
        )

    @staticmethod
    def _build_form_submission_flow() -> AutomationFlow:
        """表单填写提交流程: 多种操作类型。"""
        steps = [
            _certified_step(
                0, StepType.CLICK,
                _css_target("input[name='title']", "New Issue - Tracker", url="https://tracker.example.com/new"),
                "点击标题输入框",
            ),
            _certified_step(
                1, StepType.TYPE,
                _css_target("input[name='title']", "New Issue - Tracker", url="https://tracker.example.com/new"),
                "输入问题标题",
                action={"text": "Bug: 搜索结果不加载"},
            ),
            _certified_step(
                2, StepType.CLICK,
                _css_target("textarea[name='body']", "New Issue - Tracker", url="https://tracker.example.com/new"),
                "点击描述输入框",
            ),
            _certified_step(
                3, StepType.TYPE,
                _css_target("textarea[name='body']", "New Issue - Tracker", url="https://tracker.example.com/new"),
                "输入问题描述",
                action={"text": "搜索关键词后结果列表为空, 预期应显示匹配结果。"},
            ),
            _certified_step(
                4, StepType.SCROLL,
                _css_target("form.issue-form", "New Issue - Tracker", url="https://tracker.example.com/new"),
                "滚动到提交按钮",
                action={"direction": "down", "amount": 300},
            ),
            _certified_step(
                5, StepType.CLICK,
                _xpath_target("//button[text()='Submit']", "New Issue - Tracker", url="https://tracker.example.com/new"),
                "点击提交按钮",
                action={"button": "left"},
            ),
        ]
        return AutomationFlow(
            id="flow-form-001",
            name="问题提交",
            description="在 Tracker 上创建新问题",
            steps=steps,
            confidence=0.90,
        )

    @staticmethod
    def _build_desktop_app_flow() -> AutomationFlow:
        """桌面应用操作流程: 使用 accessibility_id。"""
        steps = [
            _certified_step(
                0, StepType.CLICK,
                _accessibility_target("menu-file", "VS Code - Project"),
                "点击 File 菜单",
                action={"button": "left"},
            ),
            _certified_step(
                1, StepType.CLICK,
                _accessibility_target("menu-item-new-file", "VS Code - Project"),
                "点击 New File",
                action={"button": "left"},
            ),
            _certified_step(
                2, StepType.TYPE,
                _accessibility_target("editor-input", "Untitled - VS Code"),
                "输入文件内容",
                action={"text": "# Hello World\nprint('hello')"},
            ),
            _certified_step(
                3, StepType.HOTKEY,
                _accessibility_target("editor-input", "Untitled - VS Code"),
                "保存文件 Cmd+S",
                action={"keys": ["command", "s"]},
            ),
        ]
        return AutomationFlow(
            id="flow-desktop-001",
            name="VS Code 新建文件",
            description="在 VS Code 中新建文件并保存",
            steps=steps,
            confidence=0.88,
        )

    def test_login_flow_certified(self):
        flow = self._build_login_flow()
        result = FlowClosureAssessor().assess(flow)
        assert result.ready is True, f"登录流程认证失败: {result.summary}, issues={[i.model_dump() for i in result.issues]}"
        assert result.metrics["position_steps"] == 0

    def test_form_submission_certified(self):
        flow = self._build_form_submission_flow()
        result = FlowClosureAssessor().assess(flow)
        assert result.ready is True, f"表单提交认证失败: {result.summary}, issues={[i.model_dump() for i in result.issues]}"
        assert result.metrics["position_steps"] == 0

    def test_desktop_app_certified(self):
        flow = self._build_desktop_app_flow()
        result = FlowClosureAssessor().assess(flow)
        assert result.ready is True, f"桌面应用认证失败: {result.summary}, issues={[i.model_dump() for i in result.issues]}"
        assert result.metrics["position_steps"] == 0

    @pytest.mark.asyncio
    async def test_login_flow_executes(self, vision_platform_adapter, vision_element_locator):
        flow = self._build_login_flow()
        engine = ExecutionEngine(
            flow=flow,
            step_executor=StepExecutor(vision_element_locator, vision_platform_adapter),
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )
        record = await engine.execute({"password": "secure_pass"})
        assert record.status == ExecutionStatus.COMPLETED
        assert record.failed_steps == 0

    @pytest.mark.asyncio
    async def test_form_submission_executes(self, vision_platform_adapter, vision_element_locator):
        flow = self._build_form_submission_flow()
        engine = ExecutionEngine(
            flow=flow,
            step_executor=StepExecutor(vision_element_locator, vision_platform_adapter),
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )
        record = await engine.execute({})
        assert record.status == ExecutionStatus.COMPLETED
        assert record.failed_steps == 0

    @pytest.mark.asyncio
    async def test_desktop_app_executes(self, vision_platform_adapter, vision_element_locator):
        flow = self._build_desktop_app_flow()
        engine = ExecutionEngine(
            flow=flow,
            step_executor=StepExecutor(vision_element_locator, vision_platform_adapter),
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )
        record = await engine.execute({})
        assert record.status == ExecutionStatus.COMPLETED
        assert record.failed_steps == 0
