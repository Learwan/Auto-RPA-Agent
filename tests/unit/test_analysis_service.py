import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analyzer.confidence_scorer import ScoredFlow
from src.analyzer.service import AnalysisService
from src.models.automation import AutomationFlow, AutomationStep, DetectedPattern, LocateStrategy, StepTarget, StepType
from src.models.desktop import Rect, WindowInfo
from src.models.operation import MouseAction, MouseButton, MouseEventData, OperationContext, OperationEvent, OperationType


class FakeRepository:
    def __init__(self, existing_flows=None):
        self.flows = {
            flow.id: flow.model_copy(deep=True)
            for flow in (existing_flows or [])
        }
        self.deleted_ids: list[str] = []

    async def list_automation_flows_by_session(self, session_id: str):
        return [
            flow.model_copy(deep=True)
            for flow in self.flows.values()
            if flow.source_session_id == session_id
        ]

    async def save_automation_flow(self, flow: AutomationFlow):
        self.flows[flow.id] = flow.model_copy(deep=True)
        return flow

    async def delete_automation_flows(self, flow_ids: list[str]):
        for flow_id in flow_ids:
            self.flows.pop(flow_id, None)
        self.deleted_ids.extend(flow_ids)


class FakeLLMService:
    is_configured = True
    _model = "fake-llm"

    async def analyze_flow_artifacts(self, flow_description: str, operations_summary: str, steps: list[dict]):
        return {
            "suggested_name": "AI Renamed Flow",
            "summary": "AI 总结了这个流程的目标。",
            "risks": ["文本匹配在界面变化时可能失效"],
            "improvements": ["增加等待步骤以提升稳定性"],
            "reliability_assessment": "整体可靠，但依赖界面文案。",
            "confidence": 0.84,
            "step_analyses": [
                {
                    "step_index": 0,
                    "intent": "点击 Open 按钮进入下一步",
                    "ui_state": "界面上应可见 Open 按钮",
                    "risk": "按钮文案变化会导致失配",
                    "suggested_next_actions": ["增加窗口校验", "补充等待"],
                    "refined_description": "等待 Open 按钮可见后再点击进入下一步",
                    "recommended_wait_ms": 1800,
                    "verification_condition": "element_exists",
                    "verification_timeout_ms": 6500,
                    "window_title_hint": "web",
                    "confidence": 0.78,
                }
            ],
        }


class FlakyLLMService(FakeLLMService):
    def __init__(self):
        self.calls = 0

    async def analyze_flow_artifacts(self, flow_description: str, operations_summary: str, steps: list[dict]):
        self.calls += 1
        if self.calls == 1:
            raise asyncio.TimeoutError("simulated timeout")
        return await super().analyze_flow_artifacts(flow_description, operations_summary, steps)


class FakeOperationRepairService:
    def __init__(self, repaired_operations):
        self.repaired_operations = repaired_operations
        self.calls = []

    async def repair_operations(self, operations):
        self.calls.append(operations)
        return self.repaired_operations


def _make_flow(flow_id: str, step_id: str) -> AutomationFlow:
    return AutomationFlow(
        id=flow_id,
        name="Switch And Click",
        description="repeatable pattern",
        source_session_id="session-1",
        steps=[
            AutomationStep(
                id=step_id,
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(
                    strategy=LocateStrategy.TEXT_MATCH,
                    title="Open",
                    text_contains="Open",
                ),
                description="Click the Open button",
            )
        ],
    )


def _make_type_flow(flow_id: str, step_id: str) -> AutomationFlow:
    return AutomationFlow(
        id=flow_id,
        name="Type Input",
        description="stale pattern",
        source_session_id="session-1",
        steps=[
            AutomationStep(
                id=step_id,
                type=StepType.TYPE,
                action={"text": "hello"},
                target=StepTarget(
                    strategy=LocateStrategy.TEXT_MATCH,
                    title="Search",
                    text_contains="Search",
                ),
                description="Type into search box",
            )
        ],
    )


@pytest.mark.asyncio
async def test_sync_session_flows_reuses_existing_signature_and_deletes_duplicates():
    existing = _make_flow("existing-flow", "step-existing")
    existing.execution_count = 4
    existing.success_count = 3

    duplicate = existing.model_copy(deep=True)
    duplicate.id = "duplicate-flow"
    duplicate.steps[0].id = "step-duplicate"

    stale = _make_type_flow("stale-flow", "step-stale")

    incoming = _make_flow("incoming-flow", "step-incoming")
    scored = ScoredFlow(flow=incoming, overall_confidence=0.92)

    repo = FakeRepository(existing_flows=[existing, duplicate, stale])
    service = AnalysisService(repository=repo)

    await service._sync_session_flows("session-1", [scored])

    assert scored.flow.id == "existing-flow"
    assert sorted(repo.deleted_ids) == ["duplicate-flow", "stale-flow"]
    assert sorted(repo.flows.keys()) == ["existing-flow"]
    assert repo.flows["existing-flow"].execution_count == 4
    assert repo.flows["existing-flow"].success_count == 3


