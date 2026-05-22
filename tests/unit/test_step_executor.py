import sys
from types import SimpleNamespace

import pytest

import src.executor.step_executor as step_executor_module
from src.executor.element_locator import LocatedElement
from src.executor.step_executor import StepExecutor
from src.models.automation import AutomationStep, LocateStrategy, StepCondition, StepTarget, StepType
from src.models.desktop import Point, Rect, UIElement


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
        self.get_windows_calls = 0
        self.capture_screen_calls = 0

    def get_platform_name(self):
        return "desktop"

    async def capture_screen(self):
        self.capture_screen_calls += 1
        return None

    async def get_screen_size(self):
        return (1920, 1080)

    async def get_windows(self):
        self.get_windows_calls += 1
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


def _window_target(title: str) -> StepTarget:
    return StepTarget(
        strategy=LocateStrategy.TEXT_MATCH,
        title=title,
        window_title=title,
    )


@pytest.mark.asyncio
async def test_switch_window_uses_window_lookup_instead_of_element_locator():
    locator = DummyLocator()
    adapter = DummyAdapter(
        windows=[DummyWindow("Code - Insiders"), DummyWindow("Terminal")],
        active_window=DummyWindow("Terminal"),
    )
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
    assert adapter.get_windows_calls == 1
    assert result.step_log.metadata["timing"]["window_match_calls"] == 1
    assert result.step_log.metadata["timing"]["window_match_ms"] >= 0


@pytest.mark.asyncio
async def test_switch_window_skips_lookup_when_target_already_active():
    locator = DummyLocator()
    active = DummyWindow("Code - Insiders")
    adapter = DummyAdapter(windows=[active], active_window=active)
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-switch-active",
        type=StepType.SWITCH_WINDOW,
        action={"window_title": "Code - Insiders"},
        target=_window_target("Code - Insiders"),
        description="Stay on current target window",
    )

    result = await executor.execute_step(step, {}, "exec-switch-active", 0)

    assert result.success is True
    assert adapter.get_windows_calls == 0
    assert adapter.activated_titles == []


@pytest.mark.asyncio
async def test_switch_window_exact_match_skips_fuzzy_scoring(monkeypatch):
    locator = DummyLocator()
    adapter = DummyAdapter(
        windows=[DummyWindow("Code - Insiders"), DummyWindow("Terminal")],
        active_window=DummyWindow("Terminal"),
    )
    adapter._active_window = None
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    def fail_if_called(window, target, action=None):
        raise AssertionError("_window_match_score should not run for exact matches")

    monkeypatch.setattr(executor, "_window_match_score", fail_if_called)

    step = AutomationStep(
        id="step-switch-exact-fast-path",
        type=StepType.SWITCH_WINDOW,
        action={"window_title": "Code - Insiders"},
        target=_window_target("Code - Insiders"),
        description="Use exact window title fast path",
    )

    result = await executor.execute_step(step, {}, "exec-switch-exact-fast-path", 0)

    assert result.success is True
    assert adapter.activated_titles == ["Code - Insiders"]


@pytest.mark.asyncio
async def test_click_step_skips_window_scan_when_target_window_already_active(monkeypatch):
    class StableLocator(DummyLocator):
        async def locate(self, target):
            self.locate_calls += 1
            return LocatedElement(
                element=UIElement(
                    role="button",
                    title="保存",
                    bounds=Rect(x=120, y=220, width=80, height=30),
                ),
                strategy_used=LocateStrategy.TEXT_MATCH,
                position=Point(x=160, y=235),
                confidence=0.95,
                provider="native_locator",
            )

    locator = StableLocator()
    active = DummyWindow("Code - Insiders")
    adapter = DummyAdapter(windows=[active], active_window=active)
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: None,
        rightClick=lambda x, y: None,
        doubleClick=lambda x, y: None,
        size=lambda: (1920, 1080),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-window-context-active",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(
            strategy=LocateStrategy.TEXT_MATCH,
            title="保存",
            window_title="Code - Insiders",
        ),
        description="Click with already-active target window",
        verification_enabled=False,
    )

    result = await executor.execute_step(step, {}, "exec-window-context-active", 0)

    assert result.success is True
    assert adapter.get_windows_calls == 0


