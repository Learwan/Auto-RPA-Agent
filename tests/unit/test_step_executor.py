import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

import src.executor.step_executor as step_executor_module
from src.executor.step_executor import StepExecutor
from src.models.automation import AutomationStep, LocateStrategy, StepCondition, StepTarget, StepType


class DummyLocator:
    def __init__(self):
        self.locate_calls = 0
        self.wait_calls = 0

    async def locate(self, target):
        self.locate_calls += 1
        return None

    async def wait_for_element(self, target, timeout_ms=5000):
        self.wait_calls += 1
        return None


class DummyWindow:
    def __init__(self, title: str, url: str | None = None, app_name: str | None = None):
        self.title = title
        self.url = url
        self.app_name = app_name or title


class DummyAdapter:
    def __init__(self, windows=None, active_window=None):
        self._windows = windows or []
        self.activated_titles: list[str] = []
        self._active_window = active_window or (self._windows[0] if self._windows else None)

    def get_platform_name(self):
        return "desktop"

    async def capture_screen(self):
        return None

    async def get_windows(self):
        return list(self._windows)

    async def get_active_window(self):
        return self._active_window

    async def activate_window(self, window):
        self.activated_titles.append(window.title)
        self._active_window = window
        return True


class DelayedWindowAdapter(DummyAdapter):
    def __init__(self, window_sequences, active_window=None):
        super().__init__(windows=[], active_window=active_window)
        self._window_sequences = list(window_sequences)
        self._read_count = 0

    async def get_windows(self):
        index = min(self._read_count, len(self._window_sequences) - 1)
        self._read_count += 1
        return list(self._window_sequences[index])


def test_step_executor_headless_import_uses_safe_pyautogui_fallback(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if name == "pyautogui":
            raise OSError("DISPLAY is not set")
        return real_import(name, globals, locals, fromlist, level)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", fake_import)
        reloaded = importlib.reload(step_executor_module)
        assert reloaded.pyautogui.size() == (1920, 1080)
        with pytest.raises(RuntimeError, match="headless"):
            reloaded.pyautogui.click(10, 20)
    importlib.reload(step_executor_module)


def test_step_executor_force_headless_skips_pyautogui_import(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *import_args, **import_kwargs):
        if name == "pyautogui":
            raise AssertionError("pyautogui should not be imported when force headless is enabled")
        return real_import(name, *import_args, **import_kwargs)

    with monkeypatch.context() as scoped:
        scoped.setenv("AUTO_AGENT_FORCE_HEADLESS", "1")
        scoped.setattr(builtins, "__import__", fake_import)
        gui_fallback = step_executor_module._load_pyautogui()
        assert gui_fallback.size() == (1920, 1080)
        with pytest.raises(RuntimeError, match="headless"):
            gui_fallback.click(1, 1)


def _window_target(title: str) -> StepTarget:
    return StepTarget(
        strategy=LocateStrategy.TEXT_MATCH,
        title=title,
        window_title=title,
    )


@pytest.mark.asyncio
async def test_switch_window_uses_window_lookup_instead_of_element_locator():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[DummyWindow("Code - Insiders")])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-switch",
        type=StepType.SWITCH_WINDOW,
        action={"window_title": "Code - Insiders"},
        target=_window_target("Code - Insiders"),
        description="Switch to Code",
    )

    result = await executor.execute_step(step, {}, "exec-1", 0)

    assert result.success is True
    assert locator.locate_calls == 0
    assert adapter.activated_titles == ["Code - Insiders"]


@pytest.mark.asyncio
async def test_window_condition_uses_window_lookup_instead_of_element_wait():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[DummyWindow("企业微信")])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-condition",
        type=StepType.CONDITION,
        action={"check": "element_exists", "window_title": "企业微信"},
        target=_window_target("企业微信"),
        condition=StepCondition(field="element_exists", operator="truthy", value=True, timeout_ms=1000),
        description="Wait for enterprise wechat window",
    )

    result = await executor.execute_step(step, {}, "exec-2", 0)

    assert result.success is True
    assert locator.wait_calls == 0


@pytest.mark.asyncio
async def test_missing_window_does_not_trip_safety_controller_after_repeats():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-missing-window",
        type=StepType.SWITCH_WINDOW,
        action={"window_title": "企业微信"},
        target=_window_target("企业微信"),
        description="Switch to enterprise wechat",
    )

    for index in range(3):
        result = await executor.execute_step(step, {}, f"exec-missing-{index}", index)
        assert result.success is False
        assert "窗口未找到" in (result.step_log.error_message or "")

    assert executor.safety_controller.is_paused() is False


@pytest.mark.asyncio
async def test_switch_window_matches_dynamic_title_segment():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[DummyWindow("企业微信")], active_window=None)
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-switch-dynamic",
        type=StepType.SWITCH_WINDOW,
        action={"window_title": "SOP 自动生成 - 企业微信"},
        target=_window_target("SOP 自动生成 - 企业微信"),
        description="Switch using recorded dynamic title",
    )

    result = await executor.execute_step(step, {}, "exec-dynamic-window", 0)

    assert result.success is True
    assert adapter.activated_titles == ["企业微信"]