@pytest.mark.asyncio
async def test_enhance_with_ai_persists_flow_and_step_metadata():
    flow = _make_flow("incoming-flow", "step-incoming")
    scored = ScoredFlow(flow=flow, overall_confidence=0.55)

    service = AnalysisService(repository=FakeRepository(), llm_service=FakeLLMService())

    result = await service._enhance_with_ai(scored, [], "session-1")

    assert result.flow.name == "AI Renamed Flow"
    assert result.flow.metadata["ai_analysis"]["summary"] == "AI 总结了这个流程的目标。"
    assert result.flow.metadata["ai_analysis"]["risks"] == ["文本匹配在界面变化时可能失效"]
    assert result.flow.metadata["ai_analysis"]["improvements"] == ["增加等待步骤以提升稳定性"]
    assert result.flow.steps[0].metadata["ai_analysis"]["intent"] == "点击 Open 按钮进入下一步"
    assert result.flow.steps[0].metadata["ai_analysis"]["risk"] == "按钮文案变化会导致失配"
    assert result.flow.steps[0].description == "等待 Open 按钮可见后再点击进入下一步"
    assert result.flow.steps[0].delay == 1800
    assert result.flow.steps[0].preconditions[0].field == "element_exists"
    assert result.flow.steps[0].preconditions[0].timeout_ms == 6500
    assert result.flow.steps[0].target.window_title == "web"
    assert result.flow.metadata["ai_enhancement_status"]["status"] == "success"
    assert result.flow.metadata["ai_enhancement_status"]["attempts"] == 1
    assert result.overall_confidence > 0.55


@pytest.mark.asyncio
async def test_enhance_with_ai_retries_after_timeout_then_succeeds():
    flow = _make_flow("incoming-flow", "step-incoming")
    scored = ScoredFlow(flow=flow, overall_confidence=0.55)

    flaky_llm = FlakyLLMService()
    service = AnalysisService(repository=FakeRepository(), llm_service=flaky_llm)

    result = await service._enhance_with_ai(scored, [], "session-1")

    assert flaky_llm.calls == 2
    assert result.flow.metadata["ai_enhancement_status"]["status"] == "success"
    assert result.flow.metadata["ai_enhancement_status"]["attempts"] == 2
    assert result.flow.metadata["ai_analysis"]["summary"] == "AI 总结了这个流程的目标。"


@pytest.mark.asyncio
async def test_enhance_with_ai_does_not_add_verification_for_uncertain_locator():
    flow = _make_flow("incoming-flow", "step-incoming")
    flow.steps[0].metadata.update(
        {
            "locator_requires_confirmation": True,
            "locator_confirmed": False,
        }
    )
    scored = ScoredFlow(flow=flow, overall_confidence=0.55)

    service = AnalysisService(repository=FakeRepository(), llm_service=FakeLLMService())

    result = await service._enhance_with_ai(scored, [], "session-1")

    assert result.flow.steps[0].preconditions == []
    assert result.flow.steps[0].target.window_title == "web"