@pytest.mark.asyncio
async def test_recover_window_context_skips_window_scan_when_target_already_active():
    locator = DummyLocator()
    active = DummyWindow("Code - Insiders")
    adapter = DummyAdapter(windows=[active], active_window=active)
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    step = AutomationStep(
        id="step-recover-window-active",
        type=StepType.WAIT,
        action={"duration": 0},
        target=_window_target("Code - Insiders"),
        description="Recovery with already-active target window",
    )

    recovered = await executor.recover_window_context(step, {})

    assert recovered is True
    assert adapter.get_windows_calls == 0


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
    assert adapter.get_windows_calls == 0


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
    adapter = DummyAdapter(
        windows=[DummyWindow("企业微信"), DummyWindow("Finder")],
        active_window=DummyWindow("Finder"),
    )
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
async def test_click_step_records_locator_telemetry(monkeypatch):
    class TelemetryLocator(DummyLocator):
        async def locate(self, target):
            self.locate_calls += 1
            return LocatedElement(
                element=UIElement(role="button", title="保存", bounds=None),
                strategy_used=LocateStrategy.IMAGE_MATCH,
                position=Point(x=120, y=240),
                confidence=0.91,
                provider="cloud_llm",
                provider_chain=["qwen_local", "cloud_llm"],
                fallback_chain=["cloud_llm"],
                attempted_providers=["qwen_local", "cloud_llm"],
                healed=True,
            )

    locator = TelemetryLocator()
    adapter = DummyAdapter(windows=[DummyWindow("Code - Insiders")])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: None,
        rightClick=lambda x, y: None,
        doubleClick=lambda x, y: None,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-locator-telemetry",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.IMAGE_MATCH, image_path="template.png"),
        description="Click using grounded locator",
        verification_enabled=False,
    )

    result = await executor.execute_step(step, {}, "exec-telemetry", 0)

    assert result.success is True
    assert result.step_log.metadata["locator"]["provider"] == "cloud_llm"
    assert result.step_log.metadata["locator"]["attempted_providers"] == ["qwen_local", "cloud_llm"]
    assert result.step_log.metadata["locator"]["healed"] is True


@pytest.mark.asyncio
async def test_click_step_reuses_verified_locator_without_second_lookup(monkeypatch):
    class StableLocator(DummyLocator):
        async def locate(self, target):
            self.locate_calls += 1
            return LocatedElement(
                element=UIElement(
                    role="button",
                    title="保存",
                    bounds=Rect(x=100, y=220, width=80, height=30),
                ),
                strategy_used=LocateStrategy.TEXT_MATCH,
                position=Point(x=140, y=235),
                confidence=0.95,
                provider="native_locator",
            )

    locator = StableLocator()
    adapter = DummyAdapter(windows=[DummyWindow("Code - Insiders")])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: None,
        rightClick=lambda x, y: None,
        doubleClick=lambda x, y: None,
        size=lambda: (1920, 1080),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-reuse-locator",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.TEXT_MATCH, title="保存"),
        description="Reuse verified locator",
        verification_enabled=True,
    )

    result = await executor.execute_step(step, {}, "exec-reuse-locator", 0)

    assert result.success is True
    assert locator.locate_calls == 1


