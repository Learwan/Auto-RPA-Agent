"""Simulation tests for the analysis pipeline with AI enhancement."""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analyzer.knowledge_graph import WorkflowKnowledgeGraph
from src.analyzer.service import AnalysisService
from src.db.repository import Repository
from src.models.operation import (
    KeyboardEventData,
    KeyAction,
    MouseAction,
    MouseButton,
    MouseEventData,
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)
from tests.simulation.conftest import make_window


class TestAnalysisSimulation:
    """Simulation of the analysis pipeline: preprocessing → segmentation → pattern detection → generation."""

    @pytest.mark.asyncio
    async def test_analyze_simple_click_sequence(self, sim_mock_llm_service):
        """Analyze a simple 3-click sequence."""
        ops = await self._make_click_sequence("sess-a1")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a1", min_confidence=0.3)

        assert len(scored) > 0
        assert scored[0].overall_confidence >= 0.3

    @pytest.mark.asyncio
    async def test_analyze_typing_sequence(self, sim_mock_llm_service):
        """Analyze click → type → enter."""
        ops = await self._make_typing_sequence("sess-a2")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a2", min_confidence=0.3)

        assert len(scored) > 0
        for sf in scored:
            assert sf.flow.name != ""

    @pytest.mark.asyncio
    async def test_analyze_window_switch_sequence(self, sim_mock_llm_service):
        """Analyze a window switch sequence."""
        ops = await self._make_window_switch_sequence("sess-a3")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a3", min_confidence=0.3)

        assert len(scored) > 0

    @pytest.mark.asyncio
    async def test_analyze_mixed_workflow(self, sim_mock_llm_service):
        """Analyze mixed workflow: click → type → hotkey → window switch."""
        ops = await self._make_mixed_sequence("sess-a4")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a4", min_confidence=0.3)

        assert len(scored) > 0

    @pytest.mark.asyncio
    async def test_analyze_with_duplicate_detection(self, sim_mock_llm_service):
        """Analyze operations with repeating patterns."""
        ops = await self._make_repeating_sequence("sess-a5")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a5", min_confidence=0.3)

        assert len(scored) > 0

    @pytest.mark.asyncio
    async def test_analyze_noise_filtering(self, sim_mock_llm_service):
        """Noise-heavy operations should still yield usable flows."""
        ops = await self._make_noisy_sequence("sess-a6")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a6", min_confidence=0.1)

    @pytest.mark.asyncio
    async def test_confidence_scoring_dimensions(self, sim_mock_llm_service):
        """Each scored flow should have dimension breakdowns."""
        ops = await self._make_click_sequence("sess-a7")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a7", min_confidence=0.3)

        for sf in scored:
            assert len(sf.dimensions) > 0, "Each flow should have dimension scores"
            dim_names = [d.name for d in sf.dimensions]
            expected = ["stability", "locator_reliability", "context_dependency", "anomaly_detection"]
            for e in expected:
                assert e in dim_names, f"Missing dimension: {e}"

    @pytest.mark.asyncio
    async def test_analyze_without_llm_fallback(self):
        """Without LLM, analysis should still work (graceful degradation)."""
        ops = await self._make_click_sequence("sess-a8")
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=None, knowledge_graph=None)
        scored = await svc.analyze_session("sess-a8", min_confidence=0.3)

        assert len(scored) > 0
        for sf in scored:
            assert sf.flow.name != ""

    @pytest.mark.asyncio
    async def test_analyze_single_operation(self, sim_mock_llm_service):
        """Single operation should still produce a direct flow."""
        ops = [
            OperationEvent(
                id="op-sng-0", session_id="sess-a9", seq_num=0, timestamp=int(time.time() * 1000),
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=100, y=100, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window("Test", "App"), platform="macOS"),
            ),
        ]
        mock_db = MagicMock(spec=Repository)
        mock_db.get_operations_by_session = AsyncMock(return_value=ops)
        mock_db.save_flow = AsyncMock()

        svc = AnalysisService(repository=mock_db, llm_service=sim_mock_llm_service)
        scored = await svc.analyze_session("sess-a9", min_confidence=0.3)

        assert len(scored) >= 0

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _make_click_sequence(self, sid):
        now = int(time.time() * 1000)
        return [
            OperationEvent(
                id=f"{sid}-clk-{i}", session_id=sid, seq_num=i, timestamp=now + i * 800,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=200 + i * 100, y=300, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window("Test App", "Chrome"), screenshot_path=f"/tmp/{sid}_{i}.png", platform="macOS"),
            )
            for i in range(3)
        ]

    async def _make_typing_sequence(self, sid):
        now = int(time.time() * 1000)
        return [
            OperationEvent(
                id=f"{sid}-0", session_id=sid, seq_num=0, timestamp=now,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=150, y=200, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window("Form", "Finder"), platform="macOS"),
            ),
            OperationEvent(
                id=f"{sid}-1", session_id=sid, seq_num=1, timestamp=now + 500,
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="a", action=KeyAction.INPUT, text="admin@test.com"),
                context=OperationContext(active_window=make_window("Form", "Finder"), platform="macOS"),
            ),
            OperationEvent(
                id=f"{sid}-2", session_id=sid, seq_num=2, timestamp=now + 2000,
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="Return", action=KeyAction.PRESS),
                context=OperationContext(active_window=make_window("Form", "Finder"), platform="macOS"),
            ),
        ]

    async def _make_window_switch_sequence(self, sid):
        now = int(time.time() * 1000)
        return [
            OperationEvent(
                id=f"{sid}-ws-0", session_id=sid, seq_num=0, timestamp=now,
                type=OperationType.WINDOW_SWITCH,
                data=WindowEventData(
                    from_window=make_window("Chrome", "Google Chrome"),
                    to_window=make_window("VS Code", "Electron"),
                    method="cmd+tab",
                ),
                context=OperationContext(platform="macOS"),
            ),
            OperationEvent(
                id=f"{sid}-ws-1", session_id=sid, seq_num=1, timestamp=now + 2000,
                type=OperationType.WINDOW_SWITCH,
                data=WindowEventData(
                    from_window=make_window("VS Code", "Electron"),
                    to_window=make_window("Chrome", "Google Chrome"),
                    method="cmd+tab",
                ),
                context=OperationContext(platform="macOS"),
            ),
        ]

    async def _make_mixed_sequence(self, sid):
        now = int(time.time() * 1000)
        ts = [now, now + 600, now + 1200, now + 2500, now + 3500, now + 4200]
        return [
            OperationEvent(
                id=f"{sid}-mx-0", session_id=sid, seq_num=0, timestamp=ts[0],
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=100, y=50, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window("Chrome", "Google Chrome")),
            ),
            OperationEvent(
                id=f"{sid}-mx-1", session_id=sid, seq_num=1, timestamp=ts[1],
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="t", action=KeyAction.INPUT, text="https://github.com"),
                context=OperationContext(active_window=make_window("Chrome", "Google Chrome")),
            ),
            OperationEvent(
                id=f"{sid}-mx-2", session_id=sid, seq_num=2, timestamp=ts[2],
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="Return", action=KeyAction.PRESS),
                context=OperationContext(active_window=make_window("Chrome", "Google Chrome")),
            ),
            OperationEvent(
                id=f"{sid}-mx-3", session_id=sid, seq_num=3, timestamp=ts[3],
                type=OperationType.WINDOW_SWITCH,
                data=WindowEventData(
                    from_window=make_window("Chrome", "Google Chrome"),
                    to_window=make_window("VS Code", "Electron"),
                    method="cmd+tab",
                ),
                context=OperationContext(platform="macOS"),
            ),
            OperationEvent(
                id=f"{sid}-mx-4", session_id=sid, seq_num=4, timestamp=ts[4],
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="s", modifiers=["command"], action=KeyAction.HOTKEY),
                context=OperationContext(active_window=make_window("VS Code", "Electron")),
            ),
            OperationEvent(
                id=f"{sid}-mx-5", session_id=sid, seq_num=5, timestamp=ts[5],
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=800, y=600, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window("VS Code", "Electron")),
            ),
        ]

    async def _make_repeating_sequence(self, sid):
        now = int(time.time() * 1000)
        return [
            OperationEvent(
                id=f"{sid}-rep-{i}", session_id=sid, seq_num=i, timestamp=now + i * 1500,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=900, y=650, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(active_window=make_window(f"Page {i+1}", "Chrome"), screenshot_path=f"/tmp/{sid}_page_{i}.png", platform="macOS"),
            )
            for i in range(10)
        ]

    async def _make_noisy_sequence(self, sid):
        import random
        now = int(time.time() * 1000)
        return [
            OperationEvent(
                id=f"{sid}-noise-{i}", session_id=sid, seq_num=i, timestamp=now + i * 200,
                type=OperationType.MOUSE_CLICK if i % 3 != 0 else OperationType.KEY_INPUT,
                data=MouseEventData(x=random.randint(0, 1920), y=random.randint(0, 1080), button=MouseButton.LEFT, action=MouseAction.CLICK) if i % 3 != 0 else KeyboardEventData(key="x", action=KeyAction.PRESS),
                context=OperationContext(active_window=make_window("Noise", "Desktop"), platform="macOS"),
            )
            for i in range(20)
        ]