@pytest.mark.asyncio
async def test_window_matching_can_fallback_to_app_name():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[DummyWindow("", app_name="企业微信")], active_window=None)
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-switch-app-name",
        type=StepType.SWITCH_WINDOW,
        action={"window_title": "企业微信"},
        target=_window_target("企业微信"),
        description="Switch using app name fallback",
    )

    result = await executor.execute_step(step, {}, "exec-app-name-window", 0)

    assert result.success is True


@pytest.mark.asyncio
async def test_window_precondition_attempts_runtime_recovery():
    locator = DummyLocator()
    adapter = DelayedWindowAdapter(
        window_sequences=[
            [],
            [DummyWindow("企业微信")],
        ],
        active_window=None,
    )
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-window-precondition-recovery",
        type=StepType.WAIT,
        action={"duration": 0},
        target=_window_target("SOP 自动生成 - 企业微信"),
        preconditions=[StepCondition(field="window_exists", operator="truthy", value=True, timeout_ms=200)],
        description="Wait for recoverable window precondition",
    )

    result = await executor.execute_step(step, {}, "exec-window-precondition", 0)

    assert result.success is True


@pytest.mark.asyncio
async def test_click_step_fails_when_recorded_window_is_missing(monkeypatch):
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[], active_window=None)
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)
    click_calls: list[tuple[int, int, int]] = []

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: click_calls.append((x, y, clicks)),
        rightClick=lambda x, y: click_calls.append((x, y, 1)),
        doubleClick=lambda x, y: click_calls.append((x, y, 2)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-window-guard",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(
            strategy=LocateStrategy.POSITION,
            position={"x": 120, "y": 240},
            window_title="企业微信",
        ),
        description="Click in a missing target window",
    )

    result = await executor.execute_step(step, {}, "exec-window-guard", 0)

    assert result.success is False
    # Closed-loop guarantee: an unconfirmed coordinate-only step is refused
    # before we even reach the "window missing" branch.
    error_msg = result.step_log.error_message or ""
    assert "拒绝执行" in error_msg or "目标窗口不可用" in error_msg
    assert click_calls == []


@pytest.mark.asyncio
async def test_page_stable_precondition_supported_for_wait_step():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-page-stable",
        type=StepType.WAIT,
        action={"duration": 0},
        preconditions=[StepCondition(field="page_stable", operator="truthy", value=True, timeout_ms=300)],
        description="Wait for stable page",
    )

    result = await executor.execute_step(step, {}, "exec-page-stable", 0)

    assert result.success is True


@pytest.mark.asyncio
async def test_non_critical_postcondition_failure_does_not_fail_step():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-postcondition-warning",
        type=StepType.WAIT,
        action={"duration": 0},
        metadata={
            "postconditions": [
                {
                    "field": "var.ready",
                    "operator": "eq",
                    "value": True,
                    "timeout_ms": 500,
                    "critical": False,
                    "description": "Optional readiness flag should be true",
                }
            ]
        },
        description="Wait with advisory postcondition",
    )

    result = await executor.execute_step(step, {}, "exec-postcondition-warning", 0)

    assert result.success is True


@pytest.mark.asyncio
async def test_critical_postcondition_failure_fails_step():
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-postcondition-critical",
        type=StepType.WAIT,
        action={"duration": 0},
        metadata={
            "postconditions": [
                {
                    "field": "var.ready",
                    "operator": "eq",
                    "value": True,
                    "timeout_ms": 500,
                    "critical": True,
                    "description": "Required readiness flag should be true",
                }
            ]
        },
        description="Wait with required postcondition",
    )

    result = await executor.execute_step(step, {}, "exec-postcondition-critical", 0)

    assert result.success is False
    assert "Postcondition not met" in (result.step_log.error_message or "")


@pytest.mark.asyncio
async def test_uncertain_recorded_locator_is_refused_by_default(monkeypatch):
    """The closed loop refuses fragile POSITION-only steps unless explicitly opted in."""
    locator = DummyLocator()
    adapter = DummyAdapter(windows=[DummyWindow("Example App")], active_window=DummyWindow("Example App"))
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)
    click_calls: list[tuple[int, int, int]] = []

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: click_calls.append((x, y, clicks)),
        rightClick=lambda x, y: click_calls.append((x, y, 1)),
        doubleClick=lambda x, y: click_calls.append((x, y, 2)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-uncertain-locator",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(
            strategy=LocateStrategy.POSITION,
            position={"x": 120, "y": 240},
            window_title="Example App",
        ),
        metadata={
            "locator_requires_confirmation": True,
            "locator_confirmed": False,
            "locator_confirmation_reason": "录制结果主要依赖坐标，界面轻微变化就可能失效。",
        },
        description="Click uncertain recorded target",
    )

    # Default: refuse to run a coordinate-only step.
    monkeypatch.delenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", raising=False)
    result = await executor.execute_step(step, {}, "exec-uncertain-locator", 0)

    assert result.success is False
    assert "拒绝执行" in (result.step_log.error_message or "")
    assert click_calls == []
    assert locator.locate_calls == 0
    assert executor.safety_controller.is_paused() is False

    # Opt-in escape hatch still works for niche scenarios.
    monkeypatch.setenv("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", "1")
    result = await executor.execute_step(step, {}, "exec-uncertain-locator-opted-in", 0)
    assert result.success is True
    assert click_calls == [(120, 240, 1)]
    assert locator.locate_calls >= 1
