import asyncio
import base64
import logging
import time
import uuid
from collections import deque

from src.executor.adaptive_workflow import AdaptiveWorkflowEngine
from src.executor.error_handler import ErrorHandler, ExecutionContext
from src.executor.execution_advisor import ExecutionAdvisor
from src.executor.intelligent_recovery import IntelligentErrorRecovery
from src.executor.step_executor import StepExecutor, StepResult
from src.models.automation import AutomationFlow, AutomationStep, ErrorAction
from src.models.execution import (
    ExecutionFeedback,
    ExecutionRecord,
    ExecutionStatus,
    ExecutionStepLog,
    StepStatus,
    VerificationResult,
)

logger = logging.getLogger(__name__)

ASK_USER_TIMEOUT_S = 300
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_RESET_S = 60


class CircuitBreaker:
    def __init__(self, threshold: int = CIRCUIT_BREAKER_THRESHOLD, reset_seconds: float = CIRCUIT_BREAKER_RESET_S):
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._threshold:
            self._state = "open"

    def is_open(self) -> bool:
        if self._state == "open":
            if time.time() - self._last_failure_time > self._reset_seconds:
                self._state = "half_open"
                return False
            return True
        return False

    @property
    def state(self) -> str:
        return self._state


class ExecutionEngine:
    def __init__(
        self,
        flow: AutomationFlow,
        step_executor: StepExecutor,
        error_handler: ErrorHandler,
        execution_id: str | None = None,
        recovery: IntelligentErrorRecovery | None = None,
        adaptive: AdaptiveWorkflowEngine | None = None,
        advisor: ExecutionAdvisor | None = None,
    ):
        self._flow = flow
        self._executor = step_executor
        self._error_handler = error_handler
        self._recovery = recovery
        self._adaptive = adaptive
        self._advisor = advisor
        self._execution_id = execution_id or str(uuid.uuid4())
        self._status = ExecutionStatus.PENDING
        self._current_step_index = -1
        self._variables: dict = {}
        self._step_logs: list[ExecutionStepLog] = []
        self._started_at: float | None = None
        self._completed_at: float | None = None
        self._failed_steps = 0
        self._completed_steps = 0
        self._retry_counts: dict[str, int] = {}
        self._skip_current = False
        self._step_over_mode = False
        self._feedback_callbacks: list = []
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False
        self._last_verification_result: VerificationResult | None = None
        self._circuit_breaker = CircuitBreaker()
        self._ai_healing_applied = 0
        self._ai_healing_attempts = 0
        self._ai_healing_failures = 0
        self._ai_summary = None

    @property
    def status(self) -> ExecutionStatus:
        return self._status

    @property
    def current_step_index(self) -> int:
        return self._current_step_index

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def flow(self) -> AutomationFlow:
        return self._flow

    def add_feedback_callback(self, callback) -> None:
        self._feedback_callbacks.append(callback)

    @staticmethod
    def _encode_screenshot(image: bytes | str | None) -> str | None:
        if image is None:
            return None
        if isinstance(image, str):
            return image
        if not image:
            return None
        return base64.b64encode(image).decode("utf-8")

    def _decorate_step_result(self, result: StepResult) -> StepResult:
        result.step_log.screenshot_before = self._encode_screenshot(result.screenshot_before)
        result.step_log.screenshot_after = self._encode_screenshot(result.screenshot_after)
        if result.visual_comparison:
            result.step_log.visual_comparison = result.visual_comparison
        if result.verification_result:
            result.step_log.verification_result = result.verification_result
        return result

    async def execute(self, variables: dict | None = None) -> ExecutionRecord:
        self._variables = variables or self._build_default_variables()
        self._status = ExecutionStatus.RUNNING
        self._started_at = time.time()
        self._stop_requested = False
        self._executor.safety_controller.reset()

        await self._emit_feedback(self._create_feedback(0, StepStatus.RUNNING, "Execution started"))

        step_map = self._flow.step_map()
        queue: deque[str] = deque(self._build_step_queue())
        queued_step_ids = set(queue)
        resolved_step_ids: set[str] = set()
        executed_step_ids: set[str] = set()
        retry_guard: dict[str, int] = {}
        runtime_index = 0

        while queue:
            step_id = queue.popleft()
            queued_step_ids.discard(step_id)
            step = step_map.get(step_id)
            if step is None or step_id in executed_step_ids:
                continue

            unmet_dependencies = [dependency for dependency in step.depends_on if dependency not in resolved_step_ids]
            if unmet_dependencies:
                retry_guard[step_id] = retry_guard.get(step_id, 0) + 1
                if retry_guard[step_id] > max(len(self._flow.steps), 1):
                    raise RuntimeError(
                        f"Unresolvable workflow dependencies for step {step_id}: {', '.join(unmet_dependencies)}"
                    )
                queue.append(step_id)
                queued_step_ids.add(step_id)
                continue

            executed_step_ids.add(step_id)
            if self._stop_requested:
                self._status = ExecutionStatus.ABORTED
                break

            if self._circuit_breaker.is_open():
                logger.warning("Circuit breaker is open, aborting execution")
                self._status = ExecutionStatus.FAILED
                break

            await self._pause_event.wait()
            if self._stop_requested:
                self._status = ExecutionStatus.ABORTED
                break

            self._current_step_index = runtime_index
            await self._emit_pre_step_feedback(step, runtime_index)
            if self._skip_current:
                self._skip_current = False
                self._failed_steps += 1
                resolved_step_ids.add(step.id)
                await self._emit_feedback(
                    self._create_feedback(
                        runtime_index,
                        StepStatus.SKIPPED,
                        "Step skipped by user",
                        step_id=step.id,
                        step_type=step.type.value,
                    )
                )
                self._enqueue_next_steps(step, None, queue, queued_step_ids, executed_step_ids, step_map)
                runtime_index += 1
                continue

            result = await self._execute_step_with_retry(step, runtime_index)
            resolved_step_ids.add(step.id)

            if result.success:
                self._completed_steps += 1
                self._circuit_breaker.record_success()
            elif result.step_log.status == StepStatus.SKIPPED:
                self._circuit_breaker.record_success()
            else:
                self._failed_steps += 1
                self._circuit_breaker.record_failure()

            if step.delay > 0 and result.success:
                await asyncio.sleep(step.delay / 1000.0)

            if self._step_over_mode and result.success:
                self._step_over_mode = False
                await self.pause()

            self._enqueue_next_steps(step, result, queue, queued_step_ids, executed_step_ids, step_map)
            runtime_index += 1

        # Add placeholder logs for steps that were never executed
        # (e.g. execution aborted by circuit breaker or stop request)
        executed_step_ids_set = {log.step_id for log in self._step_logs}
        for step in self._flow.steps:
            if step.id not in executed_step_ids_set:
                self._step_logs.append(
                    ExecutionStepLog(
                        id=str(uuid.uuid4()),
                        execution_id=self._execution_id,
                        step_id=step.id,
                        step_type=step.type,
                        step_index=step.execution_order or 0,
                        status=StepStatus.PENDING,
                        error_message="Step was not executed (execution interrupted)",
                    )
                )

        if self._status == ExecutionStatus.RUNNING:
            if self._failed_steps == 0:
                self._status = ExecutionStatus.COMPLETED
            elif self._completed_steps == 0:
                self._status = ExecutionStatus.FAILED
            else:
                self._status = (
                    ExecutionStatus.COMPLETED if self._failed_steps < self._completed_steps else ExecutionStatus.FAILED
                )

        self._completed_at = time.time()
        final_error = None
        if self._status == ExecutionStatus.FAILED:
            if self._circuit_breaker.is_open():
                final_error = "Circuit breaker triggered: too many consecutive failures"
            elif self._failed_steps > 0:
                final_error = (
                    f"Execution failed: {self._failed_steps} step(s) failed, "
                    f"{self._completed_steps} completed"
                )
        await self._emit_feedback(
            self._create_feedback(
                self._current_step_index,
                StepStatus.SUCCESS if self._status == ExecutionStatus.COMPLETED else StepStatus.FAILED,
                f"Execution {self._status.value}",
                error=final_error,
            )
        )

        record = self._build_record()
        if self._advisor:
            self._ai_summary = await self._advisor.build_summary(self._flow, record)
            self._ai_summary = self._augment_ai_summary(self._ai_summary)
            record.ai_summary = self._ai_summary
            record.ai_step_insights = self._advisor.step_insights or None
        return record

    async def dry_run(self, variables: dict | None = None) -> ExecutionRecord:
        self._variables = variables or self._build_default_variables()
        self._status = ExecutionStatus.RUNNING
        self._started_at = time.time()

        step_map = self._flow.step_map()
        queue = self._build_step_queue()
        queued_step_ids = set(queue)
        resolved_step_ids: set[str] = set()
        executed_step_ids: set[str] = set()
        retry_guard: dict[str, int] = {}
        runtime_index = 0

        while queue:
            step_id = queue.pop(0)
            queued_step_ids.discard(step_id)
            step = step_map.get(step_id)
            if step is None or step_id in executed_step_ids:
                continue

            unmet_dependencies = [dependency for dependency in step.depends_on if dependency not in resolved_step_ids]
            if unmet_dependencies:
                retry_guard[step_id] = retry_guard.get(step_id, 0) + 1
                if retry_guard[step_id] > max(len(self._flow.steps), 1):
                    raise RuntimeError(
                        f"Unresolvable workflow dependencies for step {step_id}: {', '.join(unmet_dependencies)}"
                    )
                queue.append(step_id)
                queued_step_ids.add(step_id)
                continue

            executed_step_ids.add(step_id)
            self._current_step_index = runtime_index
            result = await self._executor.dry_run_step(step, self._variables, self._execution_id, runtime_index)
            result = self._decorate_step_result(result)
            self._step_logs.append(result.step_log)
            resolved_step_ids.add(step.id)

            if result.success:
                self._completed_steps += 1
            else:
                self._failed_steps += 1

            self._enqueue_next_steps(step, result, queue, queued_step_ids, executed_step_ids, step_map)
            runtime_index += 1

        self._status = ExecutionStatus.COMPLETED
        self._completed_at = time.time()
        record = self._build_record()
        if self._advisor:
            self._ai_summary = await self._advisor.build_summary(self._flow, record)
            self._ai_summary = self._augment_ai_summary(self._ai_summary)
            record.ai_summary = self._ai_summary
            record.ai_step_insights = self._advisor.step_insights or None
        return record

    async def pause(self) -> None:
        if self._status == ExecutionStatus.RUNNING:
            self._status = ExecutionStatus.PAUSED
            self._pause_event.clear()
            logger.info(f"Execution {self._execution_id} paused at step {self._current_step_index}")

    async def resume(self) -> None:
        if self._status == ExecutionStatus.PAUSED:
            self._status = ExecutionStatus.RUNNING
            self._pause_event.set()
            self._executor.safety_controller.reset()
            logger.info(f"Execution {self._execution_id} resumed")

    async def stop(self) -> None:
        self._stop_requested = True
        self._pause_event.set()
        logger.info(f"Execution {self._execution_id} stop requested")

    async def skip_current_step(self) -> None:
        self._skip_current = True
        self._pause_event.set()

    async def step_over(self) -> None:
        self._step_over_mode = True
        self._pause_event.set()

    async def _execute_step_with_retry(self, step: AutomationStep, index: int) -> StepResult:
        max_attempts = step.retry_count + 1
        last_result = None

        for _attempt in range(max_attempts):
            if self._stop_requested:
                break

            context = ExecutionContext(
                execution_id=self._execution_id,
                current_step_index=index,
                total_steps=len(self._flow.steps),
                retry_counts=self._retry_counts,
                failed_steps=self._failed_steps,
                verification_result=self._last_verification_result,
            )

            try:
                result = await self._executor.execute_step(step, self._variables, self._execution_id, index)
                result = self._decorate_step_result(result)
                self._step_logs.append(result.step_log)
                post_advice, assist_advice = await self._build_step_advice(step, result, index)

                if result.success:
                    self._last_verification_result = None
                    await self._emit_feedback(
                        self._create_feedback(
                            index,
                            StepStatus.SUCCESS,
                            step.description or f"Step {index} completed",
                            step_id=step.id,
                            step_type=step.type.value,
                            visual_comparison=result.visual_comparison,
                            ai_check=post_advice.model_dump() if post_advice else None,
                        )
                    )
                    return result

                last_result = result
                if result.verification_result:
                    try:
                        self._last_verification_result = VerificationResult.model_validate(result.verification_result)
                    except Exception:
                        self._last_verification_result = None

                error = RuntimeError(result.step_log.error_message or "Step failed")
                context.verification_result = self._last_verification_result
                decision = await self._error_handler.handle_error(step, error, context)

                if decision.action == ErrorAction.RETRY:
                    self._retry_counts[step.id] = self._retry_counts.get(step.id, 0) + 1
                    await self._emit_feedback(
                        self._create_feedback(
                            index,
                            StepStatus.RUNNING,
                            f"Retrying (attempt {self._retry_counts[step.id]}): {decision.message}",
                            step_id=step.id,
                            step_type=step.type.value,
                            error=str(error),
                            ai_check=post_advice.model_dump() if post_advice else None,
                            ai_assist=assist_advice.model_dump() if assist_advice else None,
                        )
                    )
                    if decision.delay_ms > 0:
                        await asyncio.sleep(decision.delay_ms / 1000.0)
                    continue
                elif decision.action == ErrorAction.SKIP:
                    result.step_log.status = StepStatus.SKIPPED
                    await self._emit_feedback(
                        self._create_feedback(
                            index,
                            StepStatus.SKIPPED,
                            decision.message,
                            step_id=step.id,
                            step_type=step.type.value,
                            error=str(error),
                            ai_check=post_advice.model_dump() if post_advice else None,
                            ai_assist=assist_advice.model_dump() if assist_advice else None,
                        )
                    )
                    return result
                elif decision.action == ErrorAction.ABORT:
                    await self._emit_feedback(
                        self._create_feedback(
                            index,
                            StepStatus.FAILED,
                            f"Aborted: {decision.message}",
                            step_id=step.id,
                            step_type=step.type.value,
                            error=str(error),
                            ai_check=post_advice.model_dump() if post_advice else None,
                            ai_assist=assist_advice.model_dump() if assist_advice else None,
                        )
                    )
                    self._stop_requested = True
                    return result
                elif decision.action == ErrorAction.ASK_USER:
                    await self._emit_feedback(
                        self._create_feedback(
                            index,
                            StepStatus.PAUSED,
                            f"Waiting for user decision: {decision.message}",
                            step_id=step.id,
                            step_type=step.type.value,
                            error=str(error),
                            ai_check=post_advice.model_dump() if post_advice else None,
                            ai_assist=assist_advice.model_dump() if assist_advice else None,
                        )
                    )
                    await self.pause()
                    try:
                        await asyncio.wait_for(self._pause_event.wait(), timeout=ASK_USER_TIMEOUT_S)
                    except TimeoutError:
                        logger.warning("ASK_USER timeout after %ds, skipping step", ASK_USER_TIMEOUT_S)
                        await self._emit_feedback(
                            self._create_feedback(
                                index,
                                StepStatus.SKIPPED,
                                "User response timeout",
                                step_id=step.id,
                                step_type=step.type.value,
                                error=str(error),
                            )
                        )
                        return result
                    continue
                elif decision.action == ErrorAction.RELOCATE or decision.action == ErrorAction.ADJUST_AND_RETRY:
                    self._retry_counts[step.id] = self._retry_counts.get(step.id, 0) + 1
                    if decision.delay_ms > 0:
                        await asyncio.sleep(decision.delay_ms / 1000.0)
                    continue
                else:
                    return result

            except Exception as e:
                context.verification_result = self._last_verification_result
                decision = await self._error_handler.handle_error(step, e, context)
                if decision.action == ErrorAction.RETRY:
                    self._retry_counts[step.id] = self._retry_counts.get(step.id, 0) + 1
                    if decision.delay_ms > 0:
                        await asyncio.sleep(decision.delay_ms / 1000.0)
                    continue
                elif decision.action == ErrorAction.ABORT:
                    self._stop_requested = True
                    break
                elif decision.action == ErrorAction.RELOCATE or decision.action == ErrorAction.ADJUST_AND_RETRY:
                    self._retry_counts[step.id] = self._retry_counts.get(step.id, 0) + 1
                    if decision.delay_ms > 0:
                        await asyncio.sleep(decision.delay_ms / 1000.0)
                    continue
                else:
                    break

        if last_result:
            if self._recovery and not last_result.success:
                try:
                    last_result = await self._apply_ai_healing(step, last_result, index)
                except Exception as e:
                    logger.debug(f"AI healing failed: {e}")
            return last_result

        if self._recovery:
            try:
                fallback = StepResult(
                    success=False,
                    step_log=ExecutionStepLog(
                        id=str(uuid.uuid4()),
                        execution_id=self._execution_id,
                        step_id=step.id,
                        step_type=step.type.value,
                        step_index=index,
                        status=StepStatus.FAILED,
                        started_at=time.time(),
                        completed_at=time.time(),
                        error_message="All retry attempts exhausted",
                    ),
                )
                last_result = await self._apply_ai_healing(step, fallback, index)
                if last_result.success:
                    return last_result
            except Exception as e:
                logger.debug(f"AI healing failed: {e}")

        return StepResult(
            success=False,
            step_log=ExecutionStepLog(
                id=str(uuid.uuid4()),
                execution_id=self._execution_id,
                step_id=step.id,
                step_type=step.type.value,
                step_index=index,
                status=StepStatus.FAILED,
                started_at=time.time(),
                completed_at=time.time(),
                error_message="All retry attempts exhausted",
            ),
        )

    def _build_step_queue(self) -> list[str]:
        if self._flow.entry_step_ids:
            return list(self._flow.entry_step_ids)
        if not self._flow.steps:
            return []
        return [self._flow.steps[0].id]

    def _enqueue_next_steps(
        self,
        step: AutomationStep,
        result: StepResult | None,
        queue: list[str],
        queued_step_ids: set[str],
        executed_step_ids: set[str],
        step_map: dict[str, AutomationStep],
    ) -> None:
        outcome = result.control_flow_output if result is not None else None

        if self._adaptive and result is not None:
            loop_info = self._adaptive.check_loop(step.id, self._variables)
            if loop_info.detected:
                logger.info(
                    f"Loop detected at step {step.id}: {loop_info.iterations} iterations"
                )
                asyncio.ensure_future(self._emit_feedback(
                    self._create_feedback(
                        self._current_step_index,
                        StepStatus.RUNNING,
                        f"循环检测: 已执行 {loop_info.iterations} 次",
                        step_id=step.id,
                        step_type=step.type.value,
                    )
                ))

            adaptive_next = self._adaptive.resolve_next_steps(
                step.id, self._variables, default_next=None
            )
            if adaptive_next:
                next_step_ids = adaptive_next

        next_step_ids = self._flow.get_next_step_ids(step.id, outcome=outcome)
        if not next_step_ids and outcome is not None:
            next_step_ids = self._flow.get_next_step_ids(step.id)

        for next_step_id in next_step_ids:
            if next_step_id not in step_map:
                continue
            if next_step_id in executed_step_ids or next_step_id in queued_step_ids:
                continue
            queue.append(next_step_id)
            queued_step_ids.add(next_step_id)

    async def _emit_feedback(self, feedback: ExecutionFeedback) -> None:
        for callback in self._feedback_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(feedback)
                else:
                    callback(feedback)
            except Exception as e:
                logger.error(f"Feedback callback error: {e}")

    def _create_feedback(
        self,
        step_index: int,
        step_status: StepStatus,
        description: str,
        step_id: str | None = None,
        step_type: str | None = None,
        visual_comparison: dict | None = None,
        error: str | None = None,
        ai_check: dict | None = None,
        ai_assist: dict | None = None,
    ) -> ExecutionFeedback:
        return ExecutionFeedback(
            execution_id=self._execution_id,
            current_step=step_index + 1,
            total_steps=len(self._flow.steps),
            step_status=step_status,
            step_description=description,
            step_id=step_id,
            step_type=step_type,
            visual_comparison=visual_comparison,
            elapsed_ms=int((time.time() - (self._started_at or time.time())) * 1000),
            error=error,
            ai_check=ai_check,
            ai_assist=ai_assist,
        )

    def _build_record(self) -> ExecutionRecord:
        return ExecutionRecord(
            id=self._execution_id,
            automation_id=self._flow.id,
            status=self._status,
            started_at=self._started_at,
            completed_at=self._completed_at,
            total_steps=len(self._flow.steps),
            completed_steps=self._completed_steps,
            failed_steps=self._failed_steps,
            error_summary=self._build_error_summary(),
            variables=self._variables,
            step_logs=self._step_logs,
            ai_summary=self._ai_summary,
            ai_step_insights=self._advisor.step_insights if self._advisor else None,
        )

    def _augment_ai_summary(self, summary):
        if summary is None:
            return None

        warnings = list(summary.warnings or [])
        optimization_items = list(summary.optimization_items or [])
        if self._ai_healing_applied > 0:
            warnings.insert(0, f"执行期间 AI 已成功自愈 {self._ai_healing_applied} 次，流程对环境变化较敏感。")
            optimization_items.insert(
                0,
                "为频繁触发 AI 自愈的节点补充 window_exists / page_stable / postcondition 等前后置检查。",
            )
        if self._ai_healing_failures > 0:
            warnings.append(f"仍有 {self._ai_healing_failures} 次 AI 自愈未恢复成功，建议重新录制相关步骤。")
        return summary.model_copy(
            update={
                "warnings": warnings[:5],
                "optimization_items": optimization_items[:5],
            }
        )

    @staticmethod
    def _build_healing_assist_payload(
        action,
        error_msg: str,
        *,
        level: str,
        title: str,
        summary: str,
        suggestions: list[str] | None = None,
    ) -> dict:
        merged_suggestions = list(suggestions or [])
        if not merged_suggestions:
            merged_suggestions = [f"执行动作: {action.action_type}"]
        return {
            "phase": "self_healing",
            "level": level,
            "title": title,
            "summary": summary,
            "warnings": [error_msg] if error_msg else [],
            "suggestions": merged_suggestions,
            "provider": "recovery",
            "local_ai_used": False,
        }

    async def _complete_healing_success(
        self,
        step: AutomationStep,
        result: StepResult,
        index: int,
        action,
        healing_error: Exception,
        duration_ms: float,
    ) -> StepResult:
        self._ai_healing_applied += 1
        self._recovery.record_outcome(step.id, healing_error, action, True, duration_ms)
        await self._emit_feedback(
            self._create_feedback(
                index,
                StepStatus.SUCCESS,
                f"AI 自愈成功: {action.description}",
                step_id=step.id,
                step_type=step.type.value,
                ai_assist=self._build_healing_assist_payload(
                    action,
                    result.step_log.error_message or "",
                    level="success",
                    title="AI 自愈成功",
                    summary=f"{action.description}，已恢复当前步骤。",
                    suggestions=["已自动恢复并继续执行。"],
                ),
            )
        )
        return result

    async def _complete_healing_failure(
        self,
        step: AutomationStep,
        failed_result: StepResult,
        index: int,
        action,
        healing_error: Exception,
        duration_ms: float,
        error_msg: str,
        summary: str,
    ) -> StepResult:
        self._ai_healing_failures += 1
        self._recovery.record_outcome(step.id, healing_error, action, False, duration_ms)
        await self._emit_feedback(
            self._create_feedback(
                index,
                StepStatus.FAILED,
                summary,
                step_id=step.id,
                step_type=step.type.value,
                error=error_msg,
                ai_assist=self._build_healing_assist_payload(
                    action,
                    error_msg,
                    level="error",
                    title="AI 自愈失败",
                    summary=summary,
                    suggestions=["建议补充稳定定位器、窗口前置条件或重新录制该步骤。"],
                ),
            )
        )
        return failed_result

    async def _emit_pre_step_feedback(self, step: AutomationStep, step_index: int) -> None:
        await self._emit_feedback(
            self._create_feedback(
                step_index,
                StepStatus.RUNNING,
                step.description or f"Step {step_index + 1}",
                step_id=step.id,
                step_type=step.type.value,
            )
        )

        if not self._advisor:
            return
        advice = await self._advisor.advise_before_step(step, step_index, len(self._flow.steps))
        if advice is None:
            return
        await self._emit_feedback(
            self._create_feedback(
                step_index,
                StepStatus.RUNNING,
                step.description or f"Step {step_index + 1}",
                step_id=step.id,
                step_type=step.type.value,
                ai_check=advice.model_dump(),
            )
        )

    async def _build_step_advice(self, step: AutomationStep, result: StepResult, index: int):
        if not self._advisor:
            return None, None

        post_advice = await self._advisor.advise_after_step(
            step=step,
            step_index=index,
            total_steps=len(self._flow.steps),
            success=result.success,
            error=result.step_log.error_message,
            verification_result=result.verification_result,
            visual_comparison=result.visual_comparison,
            screenshot_before=result.step_log.screenshot_before,
            screenshot_after=result.step_log.screenshot_after,
        )
        assist_advice = None
        if not result.success:
            assist_advice = await self._advisor.advise_assist(
                step=step,
                step_index=index,
                total_steps=len(self._flow.steps),
                error=result.step_log.error_message,
                verification_result=result.verification_result,
                screenshot_before=result.step_log.screenshot_before,
                screenshot_after=result.step_log.screenshot_after,
            )
        return post_advice, assist_advice

    def _build_error_summary(self) -> str | None:
        errors = [log.error_message for log in self._step_logs if log.error_message]
        if not errors:
            return None
        if len(errors) == 1:
            return errors[0]
        return f"{len(errors)} errors: " + "; ".join(errors[:3])

    def _build_default_variables(self) -> dict:
        variables = {}
        for var in self._flow.variables:
            if var.default_value is not None:
                variables[var.name] = var.default_value
        return variables

    async def _apply_ai_healing(
        self, step: AutomationStep, failed_result: StepResult, index: int
    ) -> StepResult:
        if not self._recovery:
            return failed_result

        error_msg = failed_result.step_log.error_message or "Unknown error"
        context = {
            "step_id": step.id,
            "step_type": step.type.value,
            "retry_count": self._retry_counts.get(step.id, 0),
            "strategy_used": step.target.strategy.value if step.target else "unknown",
            "failed_at": time.time(),
            "window_target": bool(
                step.type.value == "switch_window"
                or (step.target and (step.target.window_title or step.target.url))
            ),
            "can_scroll_into_view": step.type.value in {"click", "type", "scroll"},
        }

        class _HealingError(Exception):
            pass

        healing_error = _HealingError(error_msg)
        healing_start = time.time()

        try:
            self._ai_healing_attempts += 1
            action = self._recovery.analyze_and_heal(healing_error, context)
            duration_ms = (time.time() - healing_start) * 1000

            await self._emit_feedback(
                self._create_feedback(
                    index,
                    StepStatus.RUNNING,
                    f"AI 自愈尝试: {action.description} (预估成功率={action.estimated_success_rate:.0%})",
                    step_id=step.id,
                    step_type=step.type.value,
                    error=error_msg,
                    ai_assist=self._build_healing_assist_payload(
                        action,
                        error_msg,
                        level="warning",
                        title="AI 自愈尝试",
                        summary=action.description,
                        suggestions=[
                            f"执行动作: {action.action_type}",
                            f"预估成功率: {action.estimated_success_rate:.0%}",
                        ],
                    ),
                )
            )

            if action.action_type == "retry_with_backoff":
                delay = action.parameters.get("base_delay_ms", 500)
                max_attempts = action.parameters.get("max_attempts", 2)
                for i in range(max_attempts):
                    await asyncio.sleep(delay * (2 ** i) / 1000.0)
                    try:
                        result = await self._executor.execute_step(
                            step, self._variables, self._execution_id, index
                        )
                        result = self._decorate_step_result(result)
                        self._step_logs.append(result.step_log)
                        if result.success:
                            return await self._complete_healing_success(
                                step, result, index, action, healing_error, duration_ms
                            )
                    except Exception:
                        continue
                return await self._complete_healing_failure(
                    step,
                    failed_result,
                    index,
                    action,
                    healing_error,
                    duration_ms,
                    error_msg,
                    "AI 自愈未能恢复当前步骤，已保留原始失败结果。",
                )

            elif action.action_type == "fallback_selector":
                if step.target:
                    original = step.target.strategy.value
                    fallback_priority = action.parameters.get(
                        "strategy_priority", ["accessibility_id", "xpath", "image", "coordinate"]
                    )
                    try:
                        from src.models.automation import LocatorStrategy
                        for strat_name in fallback_priority:
                            if strat_name == original:
                                continue
                            try:
                                fallback_strat = LocatorStrategy(strat_name)
                                step.target.strategy = fallback_strat
                                result = await self._executor.execute_step(
                                    step, self._variables, self._execution_id, index
                                )
                                result = self._decorate_step_result(result)
                                self._step_logs.append(result.step_log)
                                if result.success:
                                    return await self._complete_healing_success(
                                        step, result, index, action, healing_error, duration_ms
                                    )
                            except (ValueError, Exception):
                                continue
                    except ImportError:
                        pass
                return await self._complete_healing_failure(
                    step,
                    failed_result,
                    index,
                    action,
                    healing_error,
                    duration_ms,
                    error_msg,
                    "AI 已尝试切换备用定位策略，但仍未恢复该步骤。",
                )

            elif action.action_type in ("increase_timeout", "refresh_and_retry"):
                multi = action.parameters.get("timeout_multiplier", 2.0)
                step.delay = int((step.delay or 1000) * multi)
                await asyncio.sleep(1.0)
                try:
                    result = await self._executor.execute_step(
                        step, self._variables, self._execution_id, index
                    )
                    result = self._decorate_step_result(result)
                    self._step_logs.append(result.step_log)
                    if result.success:
                        return await self._complete_healing_success(
                            step, result, index, action, healing_error, duration_ms
                        )
                except Exception:
                    pass
                return await self._complete_healing_failure(
                    step,
                    failed_result,
                    index,
                    action,
                    healing_error,
                    duration_ms,
                    error_msg,
                    "AI 已调整等待策略，但当前步骤仍未恢复。",
                )

            elif action.action_type == "wait_for_window":
                recovered = await self._executor.recover_window_context(
                    step,
                    self._variables,
                    timeout_ms=action.parameters.get("timeout_ms", 4000),
                    poll_interval_ms=action.parameters.get("poll_interval_ms", 250),
                )
                if recovered:
                    result = await self._executor.execute_step(
                        step, self._variables, self._execution_id, index
                    )
                    result = self._decorate_step_result(result)
                    self._step_logs.append(result.step_log)
                    if result.success:
                        return await self._complete_healing_success(
                            step, result, index, action, healing_error, duration_ms
                        )
                return await self._complete_healing_failure(
                    step,
                    failed_result,
                    index,
                    action,
                    healing_error,
                    duration_ms,
                    error_msg,
                    "AI 等待目标窗口恢复后仍无法继续执行。",
                )

            elif action.action_type == "scroll_into_view":
                scrolled = await self._executor.scroll_target_into_view(
                    step,
                    self._variables,
                    direction=action.parameters.get("scroll_direction", "center"),
                    delta=action.parameters.get("delta", 500),
                )
                if scrolled:
                    result = await self._executor.execute_step(
                        step, self._variables, self._execution_id, index
                    )
                    result = self._decorate_step_result(result)
                    self._step_logs.append(result.step_log)
                    if result.success:
                        return await self._complete_healing_success(
                            step, result, index, action, healing_error, duration_ms
                        )
                return await self._complete_healing_failure(
                    step,
                    failed_result,
                    index,
                    action,
                    healing_error,
                    duration_ms,
                    error_msg,
                    "AI 已尝试滚动恢复目标，但当前步骤仍失败。",
                )

            elif action.action_type == "ask_user":
                await self._emit_feedback(
                    self._create_feedback(
                        index,
                        StepStatus.PAUSED,
                        f"AI 无法自动修复: {error_msg[:100]}，需人工介入",
                        step_id=step.id,
                        step_type=step.type.value,
                        error=error_msg,
                    )
                )
                await self.pause()
                self._ai_healing_failures += 1
                self._recovery.record_outcome(step.id, healing_error, action, False, duration_ms)
                return failed_result

            return await self._complete_healing_failure(
                step,
                failed_result,
                index,
                action,
                healing_error,
                duration_ms,
                error_msg,
                "AI 当前没有可执行的自愈策略，已保留原始失败结果。",
            )

        except Exception as e:
            logger.debug(f"AI healing execution failed: {e}")
            self._ai_healing_failures += 1
            return failed_result

    @property
    def ai_healing_stats(self) -> dict:
        return {
            "healing_applied": self._ai_healing_applied,
            "healing_attempts": self._ai_healing_attempts,
            "healing_failures": self._ai_healing_failures,
            "recovery_available": self._recovery is not None,
            "adaptive_available": self._adaptive is not None,
        }
