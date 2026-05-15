import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.analyzer.multi_fusion import AlignedTrace, MultiRecordingFusion
from src.models.recording_group import (
    FusedBranchModel,
    FusionResult,
    LoopPatternModel,
    RecordingGroup,
    RecordingSession,
    RecordingStatus,
)

router = APIRouter(prefix="/fusion", tags=["fusion"])

_fusion_service = MultiRecordingFusion()
_groups: dict[str, RecordingGroup] = {}
_results: dict[str, FusionResult] = {}


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    target_application: str = Field(default="")
    target_workflow: str = Field(default="")
    min_recordings: int = Field(default=2, ge=2, le=10)


class AddSessionRequest(BaseModel):
    session_id: str
    name: str = Field(default="")


class FuseRequest(BaseModel):
    group_id: str


@router.post("/groups")
async def create_group(request: CreateGroupRequest):
    group = RecordingGroup(
        group_id=str(uuid.uuid4()),
        name=request.name,
        description=request.description,
        target_application=request.target_application,
        target_workflow=request.target_workflow,
        min_recordings=request.min_recordings,
    )
    _groups[group.group_id] = group
    return group.model_dump()


@router.get("/groups")
async def list_groups():
    return [g.model_dump() for g in _groups.values()]


@router.get("/groups/{group_id}")
async def get_group(group_id: str):
    group = _groups.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group.model_dump()


@router.post("/groups/{group_id}/sessions")
async def add_session(group_id: str, request: AddSessionRequest):
    group = _groups.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    session = RecordingSession(
        session_id=request.session_id,
        name=request.name,
        status=RecordingStatus.COMPLETED,
    )
    group.add_session(session)
    return group.model_dump()


@router.delete("/groups/{group_id}/sessions/{session_id}")
async def remove_session(group_id: str, session_id: str):
    group = _groups.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    group.remove_session(session_id)
    return group.model_dump()


@router.post("/fuse")
async def fuse_recordings(request: FuseRequest):
    group = _groups.get(request.group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if not group.is_ready_for_fusion:
        raise HTTPException(
            status_code=400,
            detail=f"Need at least {group.min_recordings} completed recordings, have {group.completed_count}",
        )

    traces = [
        AlignedTrace(
            trace_id=s.session_id,
            session_id=s.session_id,
            operations=[],
        )
        for s in group.sessions
        if s.status == RecordingStatus.COMPLETED
    ]

    fused = _fusion_service.fuse(traces)
    if fused is None:
        raise HTTPException(status_code=500, detail="Fusion failed")

    branches = [
        FusedBranchModel(
            branch_id=b.branch_id,
            condition=b.condition,
            trace_ids=b.trace_ids,
            steps=b.steps,
        )
        for b in fused.branches
    ]

    loops = [
        LoopPatternModel(
            pattern_id=str(uuid.uuid4()),
            sequence=lp.get("sequence", ""),
            occurrences=lp.get("occurrences", 0),
            positions=lp.get("positions", []),
            trace_id=lp.get("trace_id", ""),
        )
        for lp in fused.loop_patterns
    ]

    result = FusionResult(
        result_id=str(uuid.uuid4()),
        group_id=request.group_id,
        common_steps=fused.common_steps,
        branches=branches,
        loop_patterns=loops,
        confidence=fused.confidence,
    )

    _results[result.result_id] = result
    group.status = "fused"
    return result.model_dump()


@router.get("/results")
async def list_results():
    return [r.model_dump() for r in _results.values()]


@router.get("/results/{result_id}")
async def get_result(result_id: str):
    result = _results.get(result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Fusion result not found")
    return result.model_dump()
