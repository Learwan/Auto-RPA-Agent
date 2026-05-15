from unittest.mock import AsyncMock, patch

import pytest

from src.models.desktop import Rect, WindowInfo
from src.models.operation import OperationType
from src.recorder.window_recorder import WindowRecorder


def _make_window(title: str, url: str | None) -> WindowInfo:
    return WindowInfo(
        window_id="demo-window",
        title=title,
        app_name="DemoApp",
        pid=7,
        bounds=Rect(x=0, y=0, width=1280, height=720),
        is_active=True,
        is_visible=True,
        url=url,
    )


@pytest.mark.asyncio
async def test_check_active_window_tolerates_asyncmock_adapter_metadata():
    events = []
    recorder = WindowRecorder()
    recorder._running = True
    recorder._session_id = "same-app-smoke"
    recorder._callback = events.append
    recorder._adapter = AsyncMock()
    recorder._adapter.get_active_window = AsyncMock(
        side_effect=[
            _make_window("Inbox", "demo://mail/inbox"),
            _make_window("Drafts", "demo://mail/drafts"),
        ]
    )

    with patch("src.recorder.window_recorder.time.time", side_effect=[100.0, 100.2, 100.2, 100.4]):
        await recorder._check_active_window()
        await recorder._check_active_window()

    switch_events = [event for event in events if event.type == OperationType.WINDOW_SWITCH]
    assert len(switch_events) == 2
    assert switch_events[1].data.from_window.title == "Inbox"
    assert switch_events[1].data.to_window.title == "Drafts"
    assert switch_events[1].context is not None
    assert switch_events[1].context.active_window.url == "demo://mail/drafts"
    assert switch_events[1].context.focused_element is None
    assert switch_events[1].context.platform == "desktop"
