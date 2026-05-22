import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AutomationModel, ExecutionModel, ExecutionStepModel, OperationModel, SessionModel
from src.models.automation import AutomationFlow
from src.models.execution import ExecutionRecord, ExecutionStatus, ExecutionStepLog, ExecutionSummary, StepStatus
from src.models.operation import OperationEvent
from src.models.session import Session, SessionStatus

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _uuid() -> str:
    return uuid4().hex


class Repository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_session(self, name: str, description: str = "", tags: list[str] | None = None) -> Session:
        session_id = _uuid()
        model = SessionModel(
            id=session_id,
            name=name,
            description=description,
            tags=json.dumps(tags or []),
            status=SessionStatus.CREATED.value,
        )
        self._session.add(model)
        await self._session.commit()
        return Session(id=session_id, name=name, description=description, tags=tags or [])

    async def get_session(self, session_id: str) -> Session | None:
        result = await self._session.execute(select(SessionModel).where(SessionModel.id == session_id))
        model = result.scalar_one_or_none()
        if not model:
            return None
        return Session(
            id=model.id,
            name=model.name,
            description=model.description or "",
            tags=json.loads(model.tags) if model.tags else [],
            status=SessionStatus(model.status),
            started_at=model.started_at.timestamp() if model.started_at else None,
            stopped_at=model.stopped_at.timestamp() if model.stopped_at else None,
            duration_ms=model.duration_ms or 0,
            operation_count=model.operation_count or 0,
        )

    async def list_sessions(self, limit: int = 50, offset: int = 0) -> list[Session]:
        result = await self._session.execute(
            select(SessionModel).order_by(SessionModel.created_at.desc()).offset(offset).limit(limit)
        )
        models = result.scalars().all()
        return [
            Session(
                id=m.id,
                name=m.name,
                description=m.description or "",
                tags=json.loads(m.tags) if m.tags else [],
                status=SessionStatus(m.status),
                started_at=m.started_at.timestamp() if m.started_at else None,
                stopped_at=m.stopped_at.timestamp() if m.stopped_at else None,
                duration_ms=m.duration_ms or 0,
                operation_count=m.operation_count or 0,
            )
            for m in models
        ]

    async def update_session_status(self, session_id: str, status: str, **kwargs: Any) -> None:
        values: dict[str, Any] = {"status": status}
        values.update(kwargs)
        await self._session.execute(update(SessionModel).where(SessionModel.id == session_id).values(**values))
        await self._session.commit()

    async def delete_session(self, session_id: str) -> None:
        automations = await self._session.execute(
            select(AutomationModel).where(AutomationModel.source_session_id == session_id)
        )
        automation_ids = [auto.id for auto in automations.scalars().all()]
        if automation_ids:
            execution_rows = await self._session.execute(
                select(ExecutionModel.id).where(ExecutionModel.automation_id.in_(automation_ids))
            )
            execution_ids = list(execution_rows.scalars().all())
            if execution_ids:
                await self._session.execute(
                    delete(ExecutionStepModel).where(ExecutionStepModel.execution_id.in_(execution_ids))
                )
                await self._session.execute(delete(ExecutionModel).where(ExecutionModel.id.in_(execution_ids)))
            await self._session.execute(delete(AutomationModel).where(AutomationModel.id.in_(automation_ids)))
        await self._session.execute(delete(OperationModel).where(OperationModel.session_id == session_id))
        await self._session.execute(delete(SessionModel).where(SessionModel.id == session_id))
        await self._session.commit()

    async def add_operation(self, event: OperationEvent) -> None:
        model = OperationModel(
            id=event.id,
            session_id=event.session_id,
            seq_num=event.seq_num,
            timestamp=event.timestamp,
            type=event.type.value,
            data=event.data.model_dump_json(),
            context=event.context.model_dump_json() if event.context else None,
        )
        self._session.add(model)
        await self._session.commit()

    async def add_operations_batch(self, events: list[OperationEvent]) -> None:
        models = [
            OperationModel(
                id=e.id,
                session_id=e.session_id,
                seq_num=e.seq_num,
                timestamp=e.timestamp,
                type=e.type.value,
                data=e.data.model_dump_json(),
                context=e.context.model_dump_json() if e.context else None,
            )
            for e in events
        ]
        self._session.add_all(models)
        await self._session.commit()

    async def get_operations(self, session_id: str, limit: int = 100, offset: int = 0) -> list[OperationModel]:
        result = await self._session.execute(
            select(OperationModel)
            .where(OperationModel.session_id == session_id)
            .order_by(OperationModel.seq_num)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_operations(self, session_id: str) -> int:
        result = await self._session.execute(select(func.count()).where(OperationModel.session_id == session_id))
        return result.scalar() or 0

    async def save_automation(self, flow: AutomationFlow) -> AutomationFlow:
        existing = await self._session.execute(select(AutomationModel).where(AutomationModel.id == flow.id))
        existing_model = existing.scalar_one_or_none()
        if existing_model:
            existing_model.execution_count = flow.execution_count
            existing_model.success_count = flow.success_count
            existing_model.confidence = flow.confidence
            existing_model.status = flow.status
            existing_model.flow_definition = flow.model_dump_json()
            await self._session.commit()
            return flow
        model = AutomationModel(
            id=flow.id,
            name=flow.name,
            description=flow.description,
            source_session_id=flow.source_session_id,
            source_pattern=json.dumps(flow.source_pattern) if flow.source_pattern else None,
            flow_definition=flow.model_dump_json(),
            confidence=flow.confidence,
            status=flow.status,
            execution_count=flow.execution_count,
            success_count=flow.success_count,
        )
        self._session.add(model)
        await self._session.commit()
        return flow

    async def get_automation(self, flow_id: str) -> AutomationFlow | None:
        result = await self._session.execute(select(AutomationModel).where(AutomationModel.id == flow_id))
        model = result.scalar_one_or_none()
        if not model:
            return None
        flow = AutomationFlow.model_validate_json(model.flow_definition)
        flow.execution_count = model.execution_count or 0
        flow.success_count = model.success_count or 0
        return flow

    async def list_automations(self, limit: int = 50, offset: int = 0) -> list[AutomationFlow]:
        result = await self._session.execute(
            select(AutomationModel).order_by(AutomationModel.created_at.desc()).offset(offset).limit(limit)
        )
        models = result.scalars().all()
        return [AutomationFlow.model_validate_json(m.flow_definition) for m in models]

    async def list_automation_flows_by_session(self, session_id: str) -> list[AutomationFlow]:
        result = await self._session.execute(
            select(AutomationModel)
            .where(AutomationModel.source_session_id == session_id)
            .order_by(AutomationModel.created_at.desc())
        )
        models = result.scalars().all()
        return [AutomationFlow.model_validate_json(m.flow_definition) for m in models]

    async def delete_automation(self, flow_id: str) -> None:
        existing = await self._session.execute(select(AutomationModel.id).where(AutomationModel.id == flow_id))
        if existing.scalar_one_or_none() is None:
            raise ValueError(f"Automation flow not found: {flow_id}")

        execution_rows = await self._session.execute(
            select(ExecutionModel.id).where(ExecutionModel.automation_id == flow_id)
        )
        execution_ids = list(execution_rows.scalars().all())
        if execution_ids:
            await self._session.execute(
                delete(ExecutionStepModel).where(ExecutionStepModel.execution_id.in_(execution_ids))
            )
            await self._session.execute(delete(ExecutionModel).where(ExecutionModel.id.in_(execution_ids)))
        try:
            await self._session.execute(delete(AutomationModel).where(AutomationModel.id == flow_id))
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

    async def delete_automation_flows(self, flow_ids: list[str]) -> None:
        unique_ids = list(dict.fromkeys(flow_ids))
        if not unique_ids:
            return

        execution_rows = await self._session.execute(
            select(ExecutionModel.id).where(ExecutionModel.automation_id.in_(unique_ids))
        )
        execution_ids = list(execution_rows.scalars().all())

        try:
            if execution_ids:
                await self._session.execute(
                    delete(ExecutionStepModel).where(ExecutionStepModel.execution_id.in_(execution_ids))
                )
                await self._session.execute(delete(ExecutionModel).where(ExecutionModel.id.in_(execution_ids)))
            await self._session.execute(delete(AutomationModel).where(AutomationModel.id.in_(unique_ids)))
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

    async def save_execution(self, record: ExecutionRecord) -> None:
        def _to_datetime(val: float | None) -> datetime | None:
            if val is None:
                return None
            return datetime.fromtimestamp(val, tz=UTC)

        variables_payload = dict(record.variables or {})
        ai_meta = {}
        if record.ai_summary is not None:
            ai_meta["summary"] = record.ai_summary.model_dump()
        if record.ai_step_insights:
            ai_meta["step_insights"] = record.ai_step_insights
        if ai_meta:
            variables_payload["__execution_ai"] = ai_meta

        payload = {
            "automation_id": record.automation_id,
            "status": record.status.value,
            "started_at": _to_datetime(record.started_at),
            "completed_at": _to_datetime(record.completed_at),
            "total_steps": record.total_steps,
            "completed_steps": record.completed_steps,
            "failed_steps": record.failed_steps,
            "error_summary": record.error_summary,
            "variables": json.dumps(variables_payload) if variables_payload else None,
        }

        existing = await self._session.execute(select(ExecutionModel).where(ExecutionModel.id == record.id))
        model = existing.scalar_one_or_none()
        if model is None:
            model = ExecutionModel(id=record.id, **payload)
            self._session.add(model)
        else:
            for key, value in payload.items():
                setattr(model, key, value)

        if record.step_logs is not None:
            await self._session.execute(delete(ExecutionStepModel).where(ExecutionStepModel.execution_id == record.id))
            step_models = [
                ExecutionStepModel(
                    id=step.id,
                    execution_id=record.id,
                    step_id=step.step_id,
                    step_type=step.step_type,
                    step_index=step.step_index,
                    status=step.status.value,
                    started_at=_to_datetime(step.started_at),
                    completed_at=_to_datetime(step.completed_at),
                    screenshot_before=step.screenshot_before,
                    screenshot_after=step.screenshot_after,
                    error_message=step.error_message,
                    metadata_json=json.dumps(step.metadata) if step.metadata else None,
                )
                for step in record.step_logs
            ]
            if step_models:
                self._session.add_all(step_models)

        await self._session.commit()

    async def update_execution(self, execution_id: str, **kwargs: Any) -> None:
        await self._session.execute(update(ExecutionModel).where(ExecutionModel.id == execution_id).values(**kwargs))
        await self._session.commit()

    async def get_operations_by_session(self, session_id: str) -> list[OperationEvent]:
        result = await self._session.execute(
            select(OperationModel).where(OperationModel.session_id == session_id).order_by(OperationModel.seq_num)
        )
        models = result.scalars().all()
        events: list[OperationEvent] = []
        for m in models:
            try:
                from src.models.operation import (
                    ClipboardEventData,
                    FileEventData,
                    KeyboardEventData,
                    MouseEventData,
                    NavigationEventData,
                    OperationContext,
                    OperationType,
                    WindowEventData,
                )

                op_type = OperationType(m.type)
                data_cls = {
                    OperationType.MOUSE_CLICK: MouseEventData,
                    OperationType.MOUSE_SCROLL: MouseEventData,
                    OperationType.MOUSE_DRAG: MouseEventData,
                    OperationType.KEY_PRESS: KeyboardEventData,
                    OperationType.KEY_INPUT: KeyboardEventData,
                    OperationType.WINDOW_SWITCH: WindowEventData,
                    OperationType.NAVIGATION: NavigationEventData,
                    OperationType.FILE_OP: FileEventData,
                    OperationType.CLIPBOARD: ClipboardEventData,
                }.get(op_type, MouseEventData)
                data = data_cls.model_validate_json(m.data) if m.data else MouseEventData(x=0, y=0)
                context = OperationContext.model_validate_json(m.context) if m.context else OperationContext()
                events.append(
                    OperationEvent(
                        id=m.id,
                        session_id=m.session_id,
                        seq_num=m.seq_num,
                        timestamp=m.timestamp,
                        type=op_type,
                        data=data,
                        context=context,
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to parse operation {m.id}: {e}")
                continue
        return events

    async def get_automation_flow(self, flow_id: str) -> AutomationFlow | None:
        return await self.get_automation(flow_id)

    async def save_automation_flow(self, flow: AutomationFlow) -> AutomationFlow:
        return await self.save_automation(flow)

    async def get_execution_record(self, execution_id: str) -> ExecutionRecord | None:
        result = await self._session.execute(select(ExecutionModel).where(ExecutionModel.id == execution_id))
        model = result.scalar_one_or_none()
        if not model:
            return None
        steps = await self._session.execute(
            select(ExecutionStepModel)
            .where(ExecutionStepModel.execution_id == execution_id)
            .order_by(ExecutionStepModel.step_index)
        )
        return self._build_execution_record(model, list(steps.scalars().all()))

    async def list_execution_records(self, limit: int = 50, offset: int = 0) -> list[ExecutionRecord]:
        result = await self._session.execute(
            select(ExecutionModel).order_by(ExecutionModel.created_at.desc()).offset(offset).limit(limit)
        )
        models = result.scalars().all()
        if not models:
            return []

        step_result = await self._session.execute(
            select(ExecutionStepModel)
            .where(ExecutionStepModel.execution_id.in_([m.id for m in models]))
            .order_by(ExecutionStepModel.execution_id, ExecutionStepModel.step_index)
        )
        step_groups: dict[str, list[ExecutionStepModel]] = defaultdict(list)
        for step in step_result.scalars().all():
            step_groups[step.execution_id].append(step)

        return [self._build_execution_record(model, step_groups.get(model.id, [])) for model in models]

    async def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        return await self.get_execution_record(execution_id)

    async def list_executions(self, limit: int = 50, offset: int = 0) -> list[ExecutionRecord]:
        return await self.list_execution_records(limit, offset)

    @staticmethod
    def _to_timestamp(val: Any) -> float | None:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        return val.timestamp()

    def _build_execution_record(self, model: ExecutionModel, step_models: list[ExecutionStepModel]) -> ExecutionRecord:
        variables = json.loads(model.variables) if model.variables else None
        ai_meta = variables.get("__execution_ai", {}) if isinstance(variables, dict) else {}
        user_variables = (
            {key: value for key, value in variables.items() if key != "__execution_ai"}
            if isinstance(variables, dict)
            else None
        )

        step_logs = []
        for step in step_models:
            metadata = {}
            if step.metadata_json:
                try:
                    parsed = json.loads(step.metadata_json)
                    metadata = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    logger.warning("Failed to decode execution step metadata for %s", step.id)
            step_logs.append(
                ExecutionStepLog(
                    id=step.id,
                    execution_id=step.execution_id,
                    step_id=step.step_id,
                    step_type=step.step_type,
                    step_index=step.step_index,
                    status=StepStatus(step.status),
                    started_at=self._to_timestamp(step.started_at),
                    completed_at=self._to_timestamp(step.completed_at),
                    screenshot_before=step.screenshot_before,
                    screenshot_after=step.screenshot_after,
                    error_message=step.error_message,
                    metadata=metadata,
                )
            )

        return ExecutionRecord(
            id=model.id,
            automation_id=model.automation_id,
            status=ExecutionStatus(model.status),
            started_at=self._to_timestamp(model.started_at),
            completed_at=self._to_timestamp(model.completed_at),
            total_steps=model.total_steps or 0,
            completed_steps=model.completed_steps or 0,
            failed_steps=model.failed_steps or 0,
            error_summary=model.error_summary,
            variables=user_variables or {},
            step_logs=step_logs,
            ai_summary=ExecutionSummary.model_validate(ai_meta["summary"]) if ai_meta.get("summary") else None,
            ai_step_insights=ai_meta.get("step_insights"),
        )
