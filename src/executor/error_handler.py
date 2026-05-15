from __future__ import annotations

import logging
import random
import time
from collections import deque
from enum import StrEnum

from src.models.automation import AutomationStep, ErrorAction
from src.models.execution import VerificationLevel, VerificationResult

logger = logging.getLogger(__name__)


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    STATE_MISMATCH = "state_mismatch"
    ELEMENT_CHANGED = "element_changed"
    ENVIRONMENT = "environment"
    FATAL = "fatal"


ERROR_KEYWORDS: dict[ErrorCategory, list[str]] = {
    ErrorCategory.TRANSIENT: [
        "timeout", "timed out", "连接", "网络", "network", "暂时",
        "temporary", "retry", "busy", "resource", "rate limit",
    ],
    ErrorCategory.STATE_MISMATCH: [
        "弹窗", "dialog", "unexpected", "意外", "遮挡", "obscured",
        "不在可视区域", "not in viewport", "页面跳转", "redirect",
    ],
    ErrorCategory.ELEMENT_CHANGED: [
        "元素未找到", "not found", "locate", "stale", "detached",
        "验证失败", "verification failed", "属性不匹配", "mismatch",
        "selector", "选择器", "元素变化",
    ],
    ErrorCategory.ENVIRONMENT: [
        "分辨率", "resolution", "dpi", "缩放", "scale",
        "权限", "permission", "denied", "安全控制", "safety",
        "窗口未找到", "window not found", "窗口不存在", "window unavailable",
    ],
    ErrorCategory.FATAL: [
        "认证失败", "authentication", "login failed", "密码错误",
        "license", "授权", "crash", "崩溃", "out of memory",
    ],
}


class SmartBackoff:
    def __init__(
        self,
        base: float = 1.0,
        max_delay: float = 30.0,
    ):
        self._base = base
        self._max_delay = max_delay
        self._history: list[float] = []

    def next_delay(self, attempt: int, error_category: ErrorCategory | None = None) -> float:
        delay = min(self._base * (2 ** attempt), self._max_delay)

        delay = random.uniform(0, delay)

        if error_category == ErrorCategory.TRANSIENT:
            delay *= 0.5
        elif error_category == ErrorCategory.ELEMENT_CHANGED:
            delay = max(delay, 2.0)
        elif error_category == ErrorCategory.STATE_MISMATCH:
            delay = max(delay, 1.5)

        if self._history and self._recent_success_rate() < 0.3:
            delay *= 1.5

        delay = min(delay, self._max_delay)
        self._history.append(delay)
        if len(self._history) > 50:
            self._history = self._history[-50:]
        return delay

    def _recent_success_rate(self, window: int = 5) -> float:
        if len(self._history) < window:
            return 1.0
        recent = self._history[-window:]
        return sum(1 for d in recent if d < self._max_delay * 0.5) / window


class Checkpoint:
    def __init__(
        self,
        checkpoint_id: str,
        step_index: int,
        variables: dict | None = None,
        app_state: dict | None = None,
    ):
        self.checkpoint_id = checkpoint_id
        self.step_index = step_index
        self.variables = variables or {}
        self.app_state = app_state or {}
        self.timestamp = time.time()


class CheckpointManager:
    def __init__(self, max_checkpoints: int = 10):
        self._checkpoints: deque[Checkpoint] = deque(maxlen=max_checkpoints)

    def save(self, step_index: int, variables: dict | None = None, app_state: dict | None = None) -> str:
        checkpoint_id = f"cp_{step_index}_{int(time.time() * 1000) % 100000}"
        checkpoint = Checkpoint(
            checkpoint_id=checkpoint_id,
            step_index=step_index,
            variables=variables,
            app_state=app_state,
        )
        self._checkpoints.append(checkpoint)
        logger.debug(f"Checkpoint saved: {checkpoint_id} at step {step_index}")
        return checkpoint_id

    def get_latest(self) -> Checkpoint | None:
        return self._checkpoints[-1] if self._checkpoints else None

    def get_for_step(self, step_index: int) -> Checkpoint | None:
        for cp in reversed(self._checkpoints):
            if cp.step_index <= step_index:
                return cp
        return None

    def rollback_to(self, checkpoint_id: str) -> Checkpoint | None:
        for i, cp in enumerate(self._checkpoints):
            if cp.checkpoint_id == checkpoint_id:
                self._checkpoints = deque(list(self._checkpoints)[: i + 1], maxlen=self._checkpoints.maxlen)
                logger.info(f"Rolled back to checkpoint: {checkpoint_id} (step {cp.step_index})")
                return cp
        return None

    def clear(self) -> None:
        self._checkpoints.clear()


