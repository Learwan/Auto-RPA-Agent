import asyncio
import contextlib
import logging
import math
import os
import re
import sys
import time
import uuid

from src.executor.element_locator import ElementLocator, LocatedElement
from src.executor.screenshot_comparator import ScreenshotComparator
from src.executor.step_verifier import StepVerifier
from src.models.automation import AutomationStep, LocateStrategy, StepCondition, StepTarget, StepType
from src.models.execution import ExecutionStepLog, StepStatus, VerificationLevel
from src.platform.base import BasePlatformAdapter

logger = logging.getLogger(__name__)


class _HeadlessPyAutoGUI:
    FAILSAFE = False

    def __init__(self, import_error: Exception):
        self._import_error = import_error

    @staticmethod
    def size() -> tuple[int, int]:
        # Keep safety checks functional in headless environments.
        return (1920, 1080)

    def __getattr__(self, name: str):
        raise RuntimeError(
            "pyautogui is unavailable in this runtime (likely headless / missing DISPLAY). "
            f"Cannot execute desktop action '{name}'. Original error: {self._import_error!r}"
        )


def _load_pyautogui():
    try:
        import pyautogui as _pyautogui
    except Exception as exc:  # pragma: no cover - import-path dependent
        logger.warning("pyautogui import failed; desktop actions will be unavailable: %r", exc)
        fallback = _HeadlessPyAutoGUI(exc)
        sys.modules.setdefault("pyautogui", fallback)
        return fallback

    _pyautogui.FAILSAFE = False
    return _pyautogui


pyautogui = _load_pyautogui()


