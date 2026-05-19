from __future__ import annotations

from pynput import mouse

from src.models.operation import MouseAction, OperationContext, OperationType
from src.recorder.mouse_recorder import MouseRecorder


def test_drag_release_emits_drag_end_event(monkeypatch):
    recorder = MouseRecorder()
    events = []

    recorder._running = True
    recorder._session_id = "session-test"
    recorder._callback = events.append

    monkeypatch.setattr(recorder, "_get_element_at", lambda x, y: None)
    monkeypatch.setattr(recorder, "_build_context", lambda x, y, element=None: OperationContext(platform="desktop"))

    recorder._on_click(100, 120, mouse.Button.left, True)
    recorder._on_click(132, 156, mouse.Button.left, False)

    assert len(events) == 1
    event = events[0]
    assert event.type == OperationType.MOUSE_DRAG
    assert event.data.action == MouseAction.DRAG_END
    assert event.data.drag_start_x == 100
    assert event.data.drag_start_y == 120
    assert event.data.x == 132
    assert event.data.y == 156