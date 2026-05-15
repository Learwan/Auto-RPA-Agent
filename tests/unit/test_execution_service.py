import pytest

from src.analyzer.closure_assessor import FlowClosureError
from src.executor.execution_service import ExecutionService
from src.models.automation import AutomationFlow, AutomationStep, LocateStrategy, StepTarget, StepType
from src.models.execution import ExecutionRecord, ExecutionStatus


class FakeRepo:
    def __init__(self, flow=None):
        self.flow = flow

    async def get_automation_flow(self, automation_id):
        return self.flow

    async def list_executions(self, limit=50, offset=0):
        return [
            ExecutionRecord(
                id="persisted-1",
                automation_id="flow-1",
                status=ExecutionStatus.COMPLETED,
                started_at=100.0,
                completed_at=110.0,
                total_steps=1,
                completed_steps=1,
                failed_steps=0,
            )
        ]


class FakeEngine:
    def __init__(self, record):
        self._record = record

    def _build_record(self):
        return self._record


def _weak_flow() -> AutomationFlow:
    return AutomationFlow(
        id="flow-weak",
        name="Weak Flow",
        confidence=0.82,
        steps=[
            AutomationStep(
                id="step-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(
                    strategy=LocateStrategy.POSITION,
                    position={"x": 120, "y": 240},
                    window_title="编辑商品",
                ),
                description="点击保存",
            )
        ],
    )


@pytest.mark.asyncio
async def test_list_executions_merges_live_engine_records():
    service = ExecutionService(repository=FakeRepo())
    service._engines["running-1"] = FakeEngine(
        ExecutionRecord(
            id="running-1",
            automation_id="flow-live",
            status=ExecutionStatus.RUNNING,
            started_at=200.0,
            total_steps=5,
            completed_steps=0,
            failed_steps=0,
        )
    )

    records = await service.list_executions(limit=10, offset=0)

    assert [record.id for record in records[:2]] == ["running-1", "persisted-1"]


@pytest.mark.asyncio
async def test_start_execution_async_blocks_uncertified_flow():
    service = ExecutionService(repository=FakeRepo(flow=_weak_flow()))

    with pytest.raises(FlowClosureError) as exc_info:
        await service.start_execution_async("flow-weak")

    assert exc_info.value.assessment.ready is False
    assert any(issue.code == "position_only_target" for issue in exc_info.value.assessment.issues)
    assert not any(issue.code == "missing_preconditions" for issue in exc_info.value.assessment.issues)
    assert not any(issue.code == "missing_postconditions" for issue in exc_info.value.assessment.issues)
