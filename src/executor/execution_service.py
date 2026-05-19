import asyncio
import contextlib
import logging
import time
import uuid

from src.analyzer.checkpoint_support import backfill_flow_checkpoints
from src.analyzer.closure_assessor import FlowClosureAssessor, FlowClosureError
from src.analyzer.knowledge_graph import OperationPattern, WorkflowKnowledgeGraph
from src.db.database import get_session_factory
from src.db.repository import Repository
from src.executor.adaptive_workflow import AdaptiveWorkflowEngine
from src.executor.cross_platform import CrossPlatformAdapter
from src.executor.element_locator import ElementLocator
from src.executor.engine import ExecutionEngine
from src.executor.error_handler import ErrorHandler
from src.executor.execution_advisor import ExecutionAdvisor
from src.executor.intelligent_recovery import IntelligentErrorRecovery
from src.executor.self_evolution import SelfEvolutionSystem
from src.executor.step_executor import StepExecutor
from src.models.automation import AutomationFlow
from src.models.execution import ExecutionAIOptions, ExecutionFeedback, ExecutionRecord, ExecutionStatus, StepStatus
from src.platform import create_adapter_for_flow
from src.platform.base import BasePlatformAdapter

logger = logging.getLogger(__name__)

DEFAULT_EXECUTION_TIMEOUT_S = 600
MAX_ASYNC_EXECUTION_TIMEOUT_S = 1800


