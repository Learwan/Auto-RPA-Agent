from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.scheduler.service import SchedulerService, TriggerType

router = APIRouter()

_scheduler = SchedulerService()


@router.post("/schedules")
async def create_schedule(
    automation_id: str,
    name: str,
    trigger_type: str = "manual",
    cron_expression: str = "",
    interval_seconds: int = 0,
):
    try:
        entry = _scheduler.create_schedule(
            automation_id=automation_id,
            name=name,
            trigger_type=TriggerType(trigger_type),
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
        )
        return entry.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/schedules")
async def list_schedules(automation_id: str | None = None):
    schedules = _scheduler.list_schedules(automation_id)
    return [s.to_dict() for s in schedules]


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str):
    entry = _scheduler.get_schedule(schedule_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return entry.to_dict()


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, **kwargs):
    entry = _scheduler.update_schedule(schedule_id, **kwargs)
    if not entry:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return entry.to_dict()


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(schedule_id: str):
    entry = _scheduler.pause_schedule(schedule_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return entry.to_dict()


@router.post("/schedules/{schedule_id}/resume")
async def resume_schedule(schedule_id: str):
    entry = _scheduler.resume_schedule(schedule_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return entry.to_dict()


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    if _scheduler.delete_schedule(schedule_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Schedule not found")
