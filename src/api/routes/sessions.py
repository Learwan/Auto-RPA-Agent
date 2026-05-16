import asyncio
import json
import logging
import re

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.analyzer.personal_workflow_copilot import PersonalWorkflowCopilotService
from src.analyzer.realtime_analyzer import RealtimeAnalyzer
from src.analyzer.service import AnalysisService
from src.db.database import get_session_factory
from src.db.repository import Repository
from src.models.operation import OperationEvent
from src.models.session import Session
from src.recorder.context_inference import normalize_browser_name, normalize_recording_mode
from src.recorder.event_bus import EventBus
from src.recorder.service import RecordingService

router = APIRouter()
logger = logging.getLogger(__name__)

_realtime_analyzers: dict[str, RealtimeAnalyzer] = {}


def _get_service() -> RecordingService:
    return RecordingService.get_instance()


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    tags: str = Field(default="")

    @field_validator("name", "description")
    @classmethod
    def sanitize_html(cls, v: str) -> str:
        return re.sub(r"<[^>]+>", "", v)


class StartRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="auto", pattern=r"^(desktop|browser|web|auto|hybrid)$")
    url: str | None = None
    browser: str | None = None
    headless: bool = False


class BatchOperationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)
    operations: list[dict] = Field(..., min_length=1)


@router.post("", response_model=Session)
async def create_session(body: CreateSessionRequest):
    svc = _get_service()
    tag_list = [t.strip() for t in body.tags.split(",") if t.strip()] if body.tags else []
    return await svc.create_session(body.name, body.description, tag_list)


@router.get("", response_model=list[Session])
async def list_sessions(limit: int = 50, offset: int = 0):
    limit = min(max(1, limit), 200)
    offset = max(0, offset)
    svc = _get_service()
    return await svc.list_sessions(limit, offset)


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: str):
    svc = _get_service()
    session = await svc.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/{session_id}/start", response_model=Session)
