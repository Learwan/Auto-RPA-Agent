import pytest

from src.executor.error_handler import ErrorHandler, ExecutionContext
from src.models.automation import AutomationStep, ErrorAction, StepTarget, StepType
from src.models.execution import VerificationLevel, VerificationResult


def _make_step(on_error: ErrorAction = ErrorAction.RETRY, retry_count: int = 3) -> AutomationStep:
    return AutomationStep(
        id="step-1",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy="accessibility_id", accessibility_id="btn"),
        on_error=on_error,
        retry_count=retry_count,
    )


def _make_window_step(on_error: ErrorAction = ErrorAction.RETRY, retry_count: int = 3) -> AutomationStep:
    return AutomationStep(
        id="step-window",
        type=StepType.SWITCH_WINDOW,
        action={"window_title": "企业微信"},
        target=StepTarget(strategy="text_match", title="企业微信", window_title="企业微信"),
        on_error=on_error,
        retry_count=retry_count,
    )


def _make_context(
    execution_id: str = "exec-1",
    step_index: int = 0,
    total_steps: int = 5,
    retry_counts: dict | None = None,
    failed_steps: int = 0,
    verification_result: VerificationResult | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        execution_id=execution_id,
        current_step_index=step_index,
        total_steps=total_steps,
        retry_counts=retry_counts or {},
        failed_steps=failed_steps,
        verification_result=verification_result,
    )


class TestErrorHandlerRetry:
    @pytest.mark.asyncio
    async def test_retry_within_limit(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.RETRY, retry_count=3)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("test error"), context)
        assert result.action == ErrorAction.RETRY

    @pytest.mark.asyncio
    async def test_retry_exhausted_falls_back(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.RETRY, retry_count=1)
        context = _make_context(retry_counts={"step-1": 1})

        result = await handler.handle_error(step, RuntimeError("test error"), context)
        assert result.action in (ErrorAction.SKIP, ErrorAction.ABORT)

    @pytest.mark.asyncio
    async def test_missing_window_aborts_by_default_in_sync_execution_path(self):
        handler = ErrorHandler()
        step = _make_window_step(on_error=ErrorAction.RETRY, retry_count=3)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("执行前验证失败: 窗口未找到 (企业微信)"), context)
        assert result.action == ErrorAction.ABORT

    @pytest.mark.asyncio
    async def test_missing_window_only_skips_when_step_explicitly_requests_skip(self):
        handler = ErrorHandler()
        step = _make_window_step(on_error=ErrorAction.SKIP, retry_count=3)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("执行前验证失败: 窗口未找到 (企业微信)"), context)
        assert result.action == ErrorAction.SKIP


class TestErrorHandlerSkip:
    @pytest.mark.asyncio
    async def test_skip_action(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.SKIP)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("test error"), context)
        assert result.action == ErrorAction.SKIP


class TestErrorHandlerAbort:
    @pytest.mark.asyncio
    async def test_abort_action(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.ABORT)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("test error"), context)
        assert result.action == ErrorAction.ABORT


class TestErrorHandlerRelocate:
    @pytest.mark.asyncio
    async def test_relocate_on_element_not_found(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.RELOCATE, retry_count=3)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("元素未找到"), context)
        assert result.action == ErrorAction.RELOCATE

    @pytest.mark.asyncio
    async def test_relocate_exhausted_falls_back(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.RELOCATE, retry_count=1)
        context = _make_context(retry_counts={"step-1": 1})

        result = await handler.handle_error(step, RuntimeError("元素未找到"), context)
        assert result.action in (ErrorAction.SKIP, ErrorAction.ABORT)


class TestErrorHandlerAdjustAndRetry:
    @pytest.mark.asyncio
    async def test_adjust_on_coordinate_warning(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.ADJUST_AND_RETRY, retry_count=3)
        verification = VerificationResult(
            coordinate_verification=VerificationLevel.WARNING,
            coordinate_details="Offset detected",
        )
        context = _make_context(verification_result=verification)

        result = await handler.handle_error(step, RuntimeError("verification warning"), context)
        assert result.action == ErrorAction.ADJUST_AND_RETRY

    @pytest.mark.asyncio
    async def test_adjust_on_attribute_warning(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.ADJUST_AND_RETRY, retry_count=3)
        verification = VerificationResult(
            attribute_verification=VerificationLevel.WARNING,
            attribute_details="Auxiliary attributes mismatch",
        )
        context = _make_context(verification_result=verification)

        result = await handler.handle_error(step, RuntimeError("attribute warning"), context)
        assert result.action == ErrorAction.ADJUST_AND_RETRY


class TestErrorHandlerFallback:
    @pytest.mark.asyncio
    async def test_fallback_safety_control_aborts(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.FALLBACK)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("安全控制：检测到随机点击模式"), context)
        assert result.action == ErrorAction.ABORT

    @pytest.mark.asyncio
    async def test_fallback_not_found_relocates(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.FALLBACK)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("Element not found"), context)
        assert result.action == ErrorAction.RELOCATE

    @pytest.mark.asyncio
    async def test_fallback_verification_failure_relocates(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.FALLBACK)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("验证失败: element mismatch"), context)
        assert result.action == ErrorAction.RELOCATE

    @pytest.mark.asyncio
    async def test_fallback_timeout_retries(self):
        handler = ErrorHandler()
        step = _make_step(on_error=ErrorAction.FALLBACK)
        context = _make_context()

        result = await handler.handle_error(step, RuntimeError("timeout waiting for element"), context)
        assert result.action == ErrorAction.RETRY


class TestErrorHandlerRetryDelay:
    def test_backoff_increases_on_average(self):
        handler = ErrorHandler(base_delay_ms=1000, max_delay_ms=30000)

        delays = [handler.get_retry_delay(i) for i in range(1, 4)]
        assert all(d >= 0 for d in delays)
        assert all(d <= 30.0 for d in delays)

    def test_delay_capped_at_max(self):
        handler = ErrorHandler(base_delay_ms=1000, max_delay_ms=5000)

        delay = handler.get_retry_delay(10)
        assert delay <= 5.0


class TestErrorHandlerShouldRetry:
    @pytest.mark.asyncio
    async def test_should_retry_runtime_not_found(self):
        handler = ErrorHandler()
        step = _make_step()
        result = await handler.should_retry(step, 1, RuntimeError("Element not found"))
        assert result is True

    @pytest.mark.asyncio
    async def test_should_not_retry_permission_error(self):
        handler = ErrorHandler()
        step = _make_step()
        result = await handler.should_retry(step, 1, PermissionError("Access denied"))
        assert result is False

    @pytest.mark.asyncio
    async def test_should_not_retry_safety_control(self):
        handler = ErrorHandler()
        step = _make_step()
        result = await handler.should_retry(step, 1, RuntimeError("安全控制：检测到随机点击模式"))
        assert result is False

    @pytest.mark.asyncio
    async def test_should_not_retry_past_max(self):
        handler = ErrorHandler()
        step = _make_step(retry_count=3)
        result = await handler.should_retry(step, 3, RuntimeError("error"))
        assert result is False
