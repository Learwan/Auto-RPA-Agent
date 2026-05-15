"""Simulation tests for the recording module - event capture and processing."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestRecordingSimulation:
    """Simulation of recording events from mouse, keyboard, window, etc."""

    def test_click_event_creation(self, sim_session_id):
        """Creating a mouse click OperationEvent."""
        op = OperationEvent(
            id="rec-sim-001",
            session_id=sim_session_id,
            seq_num=0,
            timestamp=int(time.time() * 1000),
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=500, y=400, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                active_window=make_window("Test Window", "TestApp"),
                screenshot_path="/tmp/sim/recording_click.png",
                platform="macOS",
            ),
        )

        assert op.type == OperationType.MOUSE_CLICK
        assert isinstance(op.data, MouseEventData)
        assert op.data.x == 500
        assert op.data.y == 400
        assert op.data.button == MouseButton.LEFT
        assert op.context.active_window.title == "Test Window"

    def test_double_click_event(self, sim_session_id):
        """Creating a double-click event."""
        op = OperationEvent(
            id="rec-sim-002",
            session_id=sim_session_id,
            seq_num=1,
            timestamp=int(time.time() * 1000),
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=200, y=150, button=MouseButton.LEFT, action=MouseAction.DOUBLE_CLICK),
            context=OperationContext(platform="macOS"),
        )

        assert op.data.action == MouseAction.DOUBLE_CLICK

    def test_right_click_event(self, sim_session_id):
        """Creating a right-click event."""
        op = OperationEvent(
            id="rec-sim-003",
            session_id=sim_session_id,
            seq_num=2,
            timestamp=int(time.time() * 1000),
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=800, y=600, button=MouseButton.RIGHT, action=MouseAction.RIGHT_CLICK),
            context=OperationContext(platform="macOS"),
        )

        assert op.data.button == MouseButton.RIGHT
        assert op.data.action == MouseAction.RIGHT_CLICK

    def test_scroll_event(self, sim_session_id):
        """Creating a scroll event."""
        op = OperationEvent(
            id="rec-sim-004",
            session_id=sim_session_id,
            seq_num=3,
            timestamp=int(time.time() * 1000),
            type=OperationType.MOUSE_SCROLL,
            data=MouseEventData(
                x=400, y=300, button=MouseButton.LEFT,
                action=MouseAction.SCROLL_DOWN, scroll_dy=-3,
            ),
            context=OperationContext(platform="macOS"),
        )

        assert op.type == OperationType.MOUSE_SCROLL
        assert op.data.action == MouseAction.SCROLL_DOWN
        assert op.data.scroll_dy == -3

    def test_drag_event(self, sim_session_id):
        """Creating a drag event."""
        op = OperationEvent(
            id="rec-sim-005",
            session_id=sim_session_id,
            seq_num=4,
            timestamp=int(time.time() * 1000),
            type=OperationType.MOUSE_DRAG,
            data=MouseEventData(
                x=100, y=100, button=MouseButton.LEFT, action=MouseAction.DRAG_START,
                drag_start_x=100, drag_start_y=100,
            ),
            context=OperationContext(platform="macOS"),
        )

        assert op.type == OperationType.MOUSE_DRAG
        assert op.data.drag_start_x == 100
        assert op.data.drag_start_y == 100

    def test_key_press_event(self, sim_session_id):
        """Creating a key press event."""
        op = OperationEvent(
            id="rec-sim-006",
            session_id=sim_session_id,
            seq_num=5,
            timestamp=int(time.time() * 1000),
            type=OperationType.KEY_PRESS,
            data=KeyboardEventData(key="Enter", key_code=36, action=KeyAction.PRESS),
            context=OperationContext(platform="macOS"),
        )

        assert op.type == OperationType.KEY_PRESS
        assert op.data.key == "Enter"
        assert op.data.action == KeyAction.PRESS

    def test_key_input_event(self, sim_session_id):
        """Creating a key input event (text typing)."""
        op = OperationEvent(
            id="rec-sim-007",
            session_id=sim_session_id,
            seq_num=6,
            timestamp=int(time.time() * 1000),
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(key="a", action=KeyAction.INPUT, text="hello world"),
            context=OperationContext(platform="macOS"),
        )

        assert op.data.text == "hello world"
        assert op.data.action == KeyAction.INPUT

    def test_hotkey_event(self, sim_session_id):
        """Creating a hotkey event (modifier + key)."""
        op = OperationEvent(
            id="rec-sim-008",
            session_id=sim_session_id,
            seq_num=7,
            timestamp=int(time.time() * 1000),
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(
                key="c", modifiers=["command"], action=KeyAction.HOTKEY,
            ),
            context=OperationContext(platform="macOS"),
        )

        assert op.data.modifiers == ["command"]
        assert op.data.action == KeyAction.HOTKEY

    def test_window_switch_event(self, sim_session_id):
        """Creating a window switch event."""
        op = OperationEvent(
            id="rec-sim-009",
            session_id=sim_session_id,
            seq_num=8,
            timestamp=int(time.time() * 1000),
            type=OperationType.WINDOW_SWITCH,
            data=WindowEventData(
                from_window=make_window("Browser", "Chrome"),
                to_window=make_window("Editor", "VS Code"),
                method="cmd+tab",
            ),
            context=OperationContext(platform="macOS"),
        )

        assert op.type == OperationType.WINDOW_SWITCH
        assert op.data.to_window.app_name == "VS Code"
        assert op.data.method == "cmd+tab"

    def test_sensitive_input_handling(self, sim_session_id):
        """Sensitive keyboard input (passwords) should be flagged."""
        op = OperationEvent(
            id="rec-sim-010",
            session_id=sim_session_id,
            seq_num=9,
            timestamp=int(time.time() * 1000),
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(key="p", action=KeyAction.INPUT, text="myPassword123", is_sensitive=True),
            context=OperationContext(platform="macOS"),
        )

        assert op.data.is_sensitive is True

    def test_operation_sequence_ordering(self, sim_click_sequence):
        """Events should have monotonically increasing sequence numbers."""
        for i, op in enumerate(sim_click_sequence):
            assert op.seq_num == i
            if i > 0:
                assert op.timestamp >= sim_click_sequence[i - 1].timestamp

    def test_mixed_sequence_types(self, sim_mixed_sequence):
        """Mixed workflow should contain multiple event types."""
        types = {op.type for op in sim_mixed_sequence}
        expected = {
            OperationType.MOUSE_CLICK,
            OperationType.KEY_INPUT,
            OperationType.WINDOW_SWITCH,
        }
        assert types & expected, f"Expected at least some of {expected} in {types}"

    def test_large_batch_creation(self, sim_session_id):
        """Create 500 click events efficiently."""
        ops = []
        now = int(time.time() * 1000)
        for i in range(500):
            ops.append(OperationEvent(
                id=f"batch-{i:04d}",
                session_id=sim_session_id,
                seq_num=i,
                timestamp=now + i * 100,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=i % 1920, y=i % 1080, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(platform="macOS"),
            ))

        assert len(ops) == 500
        assert ops[0].seq_num == 0
        assert ops[-1].seq_num == 499