async def start_recording(
    session_id: str,
    mode: str = "auto",
    url: str | None = None,
    browser: str | None = None,
    headless: bool = False,
    hud: bool = True,
    hud_ai_assist: bool = False,
):
    svc = _get_service()
    try:
        normalized_mode = normalize_recording_mode(mode)
        if normalized_mode in {"desktop", "auto"}:
            return await svc.start_session(
                session_id,
                mode=normalized_mode,
                options={
                    "hud": hud,
                    "hud_ai_assist": hud_ai_assist,
                },
            )
        return await svc.start_session(
            session_id,
            mode=normalized_mode,
            options={
                "url": url,
                "browser": normalize_browser_name(browser),
                "headless": headless,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{session_id}/pause", response_model=Session)
async def pause_recording(session_id: str):
    svc = _get_service()
    try:
        return await svc.pause_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{session_id}/resume", response_model=Session)
async def resume_recording(session_id: str):
    svc = _get_service()
    try:
        return await svc.resume_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{session_id}/stop", response_model=Session)
async def stop_recording(session_id: str):
    svc = _get_service()
    try:
        return await svc.stop_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/diagnostics/recorder-health")
async def recorder_diagnostics():
    """Check recording environment health: display server, system tools, and recorder readiness."""
    import os
    import platform as platmod
    import shutil

    system = platmod.system().lower()
    display = os.environ.get("DISPLAY", "")
    wayland = os.environ.get("WAYLAND_DISPLAY", "")
    has_display = bool(display or wayland)

    tools = {}
    for tool in ("wmctrl", "xdotool", "xprop", "xclip", "xsel"):
        tools[tool] = shutil.which(tool) is not None

    from src.config import settings

    recorder_config = {
        "mouse": settings.RECORD_ENABLE_MOUSE,
        "keyboard": settings.RECORD_ENABLE_KEYBOARD,
        "window": settings.RECORD_ENABLE_WINDOW,
        "clipboard": settings.RECORD_ENABLE_CLIPBOARD,
        "filesystem": settings.RECORD_ENABLE_FILESYSTEM,
    }

    issues = []
    if system == "linux" and not has_display:
        issues.append(
            "No display server detected (DISPLAY and WAYLAND_DISPLAY are unset). "
            "Mouse, keyboard, window, and clipboard recording will NOT work. "
            "Either run in a graphical environment or use mode=web for browser recording."
        )
    if system == "linux" and not tools.get("wmctrl"):
        issues.append("wmctrl not installed — window list/switch recording will not work.")
    if system == "linux" and not tools.get("xdotool") and not tools.get("xprop"):
        issues.append("Neither xdotool nor xprop installed — active window detection will not work.")
    if system == "linux" and not tools.get("xclip") and not tools.get("xsel"):
        issues.append("Neither xclip nor xsel installed — clipboard recording will not work.")

    can_record_desktop = has_display or system != "linux"

    return {
        "platform": system,
        "display": display or wayland or None,
        "has_display_server": has_display,
        "tools": tools,
        "recorder_config": recorder_config,
        "can_record_desktop": can_record_desktop,
        "issues": issues,
        "recommendation": (
            "Desktop recording should work."
            if can_record_desktop
            else "Use mode=web for browser-based recording, or provide a display server for desktop recording."
        ),
    }


@router.post("/{session_id}/hud/toggle")
async def toggle_hud_interaction(session_id: str):
    svc = _get_service()
    try:
        toggled = await svc.toggle_hud_interaction(session_id)
        return {"session_id": session_id, "toggled": toggled}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{session_id}/focus-context/capture")
async def capture_focus_context(
    session_id: str,
    include_vision: bool = True,
    include_llm: bool = True,
):
    svc = _get_service()
    try:
        capture = await svc.capture_focus_context(
            session_id,
            include_vision=include_vision,
            include_llm=include_llm,
        )
        return capture
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{session_id}/operations")
async def get_operations(session_id: str, limit: int = 100, offset: int = 0):
    limit = min(max(1, limit), 500)
    offset = max(0, offset)
    svc = _get_service()
    return await svc.get_operations(session_id, limit, offset)


@router.post("/batch-operations")
async def batch_operations(body: BatchOperationsRequest):
    factory = get_session_factory()
    try:
        async with factory() as db_session:
            repo = Repository(db_session)
            ops = [OperationEvent(**op) for op in body.operations]
            await repo.add_operations_batch(ops)
            return {"status": "ok", "count": len(ops)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Batch operations error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Batch operations failed") from e


@router.websocket("/{session_id}/events")
async def session_events_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    svc = _get_service()
    bus = EventBus.get_instance()
    queue = bus.subscribe("operation_event")
    local_queue = svc.subscribe_events(session_id)
    try:
        send_task = asyncio.create_task(_relay_events(websocket, local_queue, queue, session_id))
        recv_task = asyncio.create_task(_receive_ws_commands(websocket))
        done, pending = await asyncio.wait(
            [send_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Session events WebSocket error: {e}")
    finally:
        bus.unsubscribe("operation_event", queue)
        svc.unsubscribe_events(session_id, local_queue)


async def _relay_events(
    websocket: WebSocket,
    local_queue: asyncio.Queue,
    bus_queue: asyncio.Queue,
    session_id: str,
) -> None:
    while True:
        try:
            try:
                event = local_queue.get_nowait()
                if event is not None:
                    await websocket.send_json({"type": "operation_event", "data": event.model_dump()})
            except asyncio.QueueEmpty:
                pass

            try:
                bus_event = bus_queue.get_nowait()
                if bus_event.payload.get("session_id") == session_id:
                    await websocket.send_json(bus_event.payload)
            except asyncio.QueueEmpty:
                pass

            await websocket.send_json({"type": "heartbeat"})
            await asyncio.sleep(0.1)
        except Exception:
            break


async def _receive_ws_commands(websocket: WebSocket) -> None:
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except Exception:
        pass


@router.websocket("/{session_id}/analysis")
async def session_analysis_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    bus = EventBus.get_instance()
    analyzer = _realtime_analyzers.get(session_id)
    if analyzer is None:
        analyzer = RealtimeAnalyzer()
        _realtime_analyzers[session_id] = analyzer
        await analyzer.start(session_id)

    analysis_queue = bus.subscribe("analysis_update")
    try:
        current_state = analyzer.get_current_state()
        if current_state:
            await websocket.send_json(
                {
                    "type": "analysis_update",
                    "session_id": current_state.session_id,
                    "operation_count": current_state.operation_count,
                    "patterns": current_state.patterns,
                    "flow_candidates": current_state.flow_candidates,
                    "quality_score": current_state.quality_score,
                    "suggestions": current_state.suggestions,
                }
            )

        while True:
            try:
                event = await asyncio.wait_for(analysis_queue.get(), timeout=30.0)
                if event.payload.get("session_id") == session_id:
                    await websocket.send_json(event.payload)
            except TimeoutError:
                current_state = analyzer.get_current_state()
                if current_state:
                    await websocket.send_json(
                        {
                            "type": "analysis_heartbeat",
                            "operation_count": current_state.operation_count,
                            "quality_score": current_state.quality_score,
                        }
                    )
                else:
                    await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Session analysis WebSocket error: {e}")
    finally:
        bus.unsubscribe("analysis_update", analysis_queue)


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    svc = _get_service()
    await svc.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


@router.post("/{session_id}/analyze")
async def analyze_session(session_id: str, min_confidence: float = 0.5, include_ai: bool = False):
    from src.executor.execution_service import ExecutionService

    llm = None
    if include_ai:
        from src.llm.service import LLMService

        try:
            llm = LLMService()
        except Exception:
            llm = None

    exec_svc = ExecutionService.get_instance()

    analysis_svc = AnalysisService(
        llm_service=llm if (llm and llm.is_configured) else None,
        knowledge_graph=exec_svc.knowledge_graph,
    )
    try:
        scored_flows = await analysis_svc.analyze_session(session_id, min_confidence)
        ai_statuses = [
            (sf.flow.metadata or {}).get("ai_enhancement_status")
            for sf in scored_flows
            if (sf.flow.metadata or {}).get("ai_enhancement_status")
        ]
        attempted_statuses = [status for status in ai_statuses if (status.get("attempts") or 0) > 0]
        ai_successful = sum(1 for status in attempted_statuses if status.get("status") == "success")
        return {
            "session_id": session_id,
            "flows_found": len(scored_flows),
            "llm_enhanced": any(bool((sf.flow.metadata or {}).get("ai_analysis")) for sf in scored_flows),
            "ai_enhancement": {
                "attempted_flows": len(attempted_statuses),
                "successful_flows": ai_successful,
                "failed_flows": max(0, len(attempted_statuses) - ai_successful),
            },
            "flows": [
                {
                    "id": sf.flow.id,
                    "name": sf.flow.name,
                    "confidence": sf.overall_confidence,
                    "is_reliable": sf.is_reliable,
                    "steps_count": len(sf.flow.steps),
                    "dimensions": [{"name": d.name, "score": d.score, "details": d.details} for d in sf.dimensions],
                    "suggestions": [
                        {"category": s.category, "message": s.message, "impact": s.impact} for s in sf.suggestions
                    ],
                    "ai_analysis": (sf.flow.metadata or {}).get("ai_analysis"),
                    "ai_enhancement_status": (sf.flow.metadata or {}).get("ai_enhancement_status"),
                    "semantic_intent": (sf.flow.metadata or {}).get("semantic_intent"),
                    "checkpoint_summary": (sf.flow.metadata or {}).get("checkpoint_summary"),
                    "closed_loop_assessment": (sf.flow.metadata or {}).get("closed_loop_assessment"),
                    "closed_loop_ready": bool((sf.flow.metadata or {}).get("closed_loop_ready")),
                    "steps": [
                        {
                            "id": step.id,
                            "type": step.type,
                            "description": step.description,
                            "delay": step.delay,
                            "on_error": step.on_error,
                            "retry_count": step.retry_count,
                            "target": step.target.model_dump() if step.target else None,
                            "preconditions": [condition.model_dump() for condition in step.preconditions],
                            "ai_analysis": (step.metadata or {}).get("ai_analysis"),
                            "semantic_annotation": (step.metadata or {}).get("semantic_annotation"),
                            "checkpoints": (step.metadata or {}).get("checkpoints"),
                            "postconditions": (step.metadata or {}).get("postconditions", []),
                        }
                        for step in sf.flow.steps
                    ],
                }
                for sf in scored_flows
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Session analysis error for {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Analysis failed") from e


@router.post("/{session_id}/copilot")
async def session_copilot(session_id: str, min_confidence: float = 0.3):
    from src.llm.service import LLMService

    try:
        llm = LLMService()
    except Exception:
        llm = None

    service = PersonalWorkflowCopilotService(llm_service=llm if (llm and llm.is_configured) else None)

    try:
        return await service.build_session_brief(session_id, min_confidence=min_confidence)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Session copilot error for {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Copilot analysis failed") from e
