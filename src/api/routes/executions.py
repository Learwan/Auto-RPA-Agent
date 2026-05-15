import asyncio
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.executor.execution_service import ExecutionService

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_service() -> ExecutionService:
    return ExecutionService.get_instance()


@router.get("")
async def list_executions(limit: int = 50, offset: int = 0):
    limit = min(max(1, limit), 200)
    offset = max(0, offset)
    svc = _get_service()
    records = await svc.list_executions(limit, offset)
    return [r.model_dump() for r in records]


@router.get("/{execution_id}")
async def get_execution(execution_id: str):
    svc = _get_service()
    record = await svc.get_execution(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="Execution not found")
    return record.model_dump()


@router.post("/{execution_id}/pause")
async def pause_execution(execution_id: str):
    svc = _get_service()
    try:
        record = await svc.pause_execution(execution_id)
        return record.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{execution_id}/resume")
async def resume_execution(execution_id: str):
    svc = _get_service()
    try:
        record = await svc.resume_execution(execution_id)
        return record.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{execution_id}/stop")
async def stop_execution(execution_id: str):
    svc = _get_service()
    try:
        record = await svc.stop_execution(execution_id)
        return record.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{execution_id}/skip-step")
async def skip_step(execution_id: str):
    svc = _get_service()
    try:
        await svc.skip_step(execution_id)
        return {"status": "step_skipped", "execution_id": execution_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{execution_id}/step-over")
async def step_over(execution_id: str):
    svc = _get_service()
    try:
        await svc.step_over(execution_id)
        return {"status": "step_over", "execution_id": execution_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.websocket("/{execution_id}/feedback")
async def execution_feedback_ws(websocket: WebSocket, execution_id: str):
    await websocket.accept()
    svc = _get_service()
    try:
        queue = await svc.subscribe_feedback(execution_id)
        await websocket.send_json({"type": "subscribed", "execution_id": execution_id})
        while True:
            try:
                feedback = await asyncio.wait_for(queue.get(), timeout=30.0)
            except TimeoutError:
                try:
                    await websocket.send_json({"type": "heartbeat"})
                    continue
                except Exception:
                    break
            if feedback is None:
                await websocket.send_json({"type": "completed", "execution_id": execution_id})
                break
            await websocket.send_json(feedback.model_dump())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Execution feedback WebSocket error: {e}")
