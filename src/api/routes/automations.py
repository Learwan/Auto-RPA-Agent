import asyncio
import contextlib
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.analyzer.checkpoint_support import backfill_flow_checkpoints
from src.analyzer.closure_assessor import FlowClosureAssessor, FlowClosureError
from src.analyzer.script_generator import ScriptGenerator
from src.analyzer.service import AnalysisService
from src.db.database import get_session
from src.db.repository import Repository
from src.executor.execution_service import ExecutionService
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    ErrorAction,
    StepBranch,
    StepCondition,
    StepTarget,
    StepType,
)

router = APIRouter()
logger = logging.getLogger(__name__)
_closure_assessor = FlowClosureAssessor()


class AutomationFlowPatchRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    entry_step_ids: list[str] | None = None
    metadata: dict | None = None
    status: str | None = None


class AutomationStepPatchRequest(BaseModel):
    type: StepType | None = None
    action: dict | None = None
    target: StepTarget | None = None
    atomic: bool | None = None
    depends_on: list[str] | None = None
    preconditions: list[StepCondition] | None = None
    branches: list[StepBranch] | None = None
    execution_order: int | None = None
    metadata: dict | None = None
    delay: int | None = None
    condition: StepCondition | None = None
    on_error: ErrorAction | None = None
    retry_count: int | None = None
    description: str | None = None
    verification_enabled: bool | None = None
    verification_strict: bool | None = None


class StepBranchUpsertRequest(BaseModel):
    target_step_id: str
    outcome: str = "default"
    condition: StepCondition | None = None
    description: str | None = None
    metadata: dict = Field(default_factory=dict)


class StepBranchDeleteRequest(BaseModel):
    target_step_id: str
    outcome: str | None = None


class ExecuteAutomationRequest(BaseModel):
    variables: dict | None = None
    ai_options: dict | None = None


def _get_repo(db: AsyncSession = Depends(get_session)) -> Repository:
    return Repository(db)


def _get_execution_service() -> ExecutionService:
    return ExecutionService.get_instance()


async def _load_flow_or_404(repo: Repository, flow_id: str) -> AutomationFlow:
    flow = await repo.get_automation_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Automation not found")
    return flow


async def _save_flow(repo: Repository, flow: AutomationFlow) -> dict:
    backfill_flow_checkpoints(flow)
    _refresh_flow_closure_assessment(flow)
    saved = await repo.save_automation_flow(flow)
    return _flow_detail(saved)


def _refresh_flow_closure_assessment(flow: AutomationFlow) -> None:
    assessment = _closure_assessor.assess(flow)
    flow.metadata = {
        **(flow.metadata or {}),
        "closed_loop_assessment": assessment.model_dump(mode="json"),
        "closed_loop_ready": assessment.ready,
    }


def _flow_summary(flow: AutomationFlow) -> dict:
    backfill_flow_checkpoints(flow)
    _refresh_flow_closure_assessment(flow)
    metadata = dict(flow.metadata or {})
    return {
        "id": flow.id,
        "name": flow.name,
        "description": flow.description,
        "structure_version": flow.structure_version,
        "entry_step_ids": list(flow.entry_step_ids),
        "metadata": metadata,
        "steps": [
            {
                "id": s.id,
                "type": s.type,
                "action": s.action,
                "target": s.target.model_dump() if s.target else {},
                "atomic": s.atomic,
                "depends_on": list(s.depends_on),
                "preconditions": [condition.model_dump() for condition in s.preconditions],
                "branches": [branch.model_dump() for branch in s.branches],
                "execution_order": s.execution_order,
                "metadata": dict(s.metadata),
                "condition": s.condition.model_dump() if s.condition else None,
                "delay": s.delay,
                "on_error": s.on_error,
                "retry_count": s.retry_count,
                "description": s.description,
                "verification_enabled": s.verification_enabled,
                "verification_strict": s.verification_strict,
                "ai_analysis": (s.metadata or {}).get("ai_analysis"),
            }
            for s in flow.steps
        ],
        "variables": [v.model_dump() for v in flow.variables],
        "error_handling": flow.error_handling.model_dump() if flow.error_handling else {},
        "confidence": flow.confidence,
        "source_session_id": flow.source_session_id,
        "status": flow.status,
        "execution_count": flow.execution_count,
        "success_count": flow.success_count,
        "ai_analysis": metadata.get("ai_analysis"),
        "closed_loop_assessment": metadata.get("closed_loop_assessment"),
        "closed_loop_ready": bool(metadata.get("closed_loop_ready")),
    }


