import asyncio
import json
import logging
import uuid
from pathlib import Path

from src.config import settings
from src.collector.service import DesktopCollector
from src.models.desktop import DesktopState, Rect

logger = logging.getLogger(__name__)


class DesktopSnapshot:
    def __init__(
        self,
        snapshot_id: str,
        state: DesktopState,
        session_id: str | None = None,
        screenshot_bytes: bytes | None = None,
        screenshot_file_path: str | None = None,
    ):
        self.id = snapshot_id
        self.state = state
        self.session_id = session_id
        self.timestamp = state.timestamp
        self.screenshot_bytes = screenshot_bytes
        self.screenshot_file_path = screenshot_file_path


class SnapshotManager:
    def __init__(self, collector: DesktopCollector | None = None, storage_dir: str = ""):
        self._collector = collector or DesktopCollector()
        self._snapshots: dict[str, DesktopSnapshot] = {}
        self._capture_tasks: dict[str, asyncio.Task] = {}
        self._storage_dir = Path(storage_dir) if storage_dir else None
        if self._storage_dir:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            self._load_persisted_snapshots()

    async def take_snapshot(self, session_id: str | None = None) -> DesktopSnapshot:
        snapshot_id = str(uuid.uuid4())
        state_task = asyncio.create_task(self._collector.collect_full_state())
        screenshot_task = asyncio.create_task(self._collector.capture_screen())
        await asyncio.gather(state_task, screenshot_task, return_exceptions=True)

        state = state_task.result() if not state_task.exception() else DesktopState(timestamp=0)
        screenshot_bytes = screenshot_task.result() if not screenshot_task.exception() else None
        if screenshot_bytes:
            state.screenshot_path = f"snapshot://{snapshot_id}"

        screenshot_file_path = str(self._image_path(snapshot_id)) if screenshot_bytes and self._storage_dir else None
        snapshot = DesktopSnapshot(
            snapshot_id,
            state,
            session_id,
            screenshot_bytes=screenshot_bytes,
            screenshot_file_path=screenshot_file_path,
        )
        self._snapshots[snapshot_id] = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> DesktopSnapshot | None:
        return self._snapshots.get(snapshot_id)

    def list_snapshots(self, session_id: str | None = None, limit: int = 50) -> list[DesktopSnapshot]:
        snapshots = list(self._snapshots.values())
        if session_id:
            snapshots = [s for s in snapshots if s.session_id == session_id]
        snapshots.sort(key=lambda s: s.timestamp, reverse=True)
        return snapshots[:limit]

    def get_screenshot_bytes(self, snapshot_id: str) -> bytes | None:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            return None
        if snapshot.screenshot_bytes is None and snapshot.screenshot_file_path:
            try:
                snapshot.screenshot_bytes = Path(snapshot.screenshot_file_path).read_bytes()
            except OSError as e:
                logger.warning("Failed to read persisted snapshot image %s: %s", snapshot.screenshot_file_path, e)
                return None
        return snapshot.screenshot_bytes

    def start_periodic_capture(self, interval: float = 5.0, session_id: str | None = None) -> None:
        key = self._capture_key(session_id)
        existing = self._capture_tasks.get(key)
        if existing is not None and not existing.done():
            return
        self._capture_tasks[key] = asyncio.ensure_future(self._periodic_loop(interval, session_id))

    def stop_periodic_capture(self, session_id: str | None = None) -> None:
        if session_id is None:
            keys = list(self._capture_tasks.keys())
        else:
            keys = [self._capture_key(session_id)]

        for key in keys:
            task = self._capture_tasks.pop(key, None)
            if task is not None and not task.done():
                task.cancel()

    async def _periodic_loop(self, interval: float, session_id: str | None) -> None:
        while True:
            try:
                await self.take_snapshot(session_id)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic capture failed: {e}")
                await asyncio.sleep(interval)

    @staticmethod
    def _capture_key(session_id: str | None) -> str:
        return session_id or "__global__"

    async def capture_screenshot(self, snapshot_id: str, region: Rect | None = None) -> bytes:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot and region is None:
            cached = self.get_screenshot_bytes(snapshot_id)
            if cached:
                return cached

        data = await self._collector.capture_screen(region)
        if snapshot and region is None:
            snapshot.screenshot_bytes = data
            if self._storage_dir:
                snapshot.screenshot_file_path = str(self._image_path(snapshot_id))
            snapshot.state.screenshot_path = f"snapshot://{snapshot_id}"
            self._persist_snapshot(snapshot)
        return data

    def delete_snapshot(self, snapshot_id: str) -> bool:
        snapshot = self._snapshots.pop(snapshot_id, None)
        if snapshot is not None:
            self._delete_persisted_snapshot(snapshot_id, snapshot.screenshot_file_path)
            return True
        return False

    def clear_snapshots(self, session_id: str | None = None) -> int:
        if session_id:
            to_delete = [sid for sid, s in self._snapshots.items() if s.session_id == session_id]
            for sid in to_delete:
                self.delete_snapshot(sid)
            return len(to_delete)
        count = len(self._snapshots)
        for snapshot_id in list(self._snapshots.keys()):
            self.delete_snapshot(snapshot_id)
        return count

    def _metadata_path(self, snapshot_id: str) -> Path:
        if self._storage_dir is None:
            raise RuntimeError("storage_dir is not configured")
        return self._storage_dir / f"{snapshot_id}.json"

    def _image_path(self, snapshot_id: str) -> Path:
        if self._storage_dir is None:
            raise RuntimeError("storage_dir is not configured")
        return self._storage_dir / f"{snapshot_id}.png"

    def _persist_snapshot(self, snapshot: DesktopSnapshot) -> None:
        if self._storage_dir is None:
            return

        screenshot_name = None
        if snapshot.screenshot_bytes:
            image_path = self._image_path(snapshot.id)
            image_path.write_bytes(snapshot.screenshot_bytes)
            snapshot.screenshot_file_path = str(image_path)
            screenshot_name = image_path.name

        payload = {
            "id": snapshot.id,
            "session_id": snapshot.session_id,
            "state": snapshot.state.model_dump(mode="json"),
            "screenshot_file": screenshot_name,
        }
        self._metadata_path(snapshot.id).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_persisted_snapshots(self) -> None:
        if self._storage_dir is None:
            return

        for metadata_path in self._storage_dir.glob("*.json"):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                snapshot_id = payload.get("id") or metadata_path.stem
                state = DesktopState.model_validate(payload.get("state") or {})
                screenshot_file = payload.get("screenshot_file")
                screenshot_path = self._storage_dir / screenshot_file if screenshot_file else None
                if screenshot_path and screenshot_path.exists():
                    state.screenshot_path = f"snapshot://{snapshot_id}"
                snapshot = DesktopSnapshot(
                    snapshot_id,
                    state,
                    payload.get("session_id"),
                    screenshot_bytes=None,
                    screenshot_file_path=str(screenshot_path) if screenshot_path and screenshot_path.exists() else None,
                )
                self._snapshots[snapshot_id] = snapshot
            except Exception as e:
                logger.warning("Failed to load persisted snapshot %s: %s", metadata_path, e)

    def _delete_persisted_snapshot(self, snapshot_id: str, screenshot_file_path: str | None) -> None:
        if self._storage_dir is None:
            return

        metadata_path = self._metadata_path(snapshot_id)
        if metadata_path.exists():
            metadata_path.unlink()

        if screenshot_file_path:
            screenshot_path = Path(screenshot_file_path)
            if screenshot_path.exists():
                screenshot_path.unlink()


_snapshot_manager: SnapshotManager | None = None


def get_snapshot_manager() -> SnapshotManager:
    global _snapshot_manager
    if _snapshot_manager is None:
        _snapshot_manager = SnapshotManager(storage_dir=str(Path(settings.DATA_DIR) / "timeline_snapshots"))
    return _snapshot_manager