class ErrorDecision:
    def __init__(
        self,
        action: ErrorAction,
        delay_ms: int = 0,
        message: str = "",
        verification_result: VerificationResult | None = None,
        adjusted_coordinates: tuple[int, int] | None = None,
        error_category: ErrorCategory | None = None,
        checkpoint_id: str | None = None,
    ):
        self.action = action
        self.delay_ms = delay_ms
        self.message = message
        self.verification_result = verification_result
        self.adjusted_coordinates = adjusted_coordinates
        self.error_category = error_category
        self.checkpoint_id = checkpoint_id


class ExecutionContext:
    def __init__(
        self,
        execution_id: str,
        current_step_index: int,
        total_steps: int,
        retry_counts: dict[str, int] | None = None,
        failed_steps: int = 0,
        verification_result: VerificationResult | None = None,
        error_history: list[dict] | None = None,
        variables: dict | None = None,
    ):
        self.execution_id = execution_id
        self.current_step_index = current_step_index
        self.total_steps = total_steps
        self.retry_counts = retry_counts or {}
        self.failed_steps = failed_steps
        self.verification_result = verification_result
        self.error_history = error_history or []
        self.variables = variables or {}


RECOVERY_POLICIES: dict[ErrorCategory, dict] = {
    ErrorCategory.TRANSIENT: {
        "action": ErrorAction.RETRY,
        "max_attempts": 3,
        "backoff_type": "exponential_jitter",
    },
    ErrorCategory.STATE_MISMATCH: {
        "action": ErrorAction.RELOCATE,
        "max_attempts": 2,
        "backoff_type": "fixed",
        "fixed_delay": 2.0,
    },
    ErrorCategory.ELEMENT_CHANGED: {
        "action": ErrorAction.RELOCATE,
        "max_attempts": 3,
        "backoff_type": "linear",
    },
    ErrorCategory.ENVIRONMENT: {
        "action": ErrorAction.SKIP,
        "max_attempts": 1,
        "backoff_type": "none",
    },
    ErrorCategory.FATAL: {
        "action": ErrorAction.ABORT,
        "max_attempts": 0,
        "backoff_type": "none",
    },
}