def _flow_detail(flow: AutomationFlow) -> dict:
    result = _flow_summary(flow)
    source_pattern = flow.source_pattern
    if source_pattern and isinstance(source_pattern, dict):
        instances = source_pattern.get("instances", [])
        if len(instances) > 10:
            source_pattern = {
                **source_pattern,
                "instances": instances[:10],
                "instances_truncated": True,
                "total_instances": len(instances),
            }
    result["source_pattern"] = source_pattern
    return result


@router.get("")
async def list_automations(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_session),
):
    limit = min(max(1, limit), 200)
    offset = max(0, offset)
    repo = _get_repo(db)
    flows = await repo.list_automations(limit, offset)
    return [_flow_summary(f) for f in flows]


@router.post("", response_model=AutomationFlow)
async def create_automation(
    flow: AutomationFlow,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    backfill_flow_checkpoints(flow)
    _refresh_flow_closure_assessment(flow)
    return await repo.save_automation(flow)


@router.get("/{flow_id}")
async def get_automation(
    flow_id: str,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    flow = await _load_flow_or_404(repo, flow_id)
    return _flow_detail(flow)


@router.put("/{flow_id}")
async def update_automation(
    flow_id: str,
    flow: AutomationFlow,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    await _load_flow_or_404(repo, flow_id)
    flow.id = flow_id
    backfill_flow_checkpoints(flow)
    _refresh_flow_closure_assessment(flow)
    return await repo.save_automation(flow)


@router.patch("/{flow_id}")
async def patch_automation(
    flow_id: str,
    patch: AutomationFlowPatchRequest,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    flow = await _load_flow_or_404(repo, flow_id)

    patch_data = patch.model_dump(exclude_unset=True)
    if "name" in patch_data:
        flow.name = patch_data["name"] or flow.name
    if "description" in patch_data:
        flow.description = patch_data["description"] or ""
    if "metadata" in patch_data:
        flow.metadata = patch_data["metadata"] or {}
    if "status" in patch_data:
        flow.status = patch_data["status"] or flow.status
    if "entry_step_ids" in patch_data:
        try:
            flow.set_entry_steps(patch_data["entry_step_ids"] or [])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await _save_flow(repo, flow)


@router.post("/{flow_id}/steps")
async def add_automation_step(
    flow_id: str,
    step: AutomationStep,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    flow = await _load_flow_or_404(repo, flow_id)
    try:
        flow.add_step(step)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _save_flow(repo, flow)


@router.patch("/{flow_id}/steps/{step_id}")
async def patch_automation_step(
    flow_id: str,
    step_id: str,
    patch: AutomationStepPatchRequest,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    flow = await _load_flow_or_404(repo, flow_id)
    try:
        flow.update_step(step_id, patch.model_dump(exclude_unset=True))
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return await _save_flow(repo, flow)


@router.delete("/{flow_id}/steps/{step_id}")
async def delete_automation_step(
    flow_id: str,
    step_id: str,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    flow = await _load_flow_or_404(repo, flow_id)
    try:
        removed = flow.remove_step(step_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = await _save_flow(repo, flow)
    payload["removed_step_id"] = removed.id
    return payload


@router.post("/{flow_id}/steps/{step_id}/branches")
async def upsert_automation_branch(
    flow_id: str,
    step_id: str,
    branch_request: StepBranchUpsertRequest,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    flow = await _load_flow_or_404(repo, flow_id)
    try:
        flow.upsert_branch(step_id, StepBranch.model_validate(branch_request.model_dump()))
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return await _save_flow(repo, flow)


@router.delete("/{flow_id}/steps/{step_id}/branches")
async def delete_automation_branch(
    flow_id: str,
    step_id: str,
    branch_request: StepBranchDeleteRequest,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    flow = await _load_flow_or_404(repo, flow_id)
    try:
        flow.remove_branch(step_id, branch_request.target_step_id, branch_request.outcome)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return await _save_flow(repo, flow)


@router.delete("/{flow_id}")
async def delete_automation(
    flow_id: str,
    db: AsyncSession = Depends(get_session),
):
    repo = _get_repo(db)
    try:
        await repo.delete_automation(flow_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "flow_id": flow_id}


@router.post("/{flow_id}/execute")
async def execute_automation(
    flow_id: str,
    request: ExecuteAutomationRequest | None = None,
    async_exec: bool = False,
):
    try:
        svc = _get_execution_service()
        variables = request.variables if request else None
        ai_options = request.ai_options if request else None
        if async_exec:
            execution_id = await svc.start_execution_async(flow_id, variables, ai_options=ai_options)
            return {"execution_id": execution_id, "status": "started", "flow_id": flow_id}
        record = await svc.start_execution(flow_id, variables, ai_options=ai_options)
        return record.model_dump()
    except FlowClosureError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(e),
                "assessment": e.assessment.model_dump(mode="json"),
            },
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Execute automation error for {flow_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Execution failed")


@router.post("/{flow_id}/dry-run")
async def dry_run_automation(flow_id: str, request: ExecuteAutomationRequest | None = None):
    try:
        svc = _get_execution_service()
        variables = request.variables if request else None
        ai_options = request.ai_options if request else None
        record = await svc.start_execution(flow_id, variables, dry_run=True, ai_options=ai_options)
        return record.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Dry-run error for {flow_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Dry-run failed")


@router.post("/{flow_id}/export")
async def export_automation(flow_id: str, format: str = "json", db: AsyncSession = Depends(get_session)):
    repo = _get_repo(db)
    flow = await repo.get_automation(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Automation not found")

    generator = ScriptGenerator()
    if format == "python":
        script = generator.export_python(flow)
        return {"format": "python", "content": script, "flow_id": flow_id}
    else:
        data = generator.export_json(flow)
        return {"format": "json", "content": data, "flow_id": flow_id}


@router.get("/{flow_id}/score")
async def get_flow_score(flow_id: str, db: AsyncSession = Depends(get_session)):
    repo = _get_repo(db)
    analysis_svc = AnalysisService(repo)
    scored = await analysis_svc.get_flow_detail(flow_id)
    if not scored:
        raise HTTPException(status_code=404, detail="Flow not found")
    assessment = _closure_assessor.assess(scored.flow)
    return {
        "flow_id": flow_id,
        "overall_confidence": scored.overall_confidence,
        "is_reliable": scored.is_reliable,
        "dimensions": [{"name": d.name, "score": d.score, "details": d.details} for d in scored.dimensions],
        "suggestions": [{"category": s.category, "message": s.message, "impact": s.impact} for s in scored.suggestions],
        "closed_loop_assessment": assessment.model_dump(mode="json"),
    }


@router.websocket("/ws/{flow_id}/execute-live")
async def execute_flow_live_ws(websocket: WebSocket, flow_id: str, dry_run: bool = False):
    await websocket.accept()
    svc = _get_execution_service()

    try:
        flow = await _with_repo(lambda r: r.get_automation_flow(flow_id), websocket)
        if not flow:
            await websocket.send_json({"type": "error", "message": f"Flow not found: {flow_id}"})
            await websocket.close()
            return

        try:
            execution_id = await svc.start_execution_async(flow_id)
        except FlowClosureError as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(exc),
                    "assessment": exc.assessment.model_dump(mode="json"),
                }
            )
            await websocket.close()
            return
        await websocket.send_json(
            {
                "type": "execution_started",
                "execution_id": execution_id,
                "flow_id": flow_id,
                "total_steps": len(flow.steps),
            }
        )

        feedback_queue = await svc.subscribe_feedback(execution_id)

        while True:
            try:
                feedback = await asyncio.wait_for(feedback_queue.get(), timeout=30.0)
            except TimeoutError:
                try:
                    await websocket.send_json({"type": "heartbeat"})
                    continue
                except Exception:
                    break

            if feedback is None:
                break

            step_status = (
                feedback.step_status.value if hasattr(feedback.step_status, "value") else str(feedback.step_status)
            )

            await websocket.send_json(
                {
                    "type": "step_feedback",
                    "execution_id": execution_id,
                    **feedback.model_dump(),
                }
            )

        record = await svc.get_execution(execution_id)
        if record:
            await websocket.send_json(
                {
                    "type": "execution_completed",
                    "execution_id": execution_id,
                    "status": record.status.value if hasattr(record.status, "value") else str(record.status),
                    "total_steps": record.total_steps,
                    "completed_steps": record.completed_steps,
                    "failed_steps": record.failed_steps,
                    "error_summary": record.error_summary,
                }
            )
        else:
            await websocket.send_json(
                {
                    "type": "execution_completed",
                    "execution_id": execution_id,
                    "status": "unknown",
                }
            )

    except WebSocketDisconnect:
        logger.info(f"Flow execution WS disconnected for {flow_id}")
    except Exception as e:
        logger.error(f"Flow execution WS error: {e}")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "message": str(e)})


async def _with_repo(operation, websocket=None):
    from src.db.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        repo = Repository(session)
        return await operation(repo)
