import asyncio

import pytest

from src.collector.snapshot import DesktopSnapshot, SnapshotManager
from src.models.desktop import DesktopState


class _FakeCollector:
    async def collect_full_state(self):
        return DesktopState(timestamp=123456789)

    async def capture_screen(self, region=None):
        return b"fake-png-bytes"


@pytest.mark.asyncio
async def test_snapshot_manager_persists_snapshots(tmp_path):
    manager = SnapshotManager(collector=_FakeCollector(), storage_dir=str(tmp_path))

    snapshot = await manager.take_snapshot(session_id="session-1")

    reloaded_manager = SnapshotManager(collector=_FakeCollector(), storage_dir=str(tmp_path))
    reloaded_snapshot = reloaded_manager.get_snapshot(snapshot.id)

    assert reloaded_snapshot is not None
    assert reloaded_snapshot.session_id == "session-1"
    assert reloaded_snapshot.timestamp == 123456789
    assert reloaded_manager.get_screenshot_bytes(snapshot.id) == b"fake-png-bytes"


@pytest.mark.asyncio
async def test_snapshot_manager_stops_periodic_capture_per_session(tmp_path):
    manager = SnapshotManager(collector=_FakeCollector(), storage_dir=str(tmp_path))
    calls: list[str | None] = []

    async def _fake_take_snapshot(session_id=None):
        calls.append(session_id)
        return DesktopSnapshot(
            snapshot_id=f"snap-{len(calls)}",
            session_id=session_id,
            state=DesktopState(timestamp=123456789 + len(calls)),
            screenshot_bytes=b"fake-png-bytes",
        )

    manager.take_snapshot = _fake_take_snapshot  # type: ignore[method-assign]
    manager.start_periodic_capture(0.01, session_id="session-a")
    manager.start_periodic_capture(0.01, session_id="session-b")

    try:
        await asyncio.sleep(0.03)
        manager.stop_periodic_capture(session_id="session-a")
        session_a_count = sum(1 for item in calls if item == "session-a")
        await asyncio.sleep(0.03)
    finally:
        manager.stop_periodic_capture()

    assert session_a_count > 0
    assert sum(1 for item in calls if item == "session-a") == session_a_count
    assert sum(1 for item in calls if item == "session-b") > 0