def _coord_fallback_allowed() -> bool:
    """Allow naked (x, y) fallback only when explicitly opted in.

    The user-facing contract is "no fragile nodes such as hard coordinates".
    By default we therefore refuse to fall back to a literal pixel coordinate
    when no element could be located.  The escape hatch is the env var
    ``AUTO_AGENT_ALLOW_COORDINATE_FALLBACK=1`` for niche scenarios where
    coordinate-only execution is the only option (e.g. dragging into an
    immutable canvas region).
    """
    return os.environ.get("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

CONSECUTIVE_FAILURE_LIMIT = 3
COORDINATE_SAFETY_MARGIN_PX = 5
RANDOM_CLICK_DISTANCE_PX = 200
RANDOM_CLICK_COUNT_THRESHOLD = 3
WINDOW_MATCH_THRESHOLD = 0.72
WINDOW_SEGMENT_SPLIT_RE = re.compile(r"\s*[|·•—–-]+\s*")
WINDOW_TOKEN_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")


class StepResult:
    def __init__(
        self,
        success: bool,
        step_log: ExecutionStepLog,
        control_flow_output: str | None = None,
        screenshot_before: bytes | None = None,
        screenshot_after: bytes | None = None,
        visual_comparison: dict | None = None,
        verification_result: dict | None = None,
        located_element: LocatedElement | None = None,
    ):
        self.success = success
        self.step_log = step_log
        self.control_flow_output = control_flow_output
        self.screenshot_before = screenshot_before
        self.screenshot_after = screenshot_after
        self.visual_comparison = visual_comparison
        self.verification_result = verification_result
        self.located_element = located_element


class SafetyController:
    def __init__(self, platform_adapter: "BasePlatformAdapter | None" = None):
        self._consecutive_failures = 0
        self._recent_click_positions: list[tuple[int, int, float]] = []
        self._recent_keyboard_actions: list[tuple[str, float]] = []
        self._paused_for_safety = False
        self._adapter = platform_adapter

    def record_failure(self) -> bool:
        self._consecutive_failures += 1
        if self._consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            self._paused_for_safety = True
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._paused_for_safety = False

    def is_paused(self) -> bool:
        return self._paused_for_safety

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._paused_for_safety = False
        self._recent_click_positions.clear()
        self._recent_keyboard_actions.clear()

    async def _get_screen_size(self) -> tuple[int, int]:
        if self._adapter:
            try:
                return await self._adapter.get_logical_screen_size()
            except Exception:
                pass
        return pyautogui.size()

    async def check_coordinate_safety(self, x: int, y: int) -> bool:
        try:
            screen_w, screen_h = await self._get_screen_size()
            if x < COORDINATE_SAFETY_MARGIN_PX or y < COORDINATE_SAFETY_MARGIN_PX:
                return False
            return not (x > screen_w - COORDINATE_SAFETY_MARGIN_PX or y > screen_h - COORDINATE_SAFETY_MARGIN_PX)
        except Exception:
            return True

    def check_random_click_pattern(self, x: int, y: int) -> bool:
        now = time.time()
        self._recent_click_positions.append((x, y, now))
        self._recent_click_positions = [(px, py, t) for px, py, t in self._recent_click_positions if now - t < 5.0]

        if len(self._recent_click_positions) < RANDOM_CLICK_COUNT_THRESHOLD:
            return True

        recent = self._recent_click_positions[-RANDOM_CLICK_COUNT_THRESHOLD:]
        for i in range(len(recent) - 1):
            x1, y1, _ = recent[i]
            x2, y2, _ = recent[i + 1]
            dist = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            if dist > RANDOM_CLICK_DISTANCE_PX:
                return False

        return True

    def check_random_keyboard_pattern(self, text: str) -> bool:
        now = time.time()
        self._recent_keyboard_actions.append((text, now))
        self._recent_keyboard_actions = [(t, ts) for t, ts in self._recent_keyboard_actions if now - ts < 5.0]

        if len(self._recent_keyboard_actions) < 3:
            return True

        recent_texts = [t for t, _ in self._recent_keyboard_actions[-3:]]
        return not all(len(t) > 20 and not any(c.isalpha() for c in t) for t in recent_texts)


class StepExecutor:
    def __init__(
        self, locator: ElementLocator, platform_adapter: BasePlatformAdapter, enable_visual_verify: bool = True
    ):
        self._locator = locator
        self._adapter = platform_adapter
        self._enable_visual_verify = enable_visual_verify
        self._comparator = ScreenshotComparator() if enable_visual_verify else None
        self._verifier = StepVerifier(locator, platform_adapter)
        self._safety = SafetyController(platform_adapter=platform_adapter)

    @property
    def safety_controller(self) -> SafetyController:
        return self._safety

    async def execute_step(
        self, step: AutomationStep, variables: dict, execution_id: str, step_index: int
    ) -> StepResult:
        step_log = ExecutionStepLog(
            id=str(uuid.uuid4()),
            execution_id=execution_id,
            step_id=step.id,
            step_type=step.type.value,
            step_index=step_index,
            status=StepStatus.RUNNING,
            started_at=time.time(),
        )

        screenshot_before = await self._take_screenshot()
        visual_comparison = None
        verification_dict = None
        located_element = None
        control_flow_output = None
        resolved_action = self._resolve_variables(step.action, variables)

        try:
            if self._safety.is_paused():
                raise RuntimeError(f"安全控制：连续 {CONSECUTIVE_FAILURE_LIMIT} 次失败，执行已暂停，等待用户确认")

            if (
                step.type not in (StepType.WAIT, StepType.CONDITION, StepType.LOOP)
                and step.locator_requires_confirmation()
            ):
                confirmation_reason = (step.metadata or {}).get(
                    "locator_confirmation_reason"
                ) or "录制阶段目标定位仍不确定"
                # Refuse to auto-execute an unconfirmed step in the closed
                # loop unless the operator explicitly opts in.  This is the
                # runtime mirror of ``FlowClosureAssessor`` and prevents
                # silently degrading to a fragile path.
                if not _coord_fallback_allowed():
                    raise RuntimeError(
                        f"拒绝执行：步骤定位尚未确认（原因：{confirmation_reason}）。"
                        "请在流程编辑器中确认目标元素后再执行，或设置环境变量 "
                        "AUTO_AGENT_ALLOW_COORDINATE_FALLBACK=1 临时放行。"
                    )
                logger.warning(
                    "Proceeding with unconfirmed locator for step %s (%s): %s",
                    step.id,
                    step.type.value,
                    confirmation_reason,
                )

            if (
                step.verification_enabled
                and step.target
                and step.type
                not in (
                    StepType.WAIT,
                    StepType.CONDITION,
                    StepType.LOOP,
                )
            ):
                if self._is_web_target(step.target) and self._adapter.get_platform_name() == "web":
                    pass
                elif step.type == StepType.SWITCH_WINDOW or self._is_window_target(step.target, resolved_action):
                    if await self._find_matching_window(step.target, resolved_action) is None:
                        recovered = await self.recover_window_context(
                            step,
                            variables,
                            timeout_ms=1500,
                            poll_interval_ms=200,
                        )
                        if recovered:
                            pass
                        else:
                            window_desc = self._describe_window_target(step.target, resolved_action)
                            raise RuntimeError(
                                f"执行前验证失败: 窗口未找到 ({window_desc})"
                            )
                else:
                    located_element = await self._locator.locate(step.target)
                    if located_element is not None:
                        verification = await self._verifier.verify(
                            step.target,
                            located=located_element,
                            strict=step.verification_strict,
                        )
                        verification_dict = verification.model_dump()

                        if not verification.can_proceed:
                            raise RuntimeError(
                                f"执行前验证失败: {verification.recovery_suggestion or '元素验证未通过'}"
                            )

                        if verification.overall_level == VerificationLevel.WARNING:
                            logger.warning(f"Step {step.id} verification warning: {verification.recovery_suggestion}")
                    elif step.target.strategy != LocateStrategy.POSITION:
                        raise RuntimeError(f"执行前验证失败: 元素未找到 (策略={step.target.strategy.value})")

            await self._ensure_window_context(step, resolved_action)
            await self._ensure_preconditions(step, resolved_action, variables)

            if step.type == StepType.CONDITION:
                condition_matched = await self._evaluate_condition(step, resolved_action, variables, step.condition)
                control_flow_output = "true" if condition_matched else "false"
                if not condition_matched and not step.branches:
                    raise RuntimeError(f"Condition not met for step {step.id}")
            else:
                handler = self._get_handler(step.type)
                await handler(step, resolved_action)

            await self._ensure_postconditions(step, resolved_action, variables)

            step_log.status = StepStatus.SUCCESS
            self._safety.record_success()
        except Exception as e:
            step_log.status = StepStatus.FAILED
            step_log.error_message = str(e)
            logger.error(f"Step {step.id} execution failed: {e}")
            if self._should_count_as_safety_failure(step, resolved_action, str(e)):
                should_pause = self._safety.record_failure()
                if should_pause:
                    logger.warning(f"Safety pause triggered after {CONSECUTIVE_FAILURE_LIMIT} consecutive failures")
            else:
                logger.info(f"Step {step.id}: missing-window failure ignored by safety controller")
        finally:
            step_log.completed_at = time.time()
            step_log.verification_result = verification_dict
            screenshot_after = await self._take_screenshot()

            if self._comparator and screenshot_before and screenshot_after and step_log.status == StepStatus.SUCCESS:
                try:
                    screen_w, screen_h = await self._adapter.get_screen_size()
                    result = self._comparator.compare(screenshot_before, screenshot_after, screen_w, screen_h)
                    visual_comparison = result.to_dict()
                    if not result.is_significant and step.type in (
                        StepType.CLICK,
                        StepType.TYPE,
                        StepType.HOTKEY,
                        StepType.NAVIGATE,
                        StepType.UPLOAD_FILE,
                        StepType.DOWNLOAD_FILE,
                    ):
                        logger.info(
                            f"Step {step.id}: visual change not detected "
                            f"(similarity={result.similarity:.3f}, change_ratio={result.change_ratio:.4f})"
                        )
                except Exception as e:
                    logger.debug(f"Visual comparison failed: {e}")

        return StepResult(
            success=step_log.status == StepStatus.SUCCESS,
            step_log=step_log,
            control_flow_output=control_flow_output,
            screenshot_before=screenshot_before,
            screenshot_after=screenshot_after,
            visual_comparison=visual_comparison,
            verification_result=verification_dict,
            located_element=located_element,
        )

    async def dry_run_step(
        self, step: AutomationStep, variables: dict, execution_id: str, step_index: int
    ) -> StepResult:
        step_log = ExecutionStepLog(
            id=str(uuid.uuid4()),
            execution_id=execution_id,
            step_id=step.id,
            step_type=step.type.value,
            step_index=step_index,
            status=StepStatus.RUNNING,
            started_at=time.time(),
        )

        verification_dict = None
        control_flow_output = None

        try:
            resolved_action = self._resolve_variables(step.action, variables)
            await self._ensure_preconditions(step, resolved_action, variables)

            if step.type == StepType.CONDITION:
                condition_matched = await self._evaluate_condition(step, resolved_action, variables, step.condition)
                control_flow_output = "true" if condition_matched else "false"
                if not condition_matched and not step.branches:
                    step_log.status = StepStatus.FAILED
                    step_log.error_message = f"Condition not met for step {step.id}"
                else:
                    step_log.status = StepStatus.SUCCESS
            elif step.target and step.target.strategy != LocateStrategy.POSITION:
                located = await self._locator.locate(step.target)
                if not located:
                    step_log.status = StepStatus.FAILED
                    step_log.error_message = f"Target element not found via {step.target.strategy.value}"
                else:
                    if step.verification_enabled:
                        verification = await self._verifier.verify(
                            step.target, located=located, strict=step.verification_strict
                        )
                        verification_dict = verification.model_dump()
                        if not verification.can_proceed:
                            step_log.status = StepStatus.FAILED
                            step_log.error_message = f"Verification failed: {verification.recovery_suggestion}"
                        else:
                            step_log.status = StepStatus.SUCCESS
                    else:
                        step_log.status = StepStatus.SUCCESS
            else:
                step_log.status = StepStatus.SUCCESS
        except Exception as e:
            step_log.status = StepStatus.FAILED
            step_log.error_message = str(e)
        finally:
            step_log.completed_at = time.time()
            step_log.verification_result = verification_dict

        return StepResult(
            success=step_log.status == StepStatus.SUCCESS,
            step_log=step_log,
            control_flow_output=control_flow_output,
            verification_result=verification_dict,
        )

    def _get_handler(self, step_type: StepType):
        handlers = {
            StepType.CLICK: self._execute_click,
            StepType.TYPE: self._execute_type,
            StepType.HOTKEY: self._execute_hotkey,
            StepType.SCROLL: self._execute_scroll,
            StepType.DRAG: self._execute_drag,
            StepType.SWITCH_WINDOW: self._execute_switch_window,
            StepType.FILE_OP: self._execute_file_op,
            StepType.NAVIGATE: self._execute_navigate,
            StepType.UPLOAD_FILE: self._execute_upload_file,
            StepType.DOWNLOAD_FILE: self._execute_download_file,
            StepType.WAIT: self._execute_wait,
            StepType.CONDITION: self._execute_condition,
            StepType.LOOP: self._execute_loop,
        }
        return handlers.get(step_type, self._execute_unknown)

    async def _execute_click(self, step: AutomationStep, action: dict) -> None:
        click_target = getattr(self._adapter, "click_target", None)
        if self._is_web_target(step.target) and callable(click_target):
            await click_target(step.target, action)
            return

        fallback_position = None
        located = await self._locator.locate(step.target)
        if not located or not located.center:
            target = step.target
            if (
                target
                and target.strategy == LocateStrategy.POSITION
                and target.position
                and _coord_fallback_allowed()
            ):
                x, y = target.position.x, target.position.y
                if not await self._safety.check_coordinate_safety(x, y):
                    raise RuntimeError(f"安全控制：目标坐标 ({x}, {y}) 超出屏幕安全范围")
                if not self._safety.check_random_click_pattern(x, y):
                    raise RuntimeError("安全控制：检测到随机点击模式，操作已中止")
                fallback_position = (x, y)
            elif (
                target
                and target.strategy == LocateStrategy.POSITION
                and target.position
            ):
                # Refuse naked coordinate execution by default — this is the
                # core anti-fragility guarantee of the closed loop.
                raise RuntimeError(
                    "拒绝执行：步骤仅依赖硬坐标定位，未找到稳定元素。请重新录制或为该步骤补"
                    "充 accessibility_id / selector / xpath 等稳定锚点。如确需允许，请设置环境变量 "
                    "AUTO_AGENT_ALLOW_COORDINATE_FALLBACK=1 后重试。"
                )
            else:
                raise RuntimeError(f"Cannot locate click target: {step.target}")

        if fallback_position is not None:
            x, y = fallback_position
        else:
            x, y = located.center.x, located.center.y

        if not await self._safety.check_coordinate_safety(x, y):
            raise RuntimeError(f"安全控制：目标坐标 ({x}, {y}) 超出屏幕安全范围")

        if not self._safety.check_random_click_pattern(x, y):
            raise RuntimeError("安全控制：检测到随机点击模式，操作已中止")

        button = action.get("button", "left")
        clicks = action.get("clicks", 1)

        if button == "right":
            pyautogui.rightClick(x, y)
        elif clicks == 2:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y, clicks=clicks)

    async def _execute_type(self, step: AutomationStep, action: dict) -> None:
        text = action.get("text", "")
        if not text:
            return

        if not self._safety.check_random_keyboard_pattern(text):
            raise RuntimeError("安全控制：检测到随机键盘输入模式，操作已中止")

        type_text_target = getattr(self._adapter, "type_text_target", None)
        if self._is_web_target(step.target) and callable(type_text_target):
            await type_text_target(step.target, action)
            return

        if step.target and step.target.strategy != LocateStrategy.POSITION:
            located = await self._locator.locate(step.target)
            if located and located.center:
                x, y = located.center.x, located.center.y
                if not await self._safety.check_coordinate_safety(x, y):
                    raise RuntimeError(f"安全控制：拖拽起始坐标 ({x}, {y}) 超出屏幕安全范围")
                pyautogui.click(located.center.x, located.center.y)
                await asyncio.sleep(0.1)

        try:
            text.encode("ascii")
            interval = action.get("interval", 0.02)
            pyautogui.typewrite(text, interval=interval)
        except UnicodeEncodeError:
            try:
                import pyperclip

                pyperclip.copy(text)
                pyautogui.hotkey("command", "v")
            except ImportError:
                for char in text:
                    pyautogui.press(char)

    async def _execute_hotkey(self, step: AutomationStep, action: dict) -> None:
        press_hotkey = getattr(self._adapter, "press_hotkey", None)
        if self._adapter.get_platform_name() == "web" and callable(press_hotkey):
            await press_hotkey(action)
            return

        keys = action.get("keys", [])
        if keys:
            pyautogui.hotkey(*keys)

    async def _execute_scroll(self, step: AutomationStep, action: dict) -> None:
        scroll_target = getattr(self._adapter, "scroll_target", None)
        if (self._is_web_target(step.target) or self._adapter.get_platform_name() == "web") and callable(scroll_target):
            await scroll_target(step.target, action)
            return

        delta = action.get("delta", 3)
        x = action.get("x")
        y = action.get("y")

        if x is not None and y is not None:
            pyautogui.scroll(delta, x, y)
        else:
            pyautogui.scroll(delta)

    async def _execute_drag(self, step: AutomationStep, action: dict) -> None:
        drag_target = getattr(self._adapter, "drag_target", None)
        if (self._is_web_target(step.target) or self._adapter.get_platform_name() == "web") and callable(drag_target):
            await drag_target(step.target, action)
            return

        start_x = action.get("start_x", 0)
        start_y = action.get("start_y", 0)
        end_x = action.get("end_x", 0)
        end_y = action.get("end_y", 0)
        duration = action.get("duration", 0.5)

        if not await self._safety.check_coordinate_safety(end_x, end_y):
            raise RuntimeError(f"安全控制：拖拽目标坐标 ({end_x}, {end_y}) 超出屏幕安全范围")

        pyautogui.moveTo(start_x, start_y)
        pyautogui.drag(end_x - start_x, end_y - start_y, duration=duration)

    async def _execute_switch_window(self, step: AutomationStep, action: dict) -> None:
        window_title = self._resolve_window_title(step.target, action)
        target_url = self._resolve_window_url(step.target, action)
        if not window_title and not target_url:
            raise RuntimeError("No window title or URL specified for switch_window")

        target_window = await self._find_matching_window(step.target, action)
        if not target_window:
            raise RuntimeError(f"Window not found: {window_title or target_url}")

        activate_window = getattr(self._adapter, "activate_window", None)
        if callable(activate_window):
            activated = await activate_window(target_window)
            if not activated:
                raise RuntimeError(f"Failed to activate window: {window_title or target_url}")
        else:
            with contextlib.suppress(AttributeError):
                pyautogui.getWindowsWithTitle(target_window.title)
        await asyncio.sleep(0.3)

    def _require_web_handler(self, handler_name: str, step_type: StepType):
        handler = getattr(self._adapter, handler_name, None)
        if self._adapter.get_platform_name() != "web" or not callable(handler):
            raise RuntimeError(f"{step_type.value} is only supported for web automation")
        return handler

    async def _execute_navigate(self, step: AutomationStep, action: dict) -> None:
        navigate = self._require_web_handler("navigate", StepType.NAVIGATE)
        await navigate(step.target, action)

    async def _execute_upload_file(self, step: AutomationStep, action: dict) -> None:
        upload_files = self._require_web_handler("upload_files", StepType.UPLOAD_FILE)
        await upload_files(step.target, action)

    async def _execute_download_file(self, step: AutomationStep, action: dict) -> None:
        download_target = self._require_web_handler("download_target", StepType.DOWNLOAD_FILE)
        await download_target(step.target, action)

    async def _execute_file_op(self, step: AutomationStep, action: dict) -> None:
        import os
        import shutil

        op = action.get("operation", "read")
        path = action.get("path", "")

        if op == "read":
            with open(path) as f:
                content = f.read()
            step._file_read_content = content
        elif op == "write":
            content = action.get("content", "")
            with open(path, "w") as f:
                f.write(content)
        elif op == "copy":
            dest = action.get("destination", "")
            shutil.copy2(path, dest)
        elif op == "move":
            dest = action.get("destination", "")
            shutil.move(path, dest)
        elif op == "delete":
            os.remove(path)
        elif op == "mkdir":
            os.makedirs(path, exist_ok=True)
        else:
            raise RuntimeError(f"Unknown file operation: {op}")

    async def _execute_wait(self, step: AutomationStep, action: dict) -> None:
        duration = action.get("duration", step.delay / 1000.0)
        await asyncio.sleep(duration)

    async def _execute_condition(self, step: AutomationStep, action: dict) -> None:
        return

    async def _execute_loop(self, step: AutomationStep, action: dict) -> None:
        max_iterations = action.get("max_iterations", 10)
        delay_ms = action.get("delay_ms", 1000)
        condition_type = action.get("condition_type", "count")

        if condition_type == "count" or condition_type == "element_exists" or condition_type == "element_gone":
            for _i in range(max_iterations):
                if step.target:
                    located = await self._locator.locate(step.target)
                    if not located:
                        break
                await asyncio.sleep(delay_ms / 1000.0)

    async def recover_window_context(
        self,
        step: AutomationStep,
        variables: dict,
        timeout_ms: int = 4000,
        poll_interval_ms: int = 250,
    ) -> bool:
        resolved_action = self._resolve_variables(step.action, variables)
        deadline = time.time() + max(timeout_ms, 200) / 1000.0

        while time.time() <= deadline:
            matched_window = await self._find_matching_window(step.target, resolved_action)
            if matched_window is not None:
                active_window = await self._get_active_window()
                if self._window_matches_target(active_window, step.target, resolved_action):
                    return True

                activate_window = getattr(self._adapter, "activate_window", None)
                if callable(activate_window):
                    activated = await activate_window(matched_window)
                    if activated:
                        await asyncio.sleep(0.25)
                        active_window = await self._get_active_window()
                        if active_window is None or self._window_matches_target(
                            active_window, step.target, resolved_action
                        ):
                            return True
                else:
                    return True

            await asyncio.sleep(max(poll_interval_ms, 100) / 1000.0)

        return False

    async def scroll_target_into_view(
        self,
        step: AutomationStep,
        variables: dict,
        direction: str = "center",
        delta: int = 500,
    ) -> bool:
        resolved_action = self._resolve_variables(step.action, variables)
        scroll_target = getattr(self._adapter, "scroll_target", None)
        if (self._is_web_target(step.target) or self._adapter.get_platform_name() == "web") and callable(scroll_target):
            payload = {
                "delta": delta,
                "direction": direction,
                "ensure_visible": True,
            }
            await scroll_target(step.target, payload)
            await asyncio.sleep(0.2)
            return True

        located = await self._locator.locate(step.target) if step.target else None
        if located and located.center:
            x, y = located.center.x, located.center.y
        elif step.target and step.target.position:
            x, y = step.target.position.x, step.target.position.y
        else:
            x = resolved_action.get("x")
            y = resolved_action.get("y")

        if x is not None and y is not None and not await self._safety.check_coordinate_safety(x, y):
            return False

        if direction == "up":
            scroll_delta = abs(delta)
        elif direction == "down":
            scroll_delta = -abs(delta)
        else:
            scroll_delta = delta

        if x is not None and y is not None:
            pyautogui.scroll(scroll_delta, x, y)
        else:
            pyautogui.scroll(scroll_delta)
        await asyncio.sleep(0.2)
        return True

    async def _execute_unknown(self, step: AutomationStep, action: dict) -> None:
        raise RuntimeError(f"Unknown step type: {step.type}")

    async def _take_screenshot(self) -> bytes | None:
        try:
            return await self._adapter.capture_screen()
        except Exception as e:
            logger.debug(f"Screenshot capture failed: {e}")
            return None

    async def _ensure_preconditions(self, step: AutomationStep, action: dict, variables: dict) -> None:
        preconditions = list(step.preconditions)
        if step.condition is not None and step.type != StepType.CONDITION:
            preconditions.insert(0, step.condition)

        for precondition in preconditions:
            matched = await self._evaluate_condition(step, action, variables, precondition)
            if not matched and self._should_attempt_window_precondition_recovery(precondition):
                recovered = await self.recover_window_context(
                    step,
                    variables,
                    timeout_ms=max(precondition.timeout_ms, 1200),
                    poll_interval_ms=min(max(precondition.timeout_ms // 8, 150), 400),
                )
                if recovered:
                    matched = await self._evaluate_condition(step, action, variables, precondition)
            if not matched:
                raise RuntimeError(
                    f"Precondition not met for step {step.id}: "
                    f"{precondition.field} {precondition.operator} "
                    f"{precondition.value}"
                )

    async def _ensure_window_context(self, step: AutomationStep, action: dict) -> None:
        if not self._requires_window_context(step, action):
            return

        matched_window = await self._find_matching_window(step.target, action)
        if matched_window is None:
            raise RuntimeError(
                f"执行前安全检查失败: 目标窗口不可用 ({self._describe_window_target(step.target, action)})"
            )

        active_window = await self._get_active_window()
        if self._window_matches_target(active_window, step.target, action):
            return

        activate_window = getattr(self._adapter, "activate_window", None)
        if callable(activate_window):
            activated = await activate_window(matched_window)
            if not activated:
                raise RuntimeError(
                    f"执行前安全检查失败: 无法激活目标窗口 ({self._describe_window_target(step.target, action)})"
                )

            await asyncio.sleep(0.2)
            active_window = await self._get_active_window()
            if active_window is None or self._window_matches_target(active_window, step.target, action):
                return

        if active_window is None:
            return

        raise RuntimeError(
            f"执行前安全检查失败: 当前焦点窗口与录制窗口不一致 ({self._describe_window_target(step.target, action)})"
        )

    async def _ensure_postconditions(self, step: AutomationStep, action: dict, variables: dict) -> None:
        postconditions = (step.metadata or {}).get("postconditions", [])
        if not isinstance(postconditions, list):
            return

        for postcondition in postconditions:
            condition: StepCondition | None = None
            payload: dict = {}

            if isinstance(postcondition, StepCondition):
                condition = postcondition
                payload = postcondition.model_dump(mode="json", exclude_none=True)
            elif isinstance(postcondition, dict):
                payload = postcondition
                try:
                    condition = StepCondition.model_validate(postcondition)
                except Exception:
                    logger.debug(f"Skip invalid postcondition payload: {postcondition}")
                    continue
            else:
                continue

            matched = await self._evaluate_condition(step, action, variables, condition)
            if matched:
                continue

            description = payload.get("description") or (
                f"{condition.field} {condition.operator} {condition.value}"
            )
            if bool(payload.get("critical", False)):
                raise RuntimeError(f"Postcondition not met for step {step.id}: {description}")

            logger.warning(f"Non-critical postcondition not met for step {step.id}: {description}")

    async def _evaluate_condition(self, step: AutomationStep, action: dict, variables: dict, condition) -> bool:
        if condition is None:
            return True

        field = str(condition.field or "").strip()
        operator = str(condition.operator or "eq").strip().lower()
        actual = await self._resolve_condition_value(step, action, variables, field, condition.timeout_ms)
        return self._compare_condition(actual, operator, condition.value)

    async def _resolve_condition_value(
        self,
        step: AutomationStep,
        action: dict,
        variables: dict,
        field: str,
        timeout_ms: int,
    ):
        if field in {"element_exists", "target_exists"}:
            if step.target is None:
                return False
            if step.type == StepType.SWITCH_WINDOW or self._is_window_target(step.target, action):
                return await self._window_exists(step.target, action)
            located = await self._locator.wait_for_element(step.target, timeout_ms=timeout_ms)
            return located is not None

        if field in {"element_missing", "target_missing", "element_gone"}:
            if step.target is None:
                return True
            if step.type == StepType.SWITCH_WINDOW or self._is_window_target(step.target, action):
                return not await self._window_exists(step.target, action)
            located = await self._locator.wait_for_element(step.target, timeout_ms=timeout_ms)
            return located is None

        if field in {"window_exists", "window_available"}:
            if step.target is None:
                return False
            return await self._window_exists(step.target, action)

        if field in {"window_missing", "window_gone"}:
            if step.target is None:
                return True
            return not await self._window_exists(step.target, action)

        if field in {"page_stable", "ui_stable"}:
            return await self._is_page_stable(timeout_ms)

        if field in {"window_title", "active_window_title"}:
            window = await self._get_active_window()
            return getattr(window, "title", None) if window else None

        if field in {"window_app", "active_window_app"}:
            window = await self._get_active_window()
            return getattr(window, "app_name", None) if window else None

        if field in {"active_url", "window_url"}:
            window = await self._get_active_window()
            return getattr(window, "url", None) if window else None

        if field.startswith("var."):
            return variables.get(field[4:])
        if field.startswith("action."):
            return action.get(field[7:])
        if field.startswith("metadata."):
            return step.metadata.get(field[9:])
        if field.startswith("target."):
            return getattr(step.target, field[7:], None) if step.target else None
        if field == "step.type":
            return step.type.value

        if field in action:
            return action.get(field)
        if field in variables:
            return variables.get(field)
        return step.metadata.get(field)

    async def _get_active_window(self):
        getter = getattr(self._adapter, "get_active_window", None)
        if not callable(getter):
            return None
        try:
            return await getter()
        except Exception as e:
            logger.debug(f"Failed to get active window: {e}")
            return None

    async def _is_page_stable(self, timeout_ms: int) -> bool:
        platform_name = ""
        with contextlib.suppress(Exception):
            platform_name = self._adapter.get_platform_name()

        if platform_name and platform_name != "web":
            # Desktop stability is hard to infer reliably; avoid false negatives.
            await asyncio.sleep(min(max(timeout_ms, 0), 400) / 1000.0)
            return True

        capture = getattr(self._adapter, "capture_screen", None)
        if not callable(capture):
            return True

        timeout_seconds = max(timeout_ms, 400) / 1000.0
        deadline = time.time() + timeout_seconds

        try:
            previous = await capture()
        except Exception as e:
            logger.debug(f"Failed to capture screen for page stability check: {e}")
            return True

        stable_streak = 0
        while time.time() < deadline:
            await asyncio.sleep(0.12)
            try:
                current = await capture()
            except Exception:
                return True

            change_ratio = self._screen_change_ratio(previous, current)
            if change_ratio <= 0.01:
                stable_streak += 1
                if stable_streak >= 2:
                    return True
            else:
                stable_streak = 0
            previous = current

        return False

    @staticmethod
    def _screen_change_ratio(previous: bytes | None, current: bytes | None) -> float:
        if not previous or not current:
            return 0.0

        sample_size = min(len(previous), len(current))
        if sample_size == 0:
            return 0.0

        stride = max(1, sample_size // 6000)
        comparisons = 0
        different = 0
        for idx in range(0, sample_size, stride):
            comparisons += 1
            if previous[idx] != current[idx]:
                different += 1

        if comparisons == 0:
            return 0.0
        return different / comparisons

    def _is_window_target(self, target: StepTarget | None, action: dict | None = None) -> bool:
        if target is None:
            return False

        window_title = self._resolve_window_title(target, action)
        target_url = self._resolve_window_url(target, action)
        if not window_title and not target_url:
            return False

        return not any(
            [
                target.accessibility_id,
                target.text_contains,
                target.role,
                target.class_name,
                target.selector,
                target.xpath,
                target.image_path,
                target.position,
                target.expected_attributes,
            ]
        )

    def _requires_window_context(self, step: AutomationStep, action: dict | None = None) -> bool:
        if step.type in (StepType.WAIT, StepType.CONDITION, StepType.LOOP, StepType.SWITCH_WINDOW):
            return False

        with contextlib.suppress(Exception):
            if self._adapter.get_platform_name() == "web":
                return False

        target = step.target
        if target is None:
            return False
        if self._is_web_target(target):
            return False

        window_title = self._resolve_window_title(target, action)
        target_url = self._resolve_window_url(target, action)
        if self._is_generic_window_hint(window_title) and not target_url:
            return False
        return bool(window_title or target_url)

    @staticmethod
    def _is_generic_window_hint(window_title: str | None) -> bool:
        normalized = str(window_title or "").strip().lower()
        return normalized in {"", "web", "browser", "desktop", "unknown"}

    def _resolve_window_title(self, target: StepTarget | None, action: dict | None = None) -> str | None:
        action = action or {}
        return action.get("window_title") or action.get("title") or (target.window_title if target else None)

    def _resolve_window_url(self, target: StepTarget | None, action: dict | None = None) -> str | None:
        action = action or {}
        return action.get("url") or (target.url if target else None)

    def _describe_window_target(self, target: StepTarget | None, action: dict | None = None) -> str:
        return self._resolve_window_title(target, action) or self._resolve_window_url(target, action) or "unknown"

    async def _window_exists(self, target: StepTarget | None, action: dict | None = None) -> bool:
        return await self._find_matching_window(target, action) is not None

    async def _find_matching_window(self, target: StepTarget | None, action: dict | None = None):
        window_title = self._resolve_window_title(target, action)
        target_url = self._resolve_window_url(target, action)
        if not window_title and not target_url:
            return None

        windows = await self._adapter.get_windows()
        best_window = None
        best_score = 0.0
        for window in windows:
            score = self._window_match_score(window, target, action)
            if score > best_score:
                best_window = window
                best_score = score
        if best_window is not None and best_score >= WINDOW_MATCH_THRESHOLD:
            return best_window
        return None

    def _window_matches_target(self, window, target: StepTarget | None, action: dict | None = None) -> bool:
        if window is None:
            return False

        return self._window_match_score(window, target, action) >= WINDOW_MATCH_THRESHOLD

    def _should_count_as_safety_failure(self, step: AutomationStep, action: dict, error_message: str) -> bool:
        if "录制阶段目标定位仍不确定" in error_message:
            return False
        if self._is_window_target(step.target, action):
            lowered = error_message.lower()
            if (
                "窗口未找到" in error_message
                or "window not found" in lowered
                or "执行前验证失败: 窗口未找到" in error_message
            ):
                return False
        return True

    @staticmethod
    def _compare_condition(actual, operator: str, expected) -> bool:
        if operator in {"eq", "=="}:
            return actual == expected
        if operator in {"ne", "!="}:
            return actual != expected
        if operator in {"gt", ">"}:
            return actual is not None and expected is not None and actual > expected
        if operator in {"gte", ">="}:
            return actual is not None and expected is not None and actual >= expected
        if operator in {"lt", "<"}:
            return actual is not None and expected is not None and actual < expected
        if operator in {"lte", "<="}:
            return actual is not None and expected is not None and actual <= expected
        if operator == "contains":
            return actual is not None and expected in actual
        if operator == "not_contains":
            return actual is None or expected not in actual
        if operator == "in":
            return actual in expected if expected is not None else False
        if operator == "not_in":
            return actual not in expected if expected is not None else True
        if operator in {"truthy", "is_true"}:
            return bool(actual)
        if operator in {"falsy", "is_false"}:
            return not bool(actual)
        if operator == "exists":
            return actual is not None
        if operator == "not_exists":
            return actual is None
        return actual == expected

    @staticmethod
    def _is_web_target(target) -> bool:
        if target is None:
            return False
        return bool(
            target.strategy in {LocateStrategy.CSS_SELECTOR, LocateStrategy.XPATH}
            or target.selector
            or target.xpath
            or target.url
            or target.frame
        )

    @staticmethod
    def _resolve_variables(action: dict, variables: dict) -> dict:
        return {key: StepExecutor._resolve_value(value, variables) for key, value in action.items()}

    @staticmethod
    def _normalize_window_text(value: str | None) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return "".join(WINDOW_TOKEN_RE.findall(text))

    @classmethod
    def _window_text_tokens(cls, value: str | None) -> set[str]:
        text = str(value or "").strip().lower()
        return {token for token in WINDOW_TOKEN_RE.findall(text) if len(token) >= 2}

    @classmethod
    def _window_title_candidates(cls, value: str | None) -> list[str]:
        raw = str(value or "").strip()
        if not raw:
            return []
        candidates: list[str] = [raw]
        for segment in WINDOW_SEGMENT_SPLIT_RE.split(raw):
            segment = segment.strip()
            normalized = cls._normalize_window_text(segment)
            if not segment or normalized == cls._normalize_window_text(raw):
                continue
            if len(normalized) < 2 or cls._is_generic_window_hint(segment):
                continue
            candidates.append(segment)
        return candidates

    @classmethod
    def _window_text_match_score(cls, expected: str | None, actual: str | None) -> float:
        expected_norm = cls._normalize_window_text(expected)
        actual_norm = cls._normalize_window_text(actual)
        if not expected_norm or not actual_norm:
            return 0.0
        if expected_norm == actual_norm:
            return 1.0
        if expected_norm in actual_norm or actual_norm in expected_norm:
            return 0.92

        expected_tokens = cls._window_text_tokens(expected)
        actual_tokens = cls._window_text_tokens(actual)
        if not expected_tokens or not actual_tokens:
            return 0.0

        overlap = expected_tokens & actual_tokens
        if not overlap:
            return 0.0

        expected_coverage = len(overlap) / len(expected_tokens)
        actual_coverage = len(overlap) / len(actual_tokens)
        if expected_coverage >= 0.999:
            return 0.88
        if expected_coverage >= 0.6 and actual_coverage >= 0.34:
            return 0.78
        return 0.0

    @classmethod
    def _url_match_score(cls, expected: str | None, actual: str | None) -> float:
        expected_norm = str(expected or "").strip().lower().rstrip("/")
        actual_norm = str(actual or "").strip().lower().rstrip("/")
        if not expected_norm or not actual_norm:
            return 0.0
        if expected_norm == actual_norm:
            return 1.0
        if expected_norm in actual_norm or actual_norm in expected_norm:
            return 0.95
        return 0.0

    def _window_match_score(self, window, target: StepTarget | None, action: dict | None = None) -> float:
        if window is None:
            return 0.0

        score = self._url_match_score(
            self._resolve_window_url(target, action),
            getattr(window, "url", None),
        )
        title = getattr(window, "title", None)
        app_name = getattr(window, "app_name", None)
        for candidate in self._window_title_candidates(self._resolve_window_title(target, action)):
            score = max(
                score,
                self._window_text_match_score(candidate, title),
                self._window_text_match_score(candidate, app_name),
            )
        return score

    @staticmethod
    def _should_attempt_window_precondition_recovery(condition: StepCondition) -> bool:
        field = str(condition.field or "").strip().lower()
        operator = str(condition.operator or "eq").strip().lower()
        if field not in {"window_exists", "window_available"}:
            return False
        if operator in {"truthy", "is_true", "exists"}:
            return True
        expected = condition.value
        if isinstance(expected, str):
            expected = expected.strip().lower() in {"true", "1", "yes"}
        return operator in {"eq", "=="} and expected is True

    @staticmethod
    def _resolve_value(value, variables: dict):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            var_name = value[2:-1]
            return variables.get(var_name, value)
        if isinstance(value, dict):
            return {key: StepExecutor._resolve_value(item, variables) for key, item in value.items()}
        if isinstance(value, list):
            return [StepExecutor._resolve_value(item, variables) for item in value]
        return value