class ExecutionService:
    _instance: "ExecutionService | None" = None

    @classmethod
    def get_instance(cls) -> "ExecutionService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(
        self,
        repository: Repository | None = None,
        platform_adapter: BasePlatformAdapter | None = None,
        llm_service=None,
    ):
        self._repo = repository
        self._adapter_override = platform_adapter
        self._engines: dict[str, ExecutionEngine] = {}
        self._feedback_queues: dict[str, list[asyncio.Queue]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._execution_adapters: dict[str, BasePlatformAdapter] = {}
        self._recovery = IntelligentErrorRecovery()
        self._adaptive = AdaptiveWorkflowEngine()
        self._cross_platform = CrossPlatformAdapter()
        self._knowledge_graph = WorkflowKnowledgeGraph()
        self._evolution = SelfEvolutionSystem()
        self._closure_assessor = FlowClosureAssessor()

    async def _with_repo(self, operation):
        if self._repo is not None:
            return await operation(self._repo)
        factory = get_session_factory()
        async with factory() as session:
            repo = Repository(session)
            return await operation(repo)

    async def start_execution(
        self,
        automation_id: str,
        variables: dict | None = None,
        dry_run: bool = False,
        ai_options: ExecutionAIOptions | dict | None = None,
    ) -> ExecutionRecord:
        flow = await self._with_repo(lambda r: r.get_automation_flow(automation_id))
        if not flow:
            raise ValueError(f"Automation flow not found: {automation_id}")
        if not dry_run:
            self._ensure_flow_ready_for_execution(flow)

        execution_id = str(uuid.uuid4())
        adapter = self._resolve_adapter(flow)
        self._execution_adapters[execution_id] = adapter
        grounding = self._create_grounding_engine()
        locator = ElementLocator(adapter, grounding_engine=grounding)
        locator = self._wrap_with_self_healing(locator, adapter)
        step_executor = StepExecutor(locator, adapter)
        advisor = ExecutionAdvisor(self._normalize_ai_options(ai_options))
        error_handler = ErrorHandler(
            max_retries=flow.error_handling.max_retries,
            base_delay_ms=flow.error_handling.retry_delay_ms,
        )

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=error_handler,
            execution_id=execution_id,
            recovery=self._recovery,
            adaptive=self._adaptive,
            advisor=advisor if advisor.enabled else None,
        )

        self._feedback_queues.setdefault(execution_id, []).append(asyncio.Queue())
        engine.add_feedback_callback(lambda f: self._enqueue_feedback(execution_id, f))
        self._engines[execution_id] = engine

        timeout = DEFAULT_EXECUTION_TIMEOUT_S
        try:
            if dry_run:
                record = await asyncio.wait_for(engine.dry_run(variables), timeout=timeout)
            else:
                record = await asyncio.wait_for(engine.execute(variables), timeout=timeout)
        except TimeoutError:
            logger.error(f"Execution {execution_id} timed out after {timeout}s")
            record = ExecutionRecord(
                id=execution_id,
                automation_id=flow.id,
                status=ExecutionStatus.FAILED,
                started_at=time.time() - timeout,
                completed_at=time.time(),
                total_steps=len(flow.steps),
                completed_steps=engine._completed_steps,
                failed_steps=engine._failed_steps,
                error_summary=f"Execution timed out after {timeout} seconds",
                step_logs=engine._step_logs,
            )

        await self._with_repo(lambda r: r.save_execution(record))

        self._collect_execution_feedback(engine, record, flow)

        flow.execution_count += 1
        if record.status == ExecutionStatus.COMPLETED:
            flow.success_count += 1
        saved_flow = flow

        async def _save_flow(r):
            return await r.save_automation(saved_flow)

        await self._with_repo(_save_flow)

        await self._cleanup_execution(execution_id)
        return record

    async def start_execution_async(
        self,
        automation_id: str,
        variables: dict | None = None,
        ai_options: ExecutionAIOptions | dict | None = None,
    ) -> str:
        flow = await self._with_repo(lambda r: r.get_automation_flow(automation_id))
        if not flow:
            raise ValueError(f"Automation flow not found: {automation_id}")
        self._ensure_flow_ready_for_execution(flow)

        execution_id = str(uuid.uuid4())
        adapter = self._resolve_adapter(flow)
        self._execution_adapters[execution_id] = adapter
        grounding = self._create_grounding_engine()
        locator = ElementLocator(adapter, grounding_engine=grounding)
        locator = self._wrap_with_self_healing(locator, adapter)
        step_executor = StepExecutor(locator, adapter)
        advisor = ExecutionAdvisor(self._normalize_ai_options(ai_options))
        error_handler = ErrorHandler(
            max_retries=flow.error_handling.max_retries,
            base_delay_ms=flow.error_handling.retry_delay_ms,
        )

        engine = ExecutionEngine(
            flow=flow,
            step_executor=step_executor,
            error_handler=error_handler,
            execution_id=execution_id,
            recovery=self._recovery,
            adaptive=self._adaptive,
            advisor=advisor if advisor.enabled else None,
        )

        self._feedback_queues.setdefault(execution_id, []).append(asyncio.Queue())
        engine.add_feedback_callback(lambda f: self._enqueue_feedback(execution_id, f))
        self._engines[execution_id] = engine

        initial_variables = variables or engine._build_default_variables()
        initial_record = ExecutionRecord(
            id=execution_id,
            automation_id=flow.id,
            status=ExecutionStatus.RUNNING,
            started_at=time.time(),
            total_steps=len(flow.steps),
            completed_steps=0,
            failed_steps=0,
            variables=initial_variables,
            step_logs=[],
        )
        await self._with_repo(lambda r: r.save_execution(initial_record))

        async def _run():
            try:
                record = await asyncio.wait_for(
                    engine.execute(initial_variables),
                    timeout=MAX_ASYNC_EXECUTION_TIMEOUT_S,
                )
                await self._with_repo(lambda r: r.save_execution(record))
                self._collect_execution_feedback(engine, record, flow)
                flow.execution_count += 1
                if record.status == ExecutionStatus.COMPLETED:
                    flow.success_count += 1
                saved_flow = flow

                async def _save(r):
                    return await r.save_automation(saved_flow)

                await self._with_repo(_save)
            except TimeoutError:
                logger.error(f"Async execution {execution_id} timed out after {MAX_ASYNC_EXECUTION_TIMEOUT_S}s")
                timeout_record = ExecutionRecord(
                    id=execution_id,
                    automation_id=flow.id,
                    status=ExecutionStatus.FAILED,
                    started_at=initial_record.started_at,
                    completed_at=time.time(),
                    total_steps=len(flow.steps),
                    completed_steps=engine._completed_steps,
                    failed_steps=engine._failed_steps,
                    error_summary=f"Execution timed out after {MAX_ASYNC_EXECUTION_TIMEOUT_S} seconds",
                    step_logs=engine._step_logs,
                )
                await self._with_repo(lambda r: r.save_execution(timeout_record))
                self._enqueue_feedback(
                    execution_id,
                    ExecutionFeedback(
                        execution_id=execution_id,
                        current_step=engine._current_step_index,
                        total_steps=len(flow.steps),
                        step_status=StepStatus.FAILED,
                        step_description="Execution timed out",
                        error=f"Timeout after {MAX_ASYNC_EXECUTION_TIMEOUT_S}s",
                        elapsed_ms=int((time.time() - initial_record.started_at) * 1000),
                    ),
                )
            except Exception as e:
                logger.error(f"Async execution failed: {e}")
                failed_record = ExecutionRecord(
                    id=execution_id,
                    automation_id=flow.id,
                    status=ExecutionStatus.FAILED,
                    started_at=initial_record.started_at,
                    completed_at=time.time(),
                    total_steps=len(flow.steps),
                    completed_steps=engine._completed_steps,
                    failed_steps=engine._failed_steps,
                    error_summary=f"Execution crashed: {e}",
                    step_logs=engine._step_logs,
                )
                await self._with_repo(lambda r: r.save_execution(failed_record))
                self._enqueue_feedback(
                    execution_id,
                    ExecutionFeedback(
                        execution_id=execution_id,
                        current_step=engine._current_step_index,
                        total_steps=len(flow.steps),
                        step_status=StepStatus.FAILED,
                        step_description=f"Execution crashed: {e}",
                        error=str(e),
                        elapsed_ms=int((time.time() - initial_record.started_at) * 1000),
                    ),
                )
            finally:
                await self._cleanup_execution(execution_id)

        task = asyncio.ensure_future(_run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return execution_id

    def _ensure_flow_ready_for_execution(self, flow: AutomationFlow) -> None:
        backfill_flow_checkpoints(flow)
        assessment = self._closure_assessor.assess(flow)
        flow.metadata = {
            **(flow.metadata or {}),
            "closed_loop_assessment": assessment.model_dump(mode="json"),
            "closed_loop_ready": assessment.ready,
        }
        if not assessment.ready:
            raise FlowClosureError(assessment)

    async def pause_execution(self, execution_id: str) -> ExecutionRecord:
        engine = self._engines.get(execution_id)
        if not engine:
            raise ValueError(f"Execution engine not found: {execution_id}")
        await engine.pause()
        return self._engine_to_record(engine)

    async def resume_execution(self, execution_id: str) -> ExecutionRecord:
        engine = self._engines.get(execution_id)
        if not engine:
            raise ValueError(f"Execution engine not found: {execution_id}")
        await engine.resume()
        return self._engine_to_record(engine)

    async def stop_execution(self, execution_id: str) -> ExecutionRecord:
        engine = self._engines.get(execution_id)
        if not engine:
            raise ValueError(f"Execution engine not found: {execution_id}")
        await engine.stop()
        for _ in range(10):
            await asyncio.sleep(0.1)
            if engine.status in (ExecutionStatus.ABORTED, ExecutionStatus.COMPLETED, ExecutionStatus.FAILED):
                break
        return self._engine_to_record(engine)

    async def skip_step(self, execution_id: str) -> None:
        engine = self._engines.get(execution_id)
        if not engine:
            raise ValueError(f"Execution engine not found: {execution_id}")
        await engine.skip_current_step()

    async def step_over(self, execution_id: str) -> None:
        engine = self._engines.get(execution_id)
        if not engine:
            raise ValueError(f"Execution engine not found: {execution_id}")
        await engine.step_over()

    async def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        engine = self._engines.get(execution_id)
        if engine:
            return self._engine_to_record(engine)
        return await self._with_repo(lambda r: r.get_execution(execution_id))

    async def list_executions(self, limit: int = 50, offset: int = 0) -> list[ExecutionRecord]:
        persisted = await self._with_repo(lambda r: r.list_executions(limit=max(limit * 2, 50), offset=0))
        combined: dict[str, ExecutionRecord] = {record.id: record for record in persisted}
        for execution_id, engine in self._engines.items():
            combined[execution_id] = self._engine_to_record(engine)

        records = sorted(
            combined.values(),
            key=lambda record: (
                record.started_at
                or record.completed_at
                or 0.0
            ),
            reverse=True,
        )
        return records[offset : offset + limit]

    async def subscribe_feedback(self, execution_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._feedback_queues.setdefault(execution_id, []).append(queue)
        return queue

    def _enqueue_feedback(self, execution_id: str, feedback: ExecutionFeedback) -> None:
        queues = self._feedback_queues.get(execution_id, [])
        for q in queues:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(feedback)

    @staticmethod
    def _create_grounding_engine():
        try:
            from src.llm.gui_grounding import GUIGroundingEngine

            return GUIGroundingEngine()
        except Exception:
            return None

    @staticmethod
    def _wrap_with_self_healing(locator, adapter):
        try:
            from src.executor.self_healing import SelfHealingLocator
            from src.llm.service import LLMService

            llm = LLMService()
            if llm.is_configured:
                return SelfHealingLocator(locator, adapter, llm_service=llm)
        except Exception:
            pass
        return locator

    def _resolve_adapter(self, flow: AutomationFlow) -> BasePlatformAdapter:
        if self._adapter_override is not None:
            return self._adapter_override
        return create_adapter_for_flow(flow)

    @staticmethod
    def _normalize_ai_options(ai_options: ExecutionAIOptions | dict | None) -> ExecutionAIOptions:
        if isinstance(ai_options, ExecutionAIOptions):
            return ai_options
        return ExecutionAIOptions.model_validate(ai_options or {})

    async def _cleanup_execution(self, execution_id: str) -> None:
        self._engines.pop(execution_id, None)
        adapter = self._execution_adapters.pop(execution_id, None)
        if adapter is not None and adapter is not self._adapter_override:
            try:
                await adapter.close()
            except Exception as exc:
                logger.debug(f"Adapter cleanup failed for {execution_id}: {exc}")

        queues = self._feedback_queues.pop(execution_id, [])
        for q in queues:
            with contextlib.suppress(Exception):
                q.put_nowait(None)

    @staticmethod
    def _engine_to_record(engine: ExecutionEngine) -> ExecutionRecord:
        return engine._build_record()

    def _collect_execution_feedback(
        self, engine: ExecutionEngine, record: ExecutionRecord, flow: AutomationFlow
    ) -> None:
        try:
            duration = record.completed_at - record.started_at if record.started_at and record.completed_at else 0

            op_sequence = [log.step_type for log in record.step_logs if log.step_type]
            app_context = flow.window_context or ""
            if op_sequence:
                pattern = OperationPattern(
                    pattern_id=f"exec_{record.id[:12]}",
                    op_sequence=op_sequence,
                    app_name="",
                    window_context=app_context,
                    avg_duration_ms=duration * 1000 / max(len(op_sequence), 1),
                    success_rate=(
                        record.completed_steps / max(record.total_steps, 1)
                    ),
                    occurrence_count=1,
                )
                self._knowledge_graph.add_operation_pattern(pattern)

            error_msgs: list[str] = []
            for log in record.step_logs:
                if log.error_message:
                    error_msgs.append(f"[{log.step_type}] {log.error_message}")
                fb = ExecutionFeedback(
                    execution_id=record.id,
                    step_id=log.step_id,
                    step_index=log.step_index,
                    step_type=log.step_type or "unknown",
                    status=log.status.value if log.status else "unknown",
                    success=log.status == StepStatus.SUCCESS,
                    error=log.error_message,
                    action=log.step_type or "",
                    timestamp=log.completed_at or time.time(),
                    duration_ms=(
                        int((log.completed_at - log.started_at) * 1000)
                        if log.started_at and log.completed_at
                        else 0
                    ),
                    retry_attempt=0,
                    ai_healing_used=(
                        log.error_message is not None
                        and engine.ai_healing_stats.get("healing_applied", 0) > 0
                    ),
                    suggestions=[],
                )
                self._evolution.record_feedback(fb)

            logger.info(
                f"Evolution + Knowledge Graph updated: "
                f"flow={flow.id}, status={record.status.value}, "
                f"healing={engine.ai_healing_stats.get('healing_applied', 0)}"
            )
        except Exception as e:
            logger.debug(f"Feedback collection skipped: {e}")

    @property
    def recovery(self) -> IntelligentErrorRecovery:
        return self._recovery

    @property
    def adaptive(self) -> AdaptiveWorkflowEngine:
        return self._adaptive

    @property
    def knowledge_graph(self) -> WorkflowKnowledgeGraph:
        return self._knowledge_graph

    @property
    def evolution(self) -> SelfEvolutionSystem:
        return self._evolution
