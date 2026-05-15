from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from src.collab import CollaborationService
from src.config import settings
from src.llm.local_engine import LocalLLMEngine
from src.llm.recording_analyzer import RecordingLLMAnalyzer
from src.recorder.event_bus import EventBus

router = APIRouter()
logger = logging.getLogger(__name__)

_recording_analyzers: dict[str, RecordingLLMAnalyzer] = {}


def _get_collab_service() -> CollaborationService:
    return CollaborationService.get_instance()


@router.get("/local-llm/status")
async def local_llm_status():
    if not settings.LOCAL_LLM_ENABLED:
        return {
            "enabled": False,
            "message": "本地 LLM 未启用，请设置 LOCAL_LLM_ENABLED=true",
        }

    try:
        engine = await LocalLLMEngine.get_instance()
        status = await engine.get_status()
        return {
            "enabled": True,
            "engine": status.engine,
            "running": status.running,
            "model": status.model,
            "model_loaded": status.model_loaded,
            "gpu_name": status.gpu_name,
            "vram_used_mb": status.vram_used_mb,
            "tokens_per_second": status.tokens_per_second,
            "context_length": status.context_length,
            "error": status.error,
        }
    except Exception as e:
        return {
            "enabled": True,
            "running": False,
            "error": str(e),
        }


@router.get("/local-llm/test")
async def test_local_llm(prompt: str = "你好，请简单介绍一下你自己"):
    if not settings.LOCAL_LLM_ENABLED:
        raise HTTPException(status_code=400, detail="本地 LLM 未启用")

    try:
        engine = await LocalLLMEngine.get_instance()
        result = await engine.generate([{"role": "user", "content": prompt}], max_tokens=256)

        return {
            "success": True,
            "model": settings.LOCAL_LLM_MODEL,
            "response": result.text,
            "tokens_generated": result.tokens_generated,
            "elapsed_ms": result.elapsed_ms,
            "tokens_per_second": result.tokens_per_second,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rooms")
async def list_rooms():
    svc = _get_collab_service()
    rooms = await svc.list_rooms()
    return {"rooms": rooms, "count": len(rooms)}


@router.get("/rooms/{room_id}")
async def get_room_status(room_id: str):
    svc = _get_collab_service()
    status = await svc.get_room_status(room_id)
    if not status:
        raise HTTPException(status_code=404, detail="协作室不存在")
    return status


@router.post("/analyzer/{session_id}/start")
async def start_llm_analyzer(session_id: str):
    if session_id in _recording_analyzers:
        return {"status": "already_running", "session_id": session_id}

    analyzer = RecordingLLMAnalyzer()
    await analyzer.start(session_id)
    _recording_analyzers[session_id] = analyzer

    return {
        "status": "started",
        "session_id": session_id,
        "local_llm_enabled": settings.LOCAL_LLM_ENABLED,
        "model": settings.LOCAL_LLM_MODEL if settings.LOCAL_LLM_ENABLED else None,
    }


@router.post("/analyzer/{session_id}/stop")
async def stop_llm_analyzer(session_id: str):
    analyzer = _recording_analyzers.pop(session_id, None)
    if analyzer is None:
        raise HTTPException(status_code=404, detail="分析器未在运行")

    await analyzer.stop()
    return {
        "status": "stopped",
        "session_id": session_id,
        "suggestions": analyzer.get_suggestions(),
        "narratives": analyzer.get_narratives(),
    }


@router.get("/analyzer/{session_id}/suggestions")
async def get_suggestions(session_id: str):
    analyzer = _recording_analyzers.get(session_id)
    if not analyzer:
        return {"suggestions": [], "narratives": []}

    return {
        "suggestions": analyzer.get_suggestions(),
        "narratives": analyzer.get_narratives(),
    }


@router.websocket("/ws/{room_id}")
async def collab_websocket(
    websocket: WebSocket,
    room_id: str,
    session_id: str = Query(...),
    username: str = Query(default="匿名用户"),
):
    await websocket.accept()

    svc = _get_collab_service()
    room = await svc.get_or_create_room(room_id, session_id)
    user_id = str(uuid.uuid4())

    if not await room.join(user_id, username, websocket):
        await websocket.close(code=4000, reason="room_full")
        return

    logger.info(f"User '{username}' ({user_id}) joined collab room '{room_id}'")

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            except TimeoutError:
                await websocket.send_json({"type": "heartbeat", "ts": asyncio.get_event_loop().time()})
                continue

            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "chat")
                content = msg.get("content", "")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                if content:
                    await room.handle_message(user_id, content, msg_type)

            except json.JSONDecodeError:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "无效的 JSON 格式",
                    }
                )

    except WebSocketDisconnect:
        logger.info(f"User '{username}' disconnected from collab room '{room_id}'")
    except Exception as e:
        logger.error(f"Collab WebSocket error for '{username}': {e}")
    finally:
        await room.leave(user_id)
        if room.user_count == 0:
            await svc.remove_room(room_id)


@router.websocket("/analyzer-ws/{session_id}")
async def analyzer_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()

    bus = EventBus.get_instance()
    ai_queue = bus.subscribe("ai_suggestion")
    narrative_queue = bus.subscribe("flow_narrative")

    analyzer = _recording_analyzers.get(session_id)
    if analyzer is None:
        analyzer = RecordingLLMAnalyzer()
        await analyzer.start(session_id)
        _recording_analyzers[session_id] = analyzer

    await websocket.send_json(
        {
            "type": "analyzer_ready",
            "session_id": session_id,
            "local_llm_enabled": settings.LOCAL_LLM_ENABLED,
        }
    )

    try:
        while True:
            try:
                try:
                    event = ai_queue.get_nowait()
                    await websocket.send_json(event.payload)
                except asyncio.QueueEmpty:
                    pass

                try:
                    event = narrative_queue.get_nowait()
                    await websocket.send_json(event.payload)
                except asyncio.QueueEmpty:
                    pass

                await asyncio.sleep(0.2)

            except Exception:
                continue

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Analyzer WebSocket error: {e}")
    finally:
        bus.unsubscribe("ai_suggestion", ai_queue)
        bus.unsubscribe("flow_narrative", narrative_queue)
