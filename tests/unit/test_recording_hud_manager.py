from src.models.desktop import Rect, UIElement, WindowInfo
from src.models.operation import (
    KeyAction,
    KeyboardEventData,
    MouseAction,
    MouseButton,
    MouseEventData,
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)
from src.recorder.hud_manager import RecordingHUDManager, build_recording_prompt_context, summarize_operation_event


class _FakeProcess:
    def poll(self):
        return None


def _make_window() -> WindowInfo:
    return WindowInfo(
        window_id=1,
        title="编辑商品",
        app_name="Chrome",
        pid=123,
        bounds=Rect(x=0, y=0, width=1440, height=900),
        is_active=True,
    )


def _make_element() -> UIElement:
    return UIElement(
        role="button",
        title="保存",
        identifier="save-btn",
        bounds=Rect(x=200, y=120, width=88, height=32),
    )


def test_summarize_operation_event_uses_window_and_element_context():
    event = OperationEvent(
        id="op-1",
        session_id="session-1",
        seq_num=1,
        timestamp=1000,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=240, y=140, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(active_window=_make_window(), focused_element=_make_element(), platform="macos"),
    )

    summary = summarize_operation_event(event)

    assert summary["action"] == "鼠标点击"
    assert "Chrome" in summary["window"]
    assert "保存" in summary["focused"]
    assert "(240, 140)" in summary["detail"]


def test_summarize_operation_event_redacts_sensitive_keyboard_input():
    event = OperationEvent(
        id="op-2",
        session_id="session-1",
        seq_num=2,
        timestamp=2000,
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(
            key="text_input",
            action=KeyAction.INPUT,
            text="super-secret",
            is_sensitive=True,
        ),
        context=OperationContext(active_window=_make_window(), focused_element=None, platform="macos"),
    )

    summary = summarize_operation_event(event)

    assert summary["action"] == "键盘输入"
    assert summary["detail"] == "敏感输入已脱敏"


def test_summarize_operation_event_uses_focused_text_preview_when_label_missing():
    event = OperationEvent(
        id="op-2b",
        session_id="session-1",
        seq_num=3,
        timestamp=2500,
        type=OperationType.WINDOW_SWITCH,
        data=WindowEventData(from_window=None, to_window=_make_window(), method="focus"),
        context=OperationContext(
            active_window=_make_window(),
            focused_element=UIElement(role="textfield", value="sku-001"),
            platform="macos",
        ),
    )

    summary = summarize_operation_event(event)

    assert summary["focused"] == "sku-001"


def test_build_recording_prompt_context_includes_recent_events_and_focus():
    context = build_recording_prompt_context(
        session_name="订单录制",
        status="recording",
        operation_count=12,
        recent_events=[
            {
                "action": "鼠标点击",
                "detail": "click @ (240, 140)",
                "window": "Chrome · 编辑商品",
                "focused": "保存",
            },
            {
                "action": "键盘输入",
                "detail": "输入: sku-001",
                "window": "Chrome · 编辑商品",
                "focused": "SKU 输入框",
            },
        ],
        latest_window="Chrome · 编辑商品",
        latest_focused="SKU 输入框",
    )

    assert "订单录制" in context
    assert "已捕获操作数: 12" in context
    assert "当前窗口: Chrome · 编辑商品" in context
    assert "当前焦点元素: SKU 输入框" in context
    assert "鼠标点击" in context
    assert "键盘输入" in context


def test_set_interaction_can_disable_without_extra_assistant_message(monkeypatch):
    manager = RecordingHUDManager()
    manager._process = _FakeProcess()
    manager._active_session_id = "session-1"
    commands: list[dict] = []

    monkeypatch.setattr(manager, "_send_command", lambda payload: commands.append(payload))

    manager.set_interaction(True, session_id="session-1")

    assert manager._interactive is True
    assert commands[0] == {"type": "mode", "interactive": True}
    assert commands[1]["type"] == "append_message"

    commands.clear()
    manager.set_interaction(False, session_id="session-1")

    assert manager._interactive is False
    assert commands == [{"type": "mode", "interactive": False}]


def test_overlay_message_can_request_passive_mode(monkeypatch):
    manager = RecordingHUDManager()
    manager._process = _FakeProcess()
    manager._active_session_id = "session-1"
    manager._interactive = True
    commands: list[dict] = []

    monkeypatch.setattr(manager, "_send_command", lambda payload: commands.append(payload))

    manager._handle_overlay_message({"type": "set_interaction", "interactive": False})

    assert manager._interactive is False
    assert commands == [{"type": "mode", "interactive": False}]


def test_overlay_message_can_request_focus_context(monkeypatch):
    manager = RecordingHUDManager()
    manager._process = _FakeProcess()
    manager._active_session_id = "session-1"
    commands: list[dict] = []
    calls: list[str] = []

    monkeypatch.setattr(manager, "_send_command", lambda payload: commands.append(payload))
    manager.set_focus_context_request_handler(lambda: calls.append("capture"))

    manager._handle_overlay_message({"type": "capture_focus_context"})

    assert calls == ["capture"]
    assert commands == [
        {
            "type": "append_message",
            "role": "assistant",
            "content": "正在抓取当前焦点上下文，请稍候...",
        }
    ]


def test_present_focus_context_opens_interaction_and_sends_summary(monkeypatch):
    manager = RecordingHUDManager()
    manager._process = _FakeProcess()
    manager._active_session_id = "session-1"
    commands: list[dict] = []

    monkeypatch.setattr(manager, "_send_command", lambda payload: commands.append(payload))

    manager.present_focus_context(
        {
            "state": {
                "active_window": _make_window().model_dump(mode="json"),
                "focused_element": _make_element().model_dump(mode="json"),
            },
            "focus_summary": "当前窗口: Chrome · 编辑商品\n当前焦点: 保存",
            "node_suggestion": {
                "step_type": "click",
                "description": "点击保存按钮",
                "target": {"strategy": "accessibility_id"},
            },
            "recording_guidance": "点击后检查成功提示。",
        }
    )

    assert manager._interactive is True
    assert any(command.get("type") == "state" for command in commands)
    assert {"type": "mode", "interactive": True} in commands
    assistant_messages = [command for command in commands if command.get("type") == "append_message"]
    assert any("已抓取当前焦点上下文" in command.get("content", "") for command in assistant_messages)


def test_push_state_includes_hotkey_hints(monkeypatch):
    manager = RecordingHUDManager()
    manager._process = _FakeProcess()
    manager._active_session_id = "session-1"
    manager._session_name = "HUD Session"
    commands: list[dict] = []

    monkeypatch.setattr(manager, "_send_command", lambda payload: commands.append(payload))

    manager._push_state()

    state_commands = [command for command in commands if command.get("type") == "state"]
    assert state_commands
    hotkeys = state_commands[0]["payload"]["hotkeys"]
    assert hotkeys["capture_focus_context"] == "Ctrl+Shift+I"
    assert hotkeys["start_recording"] == "Ctrl+Shift+R"
    assert hotkeys["pause_resume_recording"] == "Ctrl+Shift+P"
    assert hotkeys["stop_recording"] == "Ctrl+Shift+S"
    assert hotkeys["toggle_hud_interaction"] == "Ctrl+Shift+U"