@pytest.mark.asyncio
async def test_type_step_reuses_verified_locator_without_second_lookup(monkeypatch):
    class StableLocator(DummyLocator):
        async def locate(self, target):
            self.locate_calls += 1
            return LocatedElement(
                element=UIElement(
                    role="textbox",
                    title="输入框",
                    bounds=Rect(x=180, y=260, width=220, height=36),
                ),
                strategy_used=LocateStrategy.TEXT_MATCH,
                position=Point(x=290, y=278),
                confidence=0.94,
                provider="native_locator",
            )

    locator = StableLocator()
    adapter = DummyAdapter(windows=[DummyWindow("Code - Insiders")])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y: None,
        typewrite=lambda text, interval=0.02: None,
        press=lambda key: None,
        hotkey=lambda *keys: None,
        size=lambda: (1920, 1080),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-reuse-type-locator",
        type=StepType.TYPE,
        action={"text": "hello"},
        target=StepTarget(strategy=LocateStrategy.TEXT_MATCH, title="输入框"),
        description="Reuse verified locator for typing",
        verification_enabled=True,
    )

    result = await executor.execute_step(step, {}, "exec-reuse-type-locator", 0)

    assert result.success is True
    assert locator.locate_calls == 1


@pytest.mark.asyncio
async def test_execute_step_skips_screenshots_when_visual_verify_disabled(monkeypatch):
    class StableLocator(DummyLocator):
        async def locate(self, target):
            self.locate_calls += 1
            return LocatedElement(
                element=UIElement(
                    role="button",
                    title="保存",
                    bounds=Rect(x=100, y=220, width=80, height=30),
                ),
                strategy_used=LocateStrategy.TEXT_MATCH,
                position=Point(x=140, y=235),
                confidence=0.95,
                provider="native_locator",
            )

    locator = StableLocator()
    adapter = DummyAdapter(windows=[DummyWindow("Code - Insiders")])
    executor = StepExecutor(locator, adapter, enable_visual_verify=False)

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: None,
        rightClick=lambda x, y: None,
        doubleClick=lambda x, y: None,
        size=lambda: (1920, 1080),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-no-screenshots",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.TEXT_MATCH, title="保存"),
        description="Skip screenshots when visual verify disabled",
        verification_enabled=True,
    )

    result = await executor.execute_step(step, {}, "exec-no-screenshots", 0)

    assert result.success is True
    assert adapter.capture_screen_calls == 0
    assert result.screenshot_before is None
    assert result.screenshot_after is None


@pytest.mark.asyncio
async def test_execute_step_records_timing_metadata(monkeypatch):
    class StableLocator(DummyLocator):
        async def locate(self, target):
            self.locate_calls += 1
            return LocatedElement(
                element=UIElement(
                    role="button",
                    title="保存",
                    bounds=Rect(x=100, y=220, width=80, height=30),
                ),
                strategy_used=LocateStrategy.TEXT_MATCH,
                position=Point(x=140, y=235),
                confidence=0.95,
                provider="native_locator",
            )

    locator = StableLocator()
    adapter = DummyAdapter(windows=[DummyWindow("Code - Insiders")])
    executor = StepExecutor(locator, adapter, enable_visual_verify=True)

    fake_pyautogui = SimpleNamespace(
        click=lambda x, y, clicks=1: None,
        rightClick=lambda x, y: None,
        doubleClick=lambda x, y: None,
        size=lambda: (1920, 1080),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(step_executor_module, "pyautogui", fake_pyautogui)

    step = AutomationStep(
        id="step-timing-metadata",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.TEXT_MATCH, title="保存"),
        description="Record timing telemetry",
        verification_enabled=True,
    )

    result = await executor.execute_step(step, {}, "exec-timing", 0)

    timing = result.step_log.metadata["timing"]

    assert result.success is True
    assert timing["locate_calls"] == 1
    assert timing["verify_calls"] == 1
    assert timing["screenshot_capture_calls"] == 2
    assert timing["locate_ms"] >= 0
    assert timing["verify_ms"] >= 0
    assert timing["screenshot_capture_ms"] >= 0
    assert timing["screenshot_before_ms"] >= 0
    assert timing["screenshot_after_ms"] >= 0
    assert timing["step_elapsed_ms"] >= 0


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