@pytest.mark.asyncio
async def test_analyze_session_marks_non_target_flows_with_skipped_rank_limit():
    flow_primary = _make_flow("flow-primary", "step-primary")
    flow_secondary = _make_type_flow("flow-secondary", "step-secondary")
    flow_third = AutomationFlow(
        id="flow-third",
        name="Wait Flow",
        description="third pattern",
        source_session_id="session-1",
        steps=[
            AutomationStep(
                id="step-third",
                type=StepType.WAIT,
                action={"duration_ms": 1500},
                description="Wait for page to stabilize",
            )
        ],
    )

    patterns = [
        DetectedPattern(id="p1", pattern_sequence=["click"], support=1, confidence=0.9),
        DetectedPattern(id="p2", pattern_sequence=["type"], support=1, confidence=0.8),
        DetectedPattern(id="p3", pattern_sequence=["wait"], support=1, confidence=0.7),
    ]
    score_by_flow = {
        "flow-primary": 0.95,
        "flow-secondary": 0.85,
        "flow-third": 0.75,
    }

    service = AnalysisService(repository=FakeRepository(), llm_service=FakeLLMService())
    service._load_operations = AsyncMock(return_value=[object(), object(), object()])
    service._preprocessor.preprocess = MagicMock(
        return_value=[
            SimpleNamespace(op_type="mouse_click"),
            SimpleNamespace(op_type="type_text"),
            SimpleNamespace(op_type="mouse_click"),
        ]
    )
    service._segmenter.segment = MagicMock(return_value=[])
    service._detector.detect_patterns = MagicMock(return_value=patterns)
    service._generator.generate = MagicMock(return_value=[flow_primary, flow_secondary, flow_third])
    service._generator.generate_direct = MagicMock(return_value=None)
    service._scorer.score_flow = MagicMock(
        side_effect=lambda flow, normalized, pattern: ScoredFlow(
            flow=flow,
            overall_confidence=score_by_flow[flow.id],
        )
    )
    service._sync_session_flows = AsyncMock()

    async def fake_enhance(scored, normalized, session_id):
        service._set_ai_enhancement_status(
            scored.flow,
            {
                "enabled": True,
                "attempts": 1,
                "status": "success",
                "reason": None,
                "last_error": None,
            },
        )
        return scored

    service._enhance_with_ai = AsyncMock(side_effect=fake_enhance)

    result = await service.analyze_session("session-1", min_confidence=0.0)

    statuses = {
        scored.flow.id: (scored.flow.metadata or {}).get("ai_enhancement_status")
        for scored in result
    }
    assert statuses["flow-primary"]["status"] == "success"
    assert statuses["flow-secondary"]["status"] == "success"
    assert statuses["flow-third"]["status"] == "skipped_rank_limit"
    assert statuses["flow-third"]["attempts"] == 0


@pytest.mark.asyncio
async def test_analyze_session_attaches_semantic_intent_and_checkpoints():
    flow = _make_flow("flow-semantic", "step-semantic")
    flow.steps[0].metadata["source_seq_num"] = 0

    pattern = DetectedPattern(id="p-semantic", pattern_sequence=["click"], support=1, confidence=0.9)

    normalized = [
        SimpleNamespace(
            op_type="mouse_click",
            seq_num=0,
            timestamp=1,
            data={"x": 100, "y": 200},
            context={
                "active_window": {
                    "title": "Search",
                    "app_name": "Chrome",
                }
            },
        )
    ]

    service = AnalysisService(repository=FakeRepository(), llm_service=None)
    service._load_operations = AsyncMock(return_value=[object()])
    service._preprocessor.preprocess = MagicMock(return_value=normalized)
    service._segmenter.segment = MagicMock(return_value=[])
    service._detector.detect_patterns = MagicMock(return_value=[pattern])
    service._generator.generate = MagicMock(return_value=[flow])
    service._generator.generate_direct = MagicMock(return_value=None)
    service._scorer.score_flow = MagicMock(return_value=ScoredFlow(flow=flow, overall_confidence=0.91))
    service._sync_session_flows = AsyncMock()
    service._semantic_extractor.extract = MagicMock(
        return_value={
            "intent": {
                "overall": "信息查询与录入",
                "sub_goals": [],
                "business_context": "general",
                "confidence": 0.8,
            },
            "data_flows": [],
            "variables": [{"name": "text_1", "type": "string", "step_index": 0}],
            "annotations": [
                {
                    "step_index": 0,
                    "business_term": "信息检索",
                    "technical_op": "mouse_click",
                    "data_flow_role": "none",
                    "confidence": 0.9,
                }
            ],
        }
    )

    result = await service.analyze_session("session-1", min_confidence=0.0)

    assert len(result) == 1
    analyzed_flow = result[0].flow
    assert analyzed_flow.metadata["semantic_intent"]["intent"]["overall"] == "信息查询与录入"
    assert analyzed_flow.metadata["checkpoint_summary"]["steps_with_checkpoints"] >= 1
    assert "closed_loop_assessment" in analyzed_flow.metadata
    assert analyzed_flow.metadata["closed_loop_ready"] is False
    assert analyzed_flow.steps[0].metadata["semantic_annotation"]["business_term"] == "信息检索"
    assert any(cond.field == "element_exists" for cond in analyzed_flow.steps[0].preconditions)
    assert analyzed_flow.steps[0].metadata["postconditions"][0]["field"] == "page_stable"


