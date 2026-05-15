import time

import pytest

from src.collector.snapshot import DesktopSnapshot
from src.models.desktop import DesktopState, Rect, UIElement, WindowInfo
from src.models.operation import (
    KeyboardEventData,
    KeyAction,
    MouseAction,
    MouseButton,
    MouseEventData,
    OperationContext,
    OperationEvent,
    OperationType,
)
from src.models.session import Session
from src.timeline.service import TimelineMemoryService


class _FakeSnapshotManager:
    def __init__(self, snapshots):
        self._snapshots = snapshots

    def list_snapshots(self, session_id=None, limit=50):
        snapshots = self._snapshots
        if session_id:
            snapshots = [snapshot for snapshot in snapshots if snapshot.session_id == session_id]
        return snapshots[:limit]

    def get_screenshot_bytes(self, snapshot_id):
        for snapshot in self._snapshots:
            if snapshot.id == snapshot_id:
                return snapshot.screenshot_bytes
        return None


def _build_snapshot(timestamp_ms):
    state = DesktopState(
        active_window=WindowInfo(
            window_id=1,
            title="Chrome - 财务报表",
            app_name="Google Chrome",
            pid=42,
            bounds=Rect(x=0, y=0, width=1440, height=900),
            url="https://example.com/reports",
        ),
        focused_element=UIElement(
            role="button",
            title="导出报表",
            identifier="export-report",
        ),
        timestamp=timestamp_ms,
    )
    return DesktopSnapshot(
        snapshot_id="snap-1",
        state=state,
        session_id="session-1",
        screenshot_bytes=b"fake-png-bytes",
    )


def _build_operation(timestamp_ms):
    return OperationEvent(
        id="op-1",
        session_id="session-1",
        seq_num=1,
        timestamp=timestamp_ms,
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(
            key="text_input",
            action=KeyAction.INPUT,
            text="导出财务报表",
        ),
        context=OperationContext(
            active_window=WindowInfo(
                window_id=2,
                title="Chrome - 财务报表",
                app_name="Google Chrome",
                pid=42,
                bounds=Rect(x=0, y=0, width=1440, height=900),
                url="https://example.com/reports",
            ),
            focused_element=UIElement(
                role="textbox",
                title="搜索框",
                identifier="report-search",
            ),
            platform="web",
        ),
    )


@pytest.mark.asyncio
async def test_query_timeline_merges_snapshots_and_operations(monkeypatch):
    now_ms = int(time.time() * 1000)
    service = TimelineMemoryService()
    service._snapshot_manager = _FakeSnapshotManager([_build_snapshot(now_ms)])

    async def _load_sessions(session_id):
        return {"session-1": Session(id="session-1", name="最近录制")}

    async def _load_operations(session_ids):
        return {"session-1": [_build_operation(now_ms - 1000)]}

    monkeypatch.setattr(service, "_load_sessions", _load_sessions)
    monkeypatch.setattr(service, "_load_operations", _load_operations)

    items = await service.query_timeline(limit=5, lookback_minutes=30)

    assert len(items) == 2
    assert items[0]["kind"] == "snapshot"
    assert items[0]["preview_url"] == "/api/memory/snapshots/snap-1/image"
    assert items[1]["kind"] == "operation"
    assert items[1]["session_name"] == "最近录制"


@pytest.mark.asyncio
async def test_query_timeline_filters_by_keyword(monkeypatch):
    now_ms = int(time.time() * 1000)
    service = TimelineMemoryService()
    service._snapshot_manager = _FakeSnapshotManager([_build_snapshot(now_ms)])

    async def _load_sessions(session_id):
        return {"session-1": Session(id="session-1", name="最近录制")}

    async def _load_operations(session_ids):
        return {"session-1": [_build_operation(now_ms - 1000)]}

    monkeypatch.setattr(service, "_load_sessions", _load_sessions)
    monkeypatch.setattr(service, "_load_operations", _load_operations)

    items = await service.query_timeline(query="导出报表", limit=5, lookback_minutes=30)

    assert len(items) == 2
    assert all(item["score"] > 0 for item in items)
    assert any(item["kind"] == "snapshot" for item in items)
    assert any(item["kind"] == "operation" for item in items)