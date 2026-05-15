from __future__ import annotations

import contextlib
import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.agent.engine import AgentEngine
from src.db.repository import Repository
from src.llm.service import LLMService
from src.models.agent import (
    Permission,
    SkillCategory,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_engine: AgentEngine | None = None


class _ManagedRepository:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def __getattr__(self, name):
        async def _call(*args, **kwargs):
            async with self._session_factory() as session:
                repo = Repository(session)
                method = getattr(repo, name)
                return await method(*args, **kwargs)

        return _call


def _get_engine() -> AgentEngine:
    global _engine
    if _engine is None:
        from src.db.database import get_session_factory

        session_factory = get_session_factory()
        repo = _ManagedRepository(session_factory)
        llm = LLMService()
        _engine = AgentEngine(repo, llm)
    return _engine


class CreateSessionRequest(BaseModel):
    permissions: list[str] = Field(default_factory=lambda: [p.value for p in Permission][:7])
    skills: list[str] = Field(
        default_factory=lambda: [
            SkillCategory.PROCESS_MAPPING.value,
            SkillCategory.WORKFLOW_OPTIMIZATION.value,
            SkillCategory.DEBUGGING.value,
        ]
    )
    context: dict = Field(default_factory=dict)


class AgentMessageRequest(BaseModel):
    session_id: str
    message: str
    max_iterations: int = 5


class AnalyzeWorkflowRequest(BaseModel):
    flow_id: str


class DebugExecutionRequest(BaseModel):
    execution_id: str


class MapProcessRequest(BaseModel):
    session_id: str


@router.get("/skills")
async def get_skills():
    engine = _get_engine()
    return await engine.get_skills()


@router.get("/tools")
async def get_tools():
    engine = _get_engine()
    return await engine.get_tools()


@router.post("/sessions")
async def create_agent_session(req: CreateSessionRequest):
    engine = _get_engine()
    permissions = [Permission(p) for p in req.permissions]
    skills = [SkillCategory(s) for s in req.skills]
    session = await engine.create_session(permissions=permissions, skills=skills, context=req.context)
    return session.model_dump()


@router.get("/sessions")
async def list_agent_sessions():
    engine = _get_engine()
    sessions = await engine.list_sessions()
    return [s.model_dump() for s in sessions]


@router.get("/sessions/{session_id}")
async def get_agent_session(session_id: str):
    engine = _get_engine()
    session = await engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return session.model_dump()


@router.post("/chat")
async def agent_chat(req: AgentMessageRequest):
    engine = _get_engine()
    try:
        response = await engine.process_message(req.session_id, req.message, req.max_iterations)
        return response.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Agent chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Agent processing failed")


@router.post("/analyze-workflow")
async def analyze_workflow(req: AnalyzeWorkflowRequest):
    try:
        engine = _get_engine()
        response = await engine.analyze_workflow(req.flow_id)
        return response.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Analyze workflow error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Workflow analysis failed")


@router.post("/debug-execution")
async def debug_execution(req: DebugExecutionRequest):
    try:
        engine = _get_engine()
        response = await engine.debug_execution(req.execution_id)
        return response.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Debug execution error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Execution debugging failed")


@router.post("/map-process")
async def map_process(req: MapProcessRequest):
    try:
        engine = _get_engine()
        response = await engine.map_process(req.session_id)
        return response.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Map process error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Process mapping failed")


@router.websocket("/sessions/{session_id}/stream")
async def agent_stream(websocket: WebSocket, session_id: str):
    await websocket.accept()
    engine = _get_engine()

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                user_message = msg.get("message", "")
                max_iter = msg.get("max_iterations", 5)
            except json.JSONDecodeError:
                user_message = data
                max_iter = 5

            await websocket.send_json({"type": "processing", "message": "Analyzing your request..."})

            response = await engine.process_message(session_id, user_message, max_iter)

            for _i, tc in enumerate(response.tool_calls):
                await websocket.send_json(
                    {
                        "type": "tool_call",
                        "tool_type": tc.tool_type.value,
                        "parameters": tc.parameters,
                        "call_id": tc.call_id,
                    }
                )

            for _i, tr in enumerate(response.tool_results):
                await websocket.send_json(
                    {
                        "type": "tool_result",
                        "call_id": tr.call_id,
                        "success": tr.success,
                        "data": tr.data if isinstance(tr.data, (dict, list)) else str(tr.data) if tr.data else None,
                        "error": tr.error,
                        "execution_ms": tr.execution_ms,
                    }
                )

            result = {
                "type": "response",
                "message": response.message,
            }
            if response.process_map:
                result["process_map"] = response.process_map.model_dump()
            if response.root_cause_analysis:
                result["root_cause_analysis"] = response.root_cause_analysis.model_dump()
            if response.workflow_assessment:
                result["workflow_assessment"] = response.workflow_assessment.model_dump()
            if response.debug_session:
                result["debug_session"] = response.debug_session.model_dump()
            if response.suggestions:
                result["suggestions"] = [s.model_dump() for s in response.suggestions]

            await websocket.send_json(result)

    except WebSocketDisconnect:
        logger.info(f"Agent WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"Agent WebSocket error: {e}", exc_info=True)
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "message": str(e)})
