import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.analyzer.closure_assessor import FlowClosureError
from src.analyzer.confidence_scorer import ScoredFlow
from src.analyzer.service import AnalysisService
from src.db.database import get_session_factory
from src.db.repository import Repository
from src.executor.execution_service import ExecutionService
from src.models.automation import AutomationFlow
from src.recorder.context_inference import normalize_browser_name
from src.recorder.service import RecordingService

router = APIRouter()
logger = logging.getLogger(__name__)


class MVPStartBrowserRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120)
    url: str = Field(..., min_length=1, max_length=2000)
    browser: str | None = Field(default="chromium", max_length=40)
    headless: bool = False
    description: str = Field(default="", max_length=500)


class MVPExecuteFlowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variables: dict | None = None
    ai_options: dict | None = None


def _get_recording_service() -> RecordingService:
    return RecordingService.get_instance()


def _get_analysis_service() -> AnalysisService:
    return AnalysisService()


def _get_execution_service() -> ExecutionService:
    return ExecutionService.get_instance()


async def _with_repo(operation: Callable[[Repository], Awaitable[Any]]) -> Any:
    factory = get_session_factory()
    async with factory() as session:
        repo = Repository(session)
        return await operation(repo)


def _session_summary(session) -> dict[str, Any]:
    return {
        "id": session.id,
        "name": session.name,
        "description": session.description,
        "status": session.status,
        "tags": list(session.tags),
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def _as_flow(item: ScoredFlow | AutomationFlow) -> AutomationFlow:
    return item.flow if isinstance(item, ScoredFlow) else item


def _flow_blocking_issue_count(flow: AutomationFlow) -> int:
    assessment = ((flow.metadata or {}).get("closed_loop_assessment") or {})
    metrics = assessment.get("metrics") or {}
    count = metrics.get("blocking_issue_count")
    if isinstance(count, int):
        return count
    return len(assessment.get("issues") or [])


def _flow_summary(flow: AutomationFlow, *, recommended: bool = False) -> dict[str, Any]:
    metadata = dict(flow.metadata or {})
    assessment = metadata.get("closed_loop_assessment") or {}
    issues = assessment.get("issues") or []
    return {
        "id": flow.id,
        "name": flow.name,
        "description": flow.description,
        "confidence": flow.confidence,
        "status": flow.status,
        "step_count": len(flow.steps),
        "source_session_id": flow.source_session_id,
        "closed_loop_ready": bool(metadata.get("closed_loop_ready")),
        "blocking_issue_count": _flow_blocking_issue_count(flow),
        "summary": assessment.get("summary") or "",
        "issues": [
            {
                "code": issue.get("code"),
                "message": issue.get("message"),
                "step_index": issue.get("step_index"),
                "severity": issue.get("severity"),
            }
            for issue in issues[:5]
            if isinstance(issue, dict)
        ],
        "recommended": recommended,
    }


def _pick_recommended_flow(items: list[ScoredFlow | AutomationFlow]) -> AutomationFlow | None:
    if not items:
        return None

    def _rank(item: ScoredFlow | AutomationFlow) -> tuple[int, int, float, int]:
        flow = _as_flow(item)
        ready = 1 if ((flow.metadata or {}).get("closed_loop_ready")) else 0
        blockers = _flow_blocking_issue_count(flow)
        confidence = float(flow.confidence or 0.0)
        step_count = len(flow.steps)
        return (ready, -blockers, confidence, -abs(step_count - 5))

    best = max(items, key=_rank)
    return _as_flow(best)


def _playwright_ready() -> bool:
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


@router.get("/overview")
async def overview():
    recording = _get_recording_service()
    sessions = await recording.list_sessions(limit=5, offset=0)
    flows = await _with_repo(lambda repo: repo.list_automations(limit=8, offset=0))
    return {
        "product": {
            "name": "Browser Workflow MVP",
            "mode": "browser-first",
            "legacy_console_path": "/index.html",
        },
        "runtime": {
            "api_status": "ok",
            "playwright_ready": _playwright_ready(),
        },
        "recent_sessions": [_session_summary(session) for session in sessions],
        "recent_flows": [_flow_summary(flow) for flow in flows],
    }


@router.post("/browser/start")
async def start_browser_recording(body: MVPStartBrowserRecordingRequest):
    recording = _get_recording_service()
    session = await recording.create_session(
        body.name,
        body.description,
        tags=["mvp", "browser"],
    )
    started = await recording.start_session(
        session.id,
        mode="web",
        options={
            "url": body.url,
            "browser": normalize_browser_name(body.browser),
            "headless": body.headless,
        },
    )
    return {
        "session": _session_summary(started),
        "target_url": body.url,
        "browser": normalize_browser_name(body.browser) or "chromium",
        "headless": body.headless,
    }


@router.get("/browser/{session_id}")
async def get_browser_session(session_id: str):
    recording = _get_recording_service()
    session = await recording.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    operations = await recording.get_operations(session_id, limit=500, offset=0)
    flows = await _with_repo(lambda repo: repo.list_automation_flows_by_session(session_id))
    recommended = _pick_recommended_flow(flows)
    return {
        "session": _session_summary(session),
        "operation_count": len(operations),
        "flows": [_flow_summary(flow, recommended=bool(recommended and flow.id == recommended.id)) for flow in flows],
        "recommended_flow": _flow_summary(recommended, recommended=True) if recommended else None,
    }


@router.post("/browser/{session_id}/stop-and-analyze")
async def stop_and_analyze_browser_session(session_id: str):
    recording = _get_recording_service()
    session = await recording.stop_session(session_id)
    operations = await recording.get_operations(session_id, limit=500, offset=0)
    scored_flows = await _get_analysis_service().analyze_session(session_id, min_confidence=0.0)
    recommended = _pick_recommended_flow(scored_flows)
    flows = [_as_flow(item) for item in scored_flows]
    return {
        "session": _session_summary(session),
        "operation_count": len(operations),
        "flow_count": len(flows),
        "recommended_flow": _flow_summary(recommended, recommended=True) if recommended else None,
        "flows": [_flow_summary(flow, recommended=bool(recommended and flow.id == recommended.id)) for flow in flows],
    }


@router.get("/flows")
async def list_mvp_flows(limit: int = 20):
    limit = min(max(1, limit), 50)
    flows = await _with_repo(lambda repo: repo.list_automations(limit=limit, offset=0))
    recommended = _pick_recommended_flow(flows)
    return {
        "flows": [_flow_summary(flow, recommended=bool(recommended and flow.id == recommended.id)) for flow in flows],
        "recommended_flow": _flow_summary(recommended, recommended=True) if recommended else None,
    }


@router.post("/flows/{flow_id}/run")
async def run_mvp_flow(flow_id: str, body: MVPExecuteFlowRequest):
    execution = _get_execution_service()
    try:
        execution_id = await execution.start_execution_async(
            flow_id,
            variables=body.variables,
            ai_options=body.ai_options,
        )
    except FlowClosureError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "assessment": exc.assessment.model_dump(mode="json"),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"execution_id": execution_id, "flow_id": flow_id}


@router.get("/executions/{execution_id}")
async def get_mvp_execution(execution_id: str):
    execution = _get_execution_service()
    record = await execution.get_execution(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="Execution not found")
    return record.model_dump()
