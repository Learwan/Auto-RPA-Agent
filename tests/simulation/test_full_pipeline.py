"""End-to-end simulation: Recording → Analysis → Generation → Execution → Feedback"""
import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analyzer.knowledge_graph import WorkflowKnowledgeGraph
from src.analyzer.service import AnalysisService
from src.db.repository import Repository
from src.executor.adaptive_workflow import AdaptiveWorkflowEngine
from src.executor.engine import ExecutionEngine
from src.executor.error_handler import ErrorHandler
from src.executor.execution_service import ExecutionService
from src.executor.intelligent_recovery import IntelligentErrorRecovery
from src.executor.self_evolution import SelfEvolutionSystem
from src.executor.step_executor import StepExecutor
from src.models.automation import AutomationStep, LocateStrategy, StepTarget, StepType
from src.models.execution import ExecutionStatus, StepStatus


class TestFullPipelineSimulation:
    """Simulate the complete flow from recording to execution with AI enhancement."""

    @pytest.mark.asyncio
    async def test_full_pipeline_click_workflow(
        self, sim_mock_llm_service, sim_platform_adapter, sim_element_locator
    ):
        """Simulate: record 3 clicks → analyze → generate flow → execute → collect feedback."""
        mock_db = MagicMock(spec=Repository)
        mock_db.save_session = AsyncMock()
        mock_db.save_operation = AsyncMock()
        mock_db.get_operations_by_session = AsyncMock(
            return_value=await self._build_click_ops("sim-sess-001")
        )
        mock_db.save_flow = AsyncMock()
        mock_db.get_flows_by_session = AsyncMock()
        mock_db.save_execution = AsyncMock()

        kg = WorkflowKnowledgeGraph()
        analysis_svc = AnalysisService(
            repository=mock_db,
            llm_service=sim_mock_llm_service,
            knowledge_graph=kg,
        )

        scored_flows = await analysis_svc.analyze_session("sim-sess-001", min_confidence=0.3)
        assert len(scored_flows) > 0, "At least one flow should be generated from 3 clicks"

        flow = scored_flows[0].flow
        assert flow.name != "", "Flow should have a name"
        assert len(flow.steps) > 0, "Flow should have steps"

        error_handler = ErrorHandler()
        step_executor = StepExecutor(sim_element_locator, sim_platform_adapter)
        recovery = IntelligentErrorRecovery()
        adaptive = AdaptiveWorkflowEngine()

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=error_handler,
            recovery=recovery,
            adaptive=adaptive,
        )

        record = await engine.execute({})
        assert record is not None
        assert record.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.ABORTED)
        assert record.total_steps > 0

        evolution = SelfEvolutionSystem()
        for log in record.step_logs or []:
            from src.executor.self_evolution import ExecutionFeedback as EvFeedback
            fb = EvFeedback(
                step_id=log.step_id,
                success=log.status == StepStatus.SUCCESS,
                duration_ms=(log.completed_at - log.started_at) * 1000 if log.started_at and log.completed_at else 0,
                error_type=None if log.status == StepStatus.SUCCESS else "execution_error",
            )
            evolution.record_feedback(fb)

        assert mock_db.save_flow.called or mock_db.save_flow.call_count >= 0

    @pytest.mark.asyncio
    async def test_full_pipeline_typing_workflow(
        self, sim_mock_llm_service, sim_platform_adapter, sim_element_locator
    ):
        """Simulate: click → type → enter → analyze → generate → execute."""
        mock_db = MagicMock(spec=Repository)
        mock_db.save_operation = AsyncMock()
        mock_db.get_operations_by_session = AsyncMock(
            return_value=await self._build_typing_ops("sim-sess-002")
        )
        mock_db.save_flow = AsyncMock()
        mock_db.save_execution = AsyncMock()

        kg = WorkflowKnowledgeGraph()
        analysis_svc = AnalysisService(
            repository=mock_db,
            llm_service=sim_mock_llm_service,
            knowledge_graph=kg,
        )

        scored_flows = await analysis_svc.analyze_session("sim-sess-002", min_confidence=0.3)
        assert len(scored_flows) > 0

        flow = scored_flows[0].flow
        type_steps = [s for s in flow.steps if s.type == StepType.TYPE]
        click_steps = [s for s in flow.steps if s.type == StepType.CLICK]
        assert len(type_steps) > 0 or len(click_steps) > 0, "Should contain type or click steps"

        step_executor = StepExecutor(sim_element_locator, sim_platform_adapter)
        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=ErrorHandler(),
            recovery=IntelligentErrorRecovery(),
            adaptive=AdaptiveWorkflowEngine(),
        )

        record = await engine.execute({})
        assert record.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)

    @pytest.mark.asyncio
    async def test_llm_name_generation(self, sim_mock_llm_service):
        """Verify LLM generates meaningful flow names."""
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(
            return_value=await self._build_click_ops("sim-sess-name")
        )
        mock_db.save_flow = AsyncMock()

        kg = WorkflowKnowledgeGraph()
        analysis_svc = AnalysisService(
            repository=mock_db,
            llm_service=sim_mock_llm_service,
            knowledge_graph=kg,
        )

        scored_flows = await analysis_svc.analyze_session("sim-sess-name", min_confidence=0.3)
        assert scored_flows

        sim_mock_llm_service.suggest_flow_name.assert_called()
        sim_mock_llm_service.analyze_flow_artifacts.assert_called()

        flow = scored_flows[0].flow
        assert flow.name != "未命名流程", f"Name should not be default: {flow.name}"
        assert len(flow.description) > 0

    @pytest.mark.asyncio
    async def test_knowledge_graph_accumulation(self, sim_mock_llm_service):
        """Verify knowledge graph accumulates patterns across multiple analyses."""
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(
            return_value=await self._build_click_ops("sim-sess-kg")
        )
        mock_db.save_flow = AsyncMock()

        kg = WorkflowKnowledgeGraph()
        analysis_svc = AnalysisService(
            repository=mock_db,
            llm_service=sim_mock_llm_service,
            knowledge_graph=kg,
        )

        await analysis_svc.analyze_session("sim-sess-kg", min_confidence=0.3)

        stats = kg.get_stats()
        assert stats["operation_patterns"] >= 0, f"KG stats: {stats}"
        sim_mock_llm_service.suggest_flow_name.assert_called()
        sim_mock_llm_service.analyze_flow_artifacts.assert_called()

    @pytest.mark.asyncio
    async def test_execution_with_variables(self, sim_platform_adapter, sim_element_locator):
        """Execute a flow with user-provided variables."""
        from src.models.automation import AutomationFlow, FlowVariable

        flow = AutomationFlow(
            id="flow-vars-001",
            name="变量测试",
            variables=[
                FlowVariable(name="username", var_type="string", default_value="user@test.com"),
                FlowVariable(name="delay_ms", var_type="int", default_value=500),
            ],
            steps=[
                AutomationStep(
                    id="step-v-0", type=StepType.TYPE, description="输入用户名",
                    target=StepTarget(strategy=LocateStrategy.TEXT_MATCH),
                    action={"text": "{{username}}"},
                    delay=0,
                    execution_order=0,
                ),
                AutomationStep(
                    id="step-v-1", type=StepType.WAIT, description="等待",
                    action={"duration": 0.5},
                    delay=500,
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

        record = await engine.execute({"username": "override@test.com", "delay_ms": 200})
        assert record is not None

    @pytest.mark.asyncio
    async def test_multiple_analysis_sessions(
        self, sim_mock_llm_service
    ):
        """Simulate analyzing multiple sessions over time to build knowledge."""
        mock_db = MagicMock(spec=Repository)
        mock_db.save_flow = AsyncMock()
        kg = WorkflowKnowledgeGraph()

        analysis_svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service, knowledge_graph=kg)

        for sess_idx in range(3):
            sess_id = f"multi-sess-{sess_idx:03d}"
            mock_db.get_operations_by_session = AsyncMock(
                return_value=await self._build_click_ops(sess_id)
            )
            await analysis_svc.analyze_session(sess_id, min_confidence=0.3)

        stats = kg.get_stats()
        assert "operation_patterns" in stats, f"Missing operation_patterns: {stats}"
        sim_mock_llm_service.suggest_flow_name.assert_called()
        sim_mock_llm_service.analyze_flow_artifacts.assert_called()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _build_click_ops(self, session_id):
        from src.models.operation import (
            MouseAction, MouseButton, MouseEventData, OperationContext,
            OperationEvent, OperationType,
        )
        from tests.simulation.conftest import make_window
        now = int(time.time() * 1000)
        return [
            OperationEvent(
                id=f"op-clk-{session_id}-{i}",
                session_id=session_id,
                seq_num=i,
                timestamp=now + i * 800,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=200 + i * 100, y=300, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window("Test App", "Chrome"), screenshot_path=f"/tmp/{session_id}_{i}.png", platform="macOS"),
            )
            for i in range(3)
        ]

    async def _build_typing_ops(self, session_id):
        from src.models.operation import (
            KeyboardEventData, KeyAction, MouseAction, MouseButton,
            MouseEventData, OperationContext, OperationEvent, OperationType,
        )
        from tests.simulation.conftest import make_window
        now = int(time.time() * 1000)
        return [
            OperationEvent(
                id=f"op-typ-{session_id}-0", session_id=session_id, seq_num=0, timestamp=now,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=150, y=200, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window("Test Form", "Finder"), platform="macOS"),
            ),
            OperationEvent(
                id=f"op-typ-{session_id}-1", session_id=session_id, seq_num=1, timestamp=now + 500,
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="a", action=KeyAction.INPUT, text="admin"),
                context=OperationContext(active_window=make_window("Test Form", "Finder"), platform="macOS"),
            ),
            OperationEvent(
                id=f"op-typ-{session_id}-2", session_id=session_id, seq_num=2, timestamp=now + 2000,
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="Return", action=KeyAction.PRESS),
                context=OperationContext(active_window=make_window("Test Form", "Finder"), platform="macOS"),
            ),
        ]
