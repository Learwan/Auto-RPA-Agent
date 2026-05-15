from fastapi import APIRouter, HTTPException

from src.collector.service import DesktopCollector
from src.collector.snapshot import get_snapshot_manager
from src.models.desktop import DesktopState, ProcessInfo, Rect, WindowInfo
from src.recorder.event_bus import EventBus

router = APIRouter()

_collector = DesktopCollector()
_snapshot_manager = get_snapshot_manager()
_event_bus = EventBus()


@router.get("/windows", response_model=list[WindowInfo])
async def get_windows():
    return await _collector.collect_windows()


@router.get("/processes", response_model=list[ProcessInfo])
async def get_processes():
    return await _collector.collect_processes()


@router.get("/active-element")
async def get_active_element():
    element = await _collector.collect_focused_element()
    return {"element": element}


@router.get("/state", response_model=DesktopState)
async def get_desktop_state():
    return await _collector.collect_full_state()


@router.post("/snapshot")
async def take_snapshot(session_id: str | None = None):
    if session_id is not None and (not isinstance(session_id, str) or len(session_id.strip()) == 0):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    snapshot = await _snapshot_manager.take_snapshot(session_id)
    return {"id": snapshot.id, "timestamp": snapshot.timestamp, "session_id": snapshot.session_id}


@router.get("/snapshot/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    snapshot = _snapshot_manager.get_snapshot(snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"id": snapshot.id, "state": snapshot.state.model_dump(), "session_id": snapshot.session_id}


@router.get("/snapshots")
async def list_snapshots(session_id: str | None = None, limit: int = 50):
    snapshots = _snapshot_manager.list_snapshots(session_id, limit)
    return [{"id": s.id, "timestamp": s.timestamp, "session_id": s.session_id} for s in snapshots]


@router.post("/periodic-capture/start")
async def start_periodic_capture(interval: float = 5.0, session_id: str | None = None):
    _snapshot_manager.start_periodic_capture(interval, session_id)
    return {"status": "started", "interval": interval}


@router.post("/periodic-capture/stop")
async def stop_periodic_capture():
    _snapshot_manager.stop_periodic_capture()
    return {"status": "stopped"}


@router.get("/screenshot")
async def get_screenshot(x: int = 0, y: int = 0, width: int = 0, height: int = 0):
    region = None
    if width > 0 and height > 0:
        region = Rect(x=x, y=y, width=width, height=height)
    data = await _collector.capture_screen(region)
    import base64

    return {"screenshot_base64": base64.b64encode(data).decode("utf-8"), "size": len(data)}


@router.post("/event")
async def send_event(event: dict, session_id: str | None = None):
    if not isinstance(event, dict) or "type" not in event:
        raise HTTPException(status_code=400, detail="Invalid event: must contain 'type'")
    await _event_bus.send_event(event, session_id)
    return {"status": "ok"}
