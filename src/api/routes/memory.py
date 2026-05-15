from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from src.timeline.service import TimelineMemoryService

router = APIRouter()

_memory_service = TimelineMemoryService()


class MemoryTimelineItem(BaseModel):
    id: str
    kind: str
    timestamp: int
    session_id: str | None = None
    session_name: str | None = None
    title: str
    summary: str
    app_name: str | None = None
    window_title: str | None = None
    url: str | None = None
    operation_type: str | None = None
    snapshot_id: str | None = None
    preview_url: str | None = None
    score: float = 0.0
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class MemoryTimelineResponse(BaseModel):
    query: str = ""
    items: list[MemoryTimelineItem] = Field(default_factory=list)
    total: int = 0
    scope: str = "global"


@router.get("/timeline", response_model=MemoryTimelineResponse)
async def query_timeline(
    query: str = "",
    session_id: str | None = None,
    limit: int = Query(default=12, ge=1, le=50),
    lookback_minutes: int = Query(default=120, ge=1, le=24 * 60),
    include_operations: bool = True,
    include_snapshots: bool = True,
):
    items = await _memory_service.query_timeline(
        query=query,
        session_id=session_id,
        limit=limit,
        lookback_minutes=lookback_minutes,
        include_operations=include_operations,
        include_snapshots=include_snapshots,
    )
    return MemoryTimelineResponse(
        query=query,
        items=[MemoryTimelineItem.model_validate(item) for item in items],
        total=len(items),
        scope="session" if session_id else "global",
    )


@router.get("/snapshots/{snapshot_id}/image")
async def get_snapshot_image(snapshot_id: str):
    image = _memory_service.get_snapshot_image(snapshot_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Snapshot image not found")
    return Response(content=image, media_type="image/png")