class ErrorHandler:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay_ms: int = 1000,
        max_delay_ms: int = 30000,
        checkpoint_manager: CheckpointManager | None = None,
    ):
        self._max_retries = max_retries
        self._base_delay_ms = base_delay_ms
        self._max_delay_ms = max_delay_ms
        self._backoff = SmartBackoff(
            base=base_delay_ms / 1000.0,
            max_delay=max_delay_ms / 1000.0,
        )
        self._checkpoint_manager = checkpoint_manager or CheckpointManager()
        self._error_history: list[dict] = []

    @property
    def checkpoint_manager(self) -> CheckpointManager:
        return self._checkpoint_manager

    async def handle_error(self, step: AutomationStep, error: Exception, context: ExecutionContext) -> ErrorDecision:
        category = self._classify_error(error, context)
        attempt = context.retry_counts.get(step.id, 0) + 1

        self._record_error(step, error, category, attempt, context)

        action = step.on_error

        if self._is_missing_window_error(step, error) and action not in (ErrorAction.ABORT, ErrorAction.ASK_USER):
            if action == ErrorAction.SKIP:
                return ErrorDecision(
                    action=ErrorAction.SKIP,
                    message=f"Skipped missing window target: {error}",
                    error_category=ErrorCategory.ENVIRONMENT,
                )
            return ErrorDecision(
                action=ErrorAction.ABORT,
                message=f"Unsafe to continue without target window: {error}",
                error_category=ErrorCategory.ENVIRONMENT,
            )

        if category == ErrorCategory.FATAL:
            return ErrorDecision(
                action=ErrorAction.ABORT,
                message=f"Fatal error ({category.value}): {error}",
                error_category=category,
            )

        if action == ErrorAction.RELOCATE:
            return self._handle_relocate_v2(step, error, context, attempt, category)

        if action == ErrorAction.ADJUST_AND_RETRY:
            return self._handle_adjust_and_retry_v2(step, error, context, attempt, category)

        if action == ErrorAction.RETRY:
            if attempt <= step.retry_count:
                delay = self._backoff.next_delay(attempt, category)
                logger.info(
                    f"Retrying step {step.id} "
                    f"(attempt {attempt}/{step.retry_count}), "
                    f"delay={delay:.1f}s, category={category.value}"
                )
                context.retry_counts[step.id] = attempt
                return ErrorDecision(
                    action=ErrorAction.RETRY,
                    delay_ms=int(delay * 1000),
                    message=f"Retry attempt {attempt} ({category.value})",
                    error_category=category,
                )
            else:
                logger.warning(f"Step {step.id} exhausted retries ({step.retry_count}), falling back")
                return self._fallback_decision_v2(step, error, context, category)

        elif action == ErrorAction.SKIP:
            logger.info(f"Skipping step {step.id} due to error: {error}")
            return ErrorDecision(
                action=ErrorAction.SKIP,
                message=f"Skipped: {error}",
                error_category=category,
            )

        elif action == ErrorAction.ASK_USER:
            logger.info(f"Pausing execution for user decision on step {step.id}")
            return ErrorDecision(
                action=ErrorAction.ASK_USER,
                message=f"User decision required: {error}",
                error_category=category,
            )

        elif action == ErrorAction.FALLBACK:
            return self._handle_fallback_v2(step, error, context, category)

        else:
            logger.error(f"Aborting execution due to error in step {step.id}: {error}")
            return ErrorDecision(action=ErrorAction.ABORT, message=f"Aborted: {error}", error_category=category)

    def _classify_error(self, error: Exception, context: ExecutionContext) -> ErrorCategory:
        error_msg = str(error).lower()

        for category, keywords in ERROR_KEYWORDS.items():
            for keyword in keywords:
                if keyword.lower() in error_msg:
                    return category

        if context.verification_result:
            vr = context.verification_result
            if vr.coordinate_verification == VerificationLevel.WARNING:
                return ErrorCategory.ELEMENT_CHANGED
            if vr.attribute_verification == VerificationLevel.FAILED:
                return ErrorCategory.ELEMENT_CHANGED
            if vr.visual_verification == VerificationLevel.WARNING:
                return ErrorCategory.STATE_MISMATCH

        if context.error_history:
            recent = context.error_history[-3:]
            same_step = sum(1 for e in recent if e.get("step_id") == context.current_step_index)
            if same_step >= 3:
                return ErrorCategory.FATAL

        return ErrorCategory.TRANSIENT

    async def should_retry(self, step: AutomationStep, attempt: int, error: Exception) -> bool:
        if attempt >= step.retry_count:
            return False
        category = self._classify_error(error, ExecutionContext(
            execution_id="", current_step_index=0, total_steps=0,
        ))
        if category == ErrorCategory.FATAL:
            return False
        return category != ErrorCategory.ENVIRONMENT

    def get_retry_delay(self, attempt: int) -> float:
        return self._backoff.next_delay(attempt)

    def _handle_relocate_v2(
        self,
        step: AutomationStep,
        error: Exception,
        context: ExecutionContext,
        attempt: int,
        category: ErrorCategory,
    ) -> ErrorDecision:
        if attempt <= step.retry_count:
            delay = self._backoff.next_delay(attempt, category)
            logger.info(
                f"Relocating element for step {step.id} "
                f"(attempt {attempt}), delay={delay:.1f}s, "
                f"category={category.value}"
            )
            context.retry_counts[step.id] = attempt
            return ErrorDecision(
                action=ErrorAction.RELOCATE,
                delay_ms=int(delay * 1000),
                message=f"Relocating element, attempt {attempt} ({category.value})",
                verification_result=context.verification_result,
                error_category=category,
            )

        logger.warning(f"Step {step.id} relocate exhausted, falling back")
        return self._fallback_decision_v2(step, error, context, category)

    def _handle_adjust_and_retry_v2(
        self,
        step: AutomationStep,
        error: Exception,
        context: ExecutionContext,
        attempt: int,
        category: ErrorCategory,
    ) -> ErrorDecision:
        verification = context.verification_result

        if (
            verification
            and verification.coordinate_verification == VerificationLevel.WARNING
            and attempt <= step.retry_count
        ):
            logger.info(
                f"Adjusting coordinates for step {step.id} "
                f"based on verification result"
            )
            context.retry_counts[step.id] = attempt
            return ErrorDecision(
                action=ErrorAction.ADJUST_AND_RETRY,
                delay_ms=500,
                message=f"Adjusting coordinates, attempt {attempt} ({category.value})",
                verification_result=verification,
                error_category=category,
            )

        if (
            verification
            and verification.attribute_verification == VerificationLevel.WARNING
            and attempt <= step.retry_count
        ):
            logger.info(
                f"Retrying step {step.id} with relaxed attribute matching"
            )
            context.retry_counts[step.id] = attempt
            return ErrorDecision(
                action=ErrorAction.ADJUST_AND_RETRY,
                delay_ms=1000,
                message=f"Retrying with relaxed matching, attempt {attempt} ({category.value})",
                verification_result=verification,
                error_category=category,
            )

        if attempt <= step.retry_count:
            delay = self._backoff.next_delay(attempt, category)
            context.retry_counts[step.id] = attempt
            return ErrorDecision(
                action=ErrorAction.ADJUST_AND_RETRY,
                delay_ms=int(delay * 1000),
                message=f"Adjust and retry, attempt {attempt} ({category.value})",
                error_category=category,
            )

        return self._fallback_decision_v2(step, error, context, category)

    def _fallback_decision_v2(
        self,
        step: AutomationStep,
        error: Exception,
        context: ExecutionContext,
        category: ErrorCategory,
    ) -> ErrorDecision:
        if category == ErrorCategory.FATAL:
            return ErrorDecision(
                action=ErrorAction.ABORT,
                message="Fallback: fatal error, aborting",
                error_category=category,
            )

        if category == ErrorCategory.ELEMENT_CHANGED:
            checkpoint = self._checkpoint_manager.get_latest()
            if checkpoint and checkpoint.step_index < context.current_step_index:
                return ErrorDecision(
                    action=ErrorAction.RELOCATE,
                    delay_ms=2000,
                    message=(
                        f"Fallback: relocate after element changed "
                        f"(checkpoint at step {checkpoint.step_index})"
                    ),
                    error_category=category,
                    checkpoint_id=checkpoint.checkpoint_id,
                )

        if category == ErrorCategory.STATE_MISMATCH:
            return ErrorDecision(
                action=ErrorAction.RELOCATE,
                delay_ms=1500,
                message="Fallback: relocate after state mismatch",
                error_category=category,
            )

        if context.failed_steps < context.total_steps * 0.3:
            return ErrorDecision(
                action=ErrorAction.SKIP,
                message="Fallback: skip step after retries exhausted",
                error_category=category,
            )
        return ErrorDecision(
            action=ErrorAction.ABORT,
            message="Fallback: too many failures, aborting",
            error_category=category,
        )

    def _handle_fallback_v2(
        self,
        step: AutomationStep,
        error: Exception,
        context: ExecutionContext,
        category: ErrorCategory,
    ) -> ErrorDecision:
        error_msg = str(error).lower()
        verification = context.verification_result

        if "安全控制" in error_msg:
            return ErrorDecision(
                action=ErrorAction.ABORT,
                message="Safety control triggered, aborting execution",
                error_category=ErrorCategory.FATAL,
            )

        if verification and verification.overall_level == VerificationLevel.WARNING:
            return ErrorDecision(
                action=ErrorAction.ADJUST_AND_RETRY,
                delay_ms=1500,
                message="Fallback: adjust and retry based on verification warnings",
                verification_result=verification,
                error_category=category,
            )

        is_element_changed = (
            category == ErrorCategory.ELEMENT_CHANGED
            or "not found" in error_msg
            or "locate" in error_msg
        )
        if is_element_changed:
            return ErrorDecision(
                action=ErrorAction.RELOCATE,
                delay_ms=2000,
                message="Fallback: relocate element after not found",
                error_category=category,
            )

        if category == ErrorCategory.TRANSIENT or "timeout" in error_msg:
            return ErrorDecision(
                action=ErrorAction.RETRY,
                delay_ms=3000,
                message="Fallback: retry with longer delay for timeout",
                error_category=category,
            )

        if "验证失败" in error_msg:
            return ErrorDecision(
                action=ErrorAction.RELOCATE,
                delay_ms=2000,
                message="Fallback: relocate after verification failure",
                error_category=ErrorCategory.ELEMENT_CHANGED,
            )

        return ErrorDecision(
            action=ErrorAction.SKIP,
            message="Fallback: skip unhandled error type",
            error_category=category,
        )

    def _record_error(
        self,
        step: AutomationStep,
        error: Exception,
        category: ErrorCategory,
        attempt: int,
        context: ExecutionContext,
    ) -> None:
        record = {
            "timestamp": time.time(),
            "step_id": step.id,
            "step_type": step.type.value,
            "error_type": type(error).__name__,
            "error_message": str(error)[:200],
            "category": category.value,
            "attempt": attempt,
            "execution_id": context.execution_id,
        }
        self._error_history.append(record)
        context.error_history.append(record)
        if len(self._error_history) > 200:
            self._error_history = self._error_history[-200:]

    def _is_missing_window_error(self, step: AutomationStep, error: Exception) -> bool:
        error_msg = str(error).lower()
        has_window_target = bool(
            step.type.value == "switch_window"
            or (step.target and (step.target.window_title or step.target.url))
        )
        if not has_window_target:
            return False

        return (
            "窗口未找到" in str(error)
            or "window not found" in error_msg
            or "窗口不存在" in str(error)
            or "window unavailable" in error_msg
        )

    def get_error_stats(self) -> dict:
        if not self._error_history:
            return {"total": 0, "categories": {}, "most_common": None}
        total = len(self._error_history)
        categories: dict[str, int] = {}
        for r in self._error_history:
            cat = r["category"]
            categories[cat] = categories.get(cat, 0) + 1
        most_common = max(categories, key=categories.get) if categories else None
        return {
            "total": total,
            "categories": categories,
            "most_common": most_common,
        }