@pytest.mark.asyncio
async def test_checkpoint_synthesis_limits_uncertain_locator_steps_to_postchecks():
    flow = _make_flow("flow-uncertain", "step-uncertain")
    flow.steps[0].metadata.update(
        {
            "source_seq_num": 0,
            "locator_requires_confirmation": True,
            "locator_confirmed": False,
        }
    )

    pattern = DetectedPattern(id="p-uncertain", pattern_sequence=["click"], support=1, confidence=0.9)

    normalized = [
        SimpleNamespace(
            op_type="mouse_click",
            seq_num=0,
            timestamp=1,
            data={"x": 100, "y": 200},
            context={
                "active_window": {
                    "title": "Search",
                    "app_name": "Chrome",
                }
            },
        )
    ]

    service = AnalysisService(repository=FakeRepository(), llm_service=None)
    service._load_operations = AsyncMock(return_value=[object()])
    service._preprocessor.preprocess = MagicMock(return_value=normalized)
    service._segmenter.segment = MagicMock(return_value=[])
    service._detector.detect_patterns = MagicMock(return_value=[pattern])
    service._generator.generate = MagicMock(return_value=[flow])
    service._generator.generate_direct = MagicMock(return_value=None)
    service._scorer.score_flow = MagicMock(return_value=ScoredFlow(flow=flow, overall_confidence=0.91))
    service._sync_session_flows = AsyncMock()
    service._semantic_extractor.extract = MagicMock(
        return_value={
            "intent": {"overall": "信息查询与录入", "sub_goals": [], "business_context": "general", "confidence": 0.8},
            "data_flows": [],
            "variables": [],
            "annotations": [],
        }
    )

    result = await service.analyze_session("session-1", min_confidence=0.0)

    analyzed_flow = result[0].flow
    assert analyzed_flow.steps[0].preconditions == []
    assert analyzed_flow.steps[0].metadata["postconditions"][0]["field"] == "page_stable"


@pytest.mark.asyncio
async def test_analyze_session_repairs_operations_before_preprocess():
    flow = _make_flow("flow-repaired", "step-repaired")
    pattern = DetectedPattern(id="p-repaired", pattern_sequence=["click"], support=1, confidence=0.9)
    raw_operation = OperationEvent(
        id="op-raw",
        session_id="session-1",
        seq_num=1,
        timestamp=1000,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=300, y=420, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(
            platform="macos",
            screenshot_path="snapshot://snap-repaired-1",
            active_window=WindowInfo(
                window_id="window-1",
                title="企业微信",
                app_name="企业微信",
                pid=1,
                bounds=Rect(x=0, y=0, width=1440, height=900),
                is_active=True,
            ),
        ),
    )
    repaired_operation = raw_operation.model_copy(deep=True)
    repaired_operation.context = raw_operation.context.model_copy(
        update={
            "node_suggestion": {
                "snapshot_id": "snap-repaired-1",
                "vision_source": "local_vlm",
                "confidence": 0.86,
                "target": {
                    "strategy": "text_match",
                    "text_contains": "发送",
                    "window_title": "企业微信",
                    "role": "button",
                    "bounds": {"x": 280, "y": 396, "width": 120, "height": 48},
                },
            }
        }
    )
    repair_service = FakeOperationRepairService([repaired_operation])
    normalized = [
        SimpleNamespace(
            op_type="mouse_click",
            seq_num=0,
            timestamp=1,
            data={"x": 300, "y": 420},
            context={
                "active_window": {"title": "企业微信", "app_name": "企业微信"},
                "node_suggestion": repaired_operation.context.node_suggestion,
                "screenshot_path": "snapshot://snap-repaired-1",
            },
        )
    ]

    service = AnalysisService(
        repository=FakeRepository(),
        llm_service=None,
        operation_repair_service=repair_service,
    )
    service._load_operations = AsyncMock(return_value=[raw_operation])
    service._preprocessor.preprocess = MagicMock(return_value=normalized)
    service._segmenter.segment = MagicMock(return_value=[])
    service._detector.detect_patterns = MagicMock(return_value=[pattern])
    service._generator.generate = MagicMock(return_value=[flow])
    service._generator.generate_direct = MagicMock(return_value=None)
    service._scorer.score_flow = MagicMock(return_value=ScoredFlow(flow=flow, overall_confidence=0.93))
    service._sync_session_flows = AsyncMock()
    service._semantic_extractor.extract = MagicMock(
        return_value={
            "intent": {"overall": "沟通协作", "sub_goals": [], "business_context": "general", "confidence": 0.8},
            "data_flows": [],
            "variables": [],
            "annotations": [],
        }
    )

    await service.analyze_session("session-1", min_confidence=0.0)

    assert repair_service.calls == [[raw_operation]]
    assert service._preprocessor.preprocess.call_args.args[0] == [repaired_operation]
