import asyncio

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.models.hotkey import UserSettings
from src.models.desktop import Rect, UIElement, WindowInfo
from src.models.operation import MouseAction, MouseButton, MouseEventData, OperationContext, OperationEvent, OperationType
from src.models.operation import KeyAction, KeyboardEventData
from src.models.operation import WindowEventData
from src.models.session import Session, SessionStatus
from src.recorder.session_manager import SessionManager


class _FakeRecorder:
    def __init__(self, call_order: list[str] | None = None):
        self.started_with = None
        self.call_order = call_order

    def start(self, session_id, callback):
        if self.call_order is not None:
            self.call_order.append("recorders")
        self.started_with = (session_id, callback)

    def stop(self):
        return None


class _FakeHUDManager:
    def __init__(self, call_order: list[str] | None = None):
        self.loop = None
        self.started = []
        self.updated = []
        self.toggled = []
        self.operations = []
        self.focus_contexts = []
        self.focus_context_request_handler = None
        self.call_order = call_order

    def set_loop(self, loop):
        self.loop = loop

    def set_focus_context_request_handler(self, handler):
        self.focus_context_request_handler = handler

    def start_session(self, session_id: str, session_name: str, hud_ai_assist: bool = False):
        if self.call_order is not None:
            self.call_order.append("hud")
        self.started.append(
            {
                "session_id": session_id,
                "session_name": session_name,
                "hud_ai_assist": hud_ai_assist,
            }
        )

    def update_session_status(self, session_id: str, status: str, operation_count: int = 0):
        self.updated.append((session_id, status, operation_count))

    def toggle_interaction(self, session_id: str | None = None):
        self.toggled.append(session_id)

    def handle_operation(self, event: OperationEvent, operation_count: int):
        self.operations.append((event.id, operation_count))

    def present_focus_context(self, capture: dict):
        self.focus_contexts.append(capture)


class _FakeHotkeyManager:
    def __init__(self, call_order: list[str] | None = None):
        self.start_calls = []
        self.call_order = call_order

    def start(self, settings, **callbacks):
        if self.call_order is not None:
            self.call_order.append("hotkeys")
        self.start_calls.append({"settings": settings, **callbacks})


class _FakeFocusContextService:
    def __init__(self, payload: dict | None = None):
        self.calls = []
        self.payload = payload or {
            "session_id": "session-default",
            "snapshot_id": "snap-focus-1",
            "focus_summary": "当前窗口: Chrome · 编辑商品",
        }

    async def capture(
        self,
        session_id: str,
        include_vision: bool = True,
        include_ocr: bool = True,
        include_llm: bool = True,
    ):
        self.calls.append(
            {
                "session_id": session_id,
                "include_vision": include_vision,
                "include_ocr": include_ocr,
                "include_llm": include_llm,
            }
        )
        payload = dict(self.payload)
        payload["session_id"] = session_id
        return payload


class _FakeSnapshotManager:
    def __init__(self):
        self.start_calls = []
        self.stop_calls = []

    def start_periodic_capture(self, interval: float = 5.0, session_id: str | None = None):
        self.start_calls.append((session_id, interval))

    def stop_periodic_capture(self, session_id: str | None = None):
        self.stop_calls.append(session_id)


class _FakeRepository:
    updates = []
    saved_operations = []

    def __init__(self, _db_session):
        pass

    async def update_session_status(self, session_id: str, status: str, **kwargs):
        self.updates.append((session_id, status, kwargs))

    async def add_operation(self, event: OperationEvent):
        self.saved_operations.append(event)


@asynccontextmanager
async def _fake_factory():
    yield object()


@pytest.mark.asyncio
async def test_start_session_wires_hud_and_hotkey_toggle(monkeypatch):
    _FakeRepository.updates = []
    call_order = []
    recorder = _FakeRecorder(call_order)
    hud_manager = _FakeHUDManager(call_order)
    hotkey_manager = _FakeHotkeyManager(call_order)
    snapshot_manager = _FakeSnapshotManager()
    manager = SessionManager(recorders_factory=lambda: [recorder])
    manager._hud_manager = hud_manager
    manager._hotkey_manager = hotkey_manager
    manager._snapshot_manager = snapshot_manager
    manager._audio = SimpleNamespace(play_start=lambda: None)
    manager._notifier = SimpleNamespace(notify_recording_started=lambda _name: None)
    manager._settings_store = SimpleNamespace(get_settings=lambda: {"hotkeys": "ok"})
    manager._sessions["session-hud-1"] = Session(id="session-hud-1", name="HUD Session")
    manager._event_subscribers["session-hud-1"] = []
    manager._operation_counts["session-hud-1"] = 0

    monkeypatch.setattr("src.recorder.session_manager.get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr("src.recorder.session_manager.Repository", _FakeRepository)

    session = await manager.start_session(
        "session-hud-1",
        mode="desktop",
        options={"hud": True, "hud_ai_assist": True},
    )

    assert session.status == SessionStatus.RECORDING
    assert recorder.started_with is not None
    assert hud_manager.loop is not None
    assert hud_manager.started == [
        {
            "session_id": "session-hud-1",
            "session_name": "HUD Session",
            "hud_ai_assist": True,
        }
    ]
    assert hud_manager.updated == [
        ("session-hud-1", SessionStatus.RECORDING.value, 0),
    ]
    assert callable(hud_manager.focus_context_request_handler)
    assert _FakeRepository.updates[0][0] == "session-hud-1"
    assert _FakeRepository.updates[0][1] == SessionStatus.RECORDING.value
    assert hotkey_manager.start_calls
    assert "on_capture_focus" in hotkey_manager.start_calls[0]
    assert snapshot_manager.start_calls == [("session-hud-1", 0.5)]
    assert call_order[:3] == ["hud", "hotkeys", "recorders"]

    toggle_callback = hotkey_manager.start_calls[0]["on_toggle_hud"]
    toggle_callback()

    assert hud_manager.toggled == ["session-hud-1"]

    manager._schedule_coro = lambda coro: coro.close()
    manager._on_operation(
        OperationEvent(
            id="op-hud-1",
            session_id="session-hud-1",
            seq_num=1,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=120, y=220, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(platform="macos"),
        )
    )

    assert hud_manager.operations == [("op-hud-1", 1)]


@pytest.mark.asyncio
async def test_session_lifecycle_manages_periodic_capture(monkeypatch):
    _FakeRepository.updates = []
    snapshot_manager = _FakeSnapshotManager()
    recorder = _FakeRecorder()
    manager = SessionManager(recorders_factory=lambda: [recorder])
    manager._snapshot_manager = snapshot_manager
    manager._sessions["session-hud-life"] = Session(id="session-hud-life", name="HUD Lifecycle")
    manager._event_subscribers["session-hud-life"] = []
    manager._operation_counts["session-hud-life"] = 0
    monkeypatch.setattr("src.recorder.session_manager.get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr("src.recorder.session_manager.Repository", _FakeRepository)

    await manager.start_session("session-hud-life", mode="desktop", options={"hud": False})
    await manager.pause_session("session-hud-life")
    await manager.resume_session("session-hud-life")
    await manager.stop_session("session-hud-life")

    assert snapshot_manager.start_calls == [
        ("session-hud-life", 0.5),
        ("session-hud-life", 0.5),
    ]
    assert snapshot_manager.stop_calls == ["session-hud-life", "session-hud-life"]


def test_on_operation_uses_session_level_sequence(monkeypatch):
    manager = SessionManager(recorders_factory=lambda: [])
    manager._sessions["session-hud-seq"] = Session(
        id="session-hud-seq",
        name="HUD Sequence Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-seq"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-seq"] = []
    manager._operation_counts["session-hud-seq"] = 0
    manager._schedule_coro = lambda coro: coro.close()
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)

    event1 = OperationEvent(
        id="op-seq-1",
        session_id="session-hud-seq",
        seq_num=1,
        timestamp=1000,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=10, y=10, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(platform="macos"),
    )
    event2 = OperationEvent(
        id="op-seq-2",
        session_id="session-hud-seq",
        seq_num=1,
        timestamp=1001,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=20, y=20, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(platform="macos"),
    )

    manager._on_operation(event1)
    manager._on_operation(event2)

    assert event1.seq_num == 1
    assert event2.seq_num == 2
    assert manager._sessions["session-hud-seq"].operation_count == 2


@pytest.mark.asyncio
async def test_capture_focus_context_uses_service_and_pushes_to_hud():
    hud_manager = _FakeHUDManager()
    focus_service = _FakeFocusContextService()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._hud_manager = hud_manager
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-focus"] = Session(
        id="session-hud-focus",
        name="HUD Focus Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-focus"] = [SimpleNamespace()]
    manager._session_options["session-hud-focus"] = {"hud": True}

    capture = await manager.capture_focus_context(
        "session-hud-focus",
        include_vision=False,
        include_llm=False,
    )

    assert capture["snapshot_id"] == "snap-focus-1"
    assert focus_service.calls == [
        {
            "session_id": "session-hud-focus",
            "include_vision": False,
            "include_ocr": True,
            "include_llm": False,
        }
    ]
    assert hud_manager.focus_contexts == [capture]


@pytest.mark.asyncio
async def test_on_operation_auto_captures_lightweight_focus_context_for_window_switch(monkeypatch):
    focus_service = _FakeFocusContextService()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-auto-focus"] = Session(
        id="session-hud-auto-focus",
        name="HUD Auto Focus Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-auto-focus"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-auto-focus"] = []
    manager._operation_counts["session-hud-auto-focus"] = 0
    manager._session_options["session-hud-auto-focus"] = {"hud": False}
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "_persist_event", _async_noop)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_ENABLED", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS", 10)

    manager._on_operation(
        OperationEvent(
            id="op-auto-focus-1",
            session_id="session-hud-auto-focus",
            seq_num=1,
            timestamp=1000,
            type=OperationType.WINDOW_SWITCH,
            data=WindowEventData(
                to_window=WindowInfo(
                    window_id="window-1",
                    title="编辑商品",
                    app_name="Chrome",
                    pid=10,
                    bounds=Rect(x=0, y=0, width=1440, height=900),
                    is_active=True,
                )
            ),
            context=OperationContext(platform="macos"),
        )
    )

    await asyncio.sleep(0.05)

    assert focus_service.calls == [
        {
            "session_id": "session-hud-auto-focus",
            "include_vision": False,
            "include_ocr": False,
            "include_llm": False,
        }
    ]


@pytest.mark.asyncio
async def test_on_operation_skips_auto_focus_context_for_well_formed_click(monkeypatch):
    focus_service = _FakeFocusContextService()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-auto-skip"] = Session(
        id="session-hud-auto-skip",
        name="HUD Auto Focus Skip Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-auto-skip"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-auto-skip"] = []
    manager._operation_counts["session-hud-auto-skip"] = 0
    manager._session_options["session-hud-auto-skip"] = {"hud": False}
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "_persist_event", _async_noop)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_ENABLED", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS", 10)

    manager._on_operation(
        OperationEvent(
            id="op-auto-focus-2",
            session_id="session-hud-auto-skip",
            seq_num=1,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=120, y=220, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                platform="macos",
                active_window=WindowInfo(
                    window_id="window-2",
                    title="编辑商品",
                    app_name="Chrome",
                    pid=10,
                    bounds=Rect(x=0, y=0, width=1440, height=900),
                    is_active=True,
                ),
                focused_element=UIElement(role="button", title="保存", is_focused=True),
            ),
        )
    )

    await asyncio.sleep(0.05)

    assert focus_service.calls == []


@pytest.mark.asyncio
async def test_on_operation_skips_auto_focus_context_for_sensitive_key_input(monkeypatch):
    focus_service = _FakeFocusContextService()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-auto-sensitive"] = Session(
        id="session-hud-auto-sensitive",
        name="HUD Auto Focus Sensitive Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-auto-sensitive"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-auto-sensitive"] = []
    manager._operation_counts["session-hud-auto-sensitive"] = 0
    manager._session_options["session-hud-auto-sensitive"] = {"hud": False}
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "_persist_event", _async_noop)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_ENABLED", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS", 10)

    manager._on_operation(
        OperationEvent(
            id="op-auto-focus-sensitive-1",
            session_id="session-hud-auto-sensitive",
            seq_num=1,
            timestamp=1000,
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(
                key="p",
                action=KeyAction.INPUT,
                text="***",
                is_sensitive=True,
            ),
            context=OperationContext(platform="macos"),
        )
    )

    await asyncio.sleep(0.05)

    assert focus_service.calls == []


@pytest.mark.asyncio
async def test_on_operation_throttles_auto_focus_context(monkeypatch):
    focus_service = _FakeFocusContextService()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-auto-throttle"] = Session(
        id="session-hud-auto-throttle",
        name="HUD Auto Focus Throttle Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-auto-throttle"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-auto-throttle"] = []
    manager._operation_counts["session-hud-auto-throttle"] = 0
    manager._session_options["session-hud-auto-throttle"] = {"hud": False}
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "_persist_event", _async_noop)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_ENABLED", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS", 1000)

    first_event = OperationEvent(
        id="op-auto-focus-3",
        session_id="session-hud-auto-throttle",
        seq_num=1,
        timestamp=1000,
        type=OperationType.WINDOW_SWITCH,
        data=WindowEventData(
            to_window=WindowInfo(
                window_id="window-3",
                title="编辑商品",
                app_name="Chrome",
                pid=10,
                bounds=Rect(x=0, y=0, width=1440, height=900),
                is_active=True,
            )
        ),
        context=OperationContext(platform="macos"),
    )
    second_event = first_event.model_copy(update={"id": "op-auto-focus-4", "seq_num": 2, "timestamp": 1100})

    manager._on_operation(first_event)
    await asyncio.sleep(0.05)
    manager._on_operation(second_event)
    await asyncio.sleep(0.05)

    assert focus_service.calls == [
        {
            "session_id": "session-hud-auto-throttle",
            "include_vision": False,
            "include_ocr": False,
            "include_llm": False,
        }
    ]


@pytest.mark.asyncio
async def test_on_operation_auto_captures_focus_context_for_weak_click(monkeypatch):
    focus_service = _FakeFocusContextService()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-auto-weak"] = Session(
        id="session-hud-auto-weak",
        name="HUD Auto Weak Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-auto-weak"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-auto-weak"] = []
    manager._operation_counts["session-hud-auto-weak"] = 0
    manager._session_options["session-hud-auto-weak"] = {"hud": False}
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "_persist_event", _async_noop)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_ENABLED", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS", 10)

    manager._on_operation(
        OperationEvent(
            id="op-auto-focus-weak-1",
            session_id="session-hud-auto-weak",
            seq_num=1,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=120, y=220, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(
                platform="macos",
                active_window=WindowInfo(
                    window_id="window-weak-1",
                    title="编辑商品",
                    app_name="Chrome",
                    pid=10,
                    bounds=Rect(x=0, y=0, width=1440, height=900),
                    is_active=True,
                ),
                focused_element=UIElement(role="button", is_focused=True),
            ),
        )
    )

    await asyncio.sleep(0.05)

    assert focus_service.calls == [
        {
            "session_id": "session-hud-auto-weak",
            "include_vision": False,
            "include_ocr": False,
            "include_llm": False,
        }
    ]


@pytest.mark.asyncio
async def test_persist_event_enriches_weak_operation_with_focus_capture(monkeypatch):
    _FakeRepository.saved_operations = []
    focus_service = _FakeFocusContextService(
        payload={
            "session_id": "session-hud-enrich",
            "snapshot_id": "snap-enrich-1",
            "timestamp": 1001,
            "state": {
                "active_window": {
                    "window_id": "window-enrich-1",
                    "title": "编辑商品",
                    "app_name": "Google Chrome",
                    "pid": 10,
                    "bounds": {"x": 0, "y": 0, "width": 1440, "height": 900},
                    "is_active": True,
                    "is_visible": True,
                    "url": "https://example.com/editor",
                    "browser_type": "chromium",
                },
            },
            "focused_details": {
                "label": "保存",
                "role": "button",
                "identifier": "save-button",
                "description": "保存当前记录",
                "functional_label": "保存按钮",
                "class_name": "AXButton",
                "is_enabled": True,
                "is_focused": True,
            },
            "node_suggestion": {
                "target": {
                    "strategy": "accessibility_id",
                    "accessibility_id": "save-button",
                    "title": "保存",
                    "role": "button",
                    "class_name": "AXButton",
                    "bounds": {"x": 120, "y": 220, "width": 80, "height": 32},
                    "url": "https://example.com/editor",
                }
            },
        }
    )
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-enrich"] = Session(
        id="session-hud-enrich",
        name="HUD Enrich Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-enrich"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-enrich"] = []
    manager._operation_counts["session-hud-enrich"] = 0
    manager._session_options["session-hud-enrich"] = {"hud": False}

    monkeypatch.setattr("src.recorder.session_manager.get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr("src.recorder.session_manager.Repository", _FakeRepository)

    await manager._persist_event(
        OperationEvent(
            id="op-enrich-1",
            session_id="session-hud-enrich",
            seq_num=1,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=120, y=220, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(platform="macos"),
        )
    )

    assert focus_service.calls == [
        {
            "session_id": "session-hud-enrich",
            "include_vision": True,
            "include_ocr": True,
            "include_llm": False,
        }
    ]
    saved_event = _FakeRepository.saved_operations[0]
    assert saved_event.context is not None
    assert saved_event.context.screenshot_path == "snapshot://snap-enrich-1"
    assert saved_event.context.active_window is not None
    assert saved_event.context.active_window.title == "编辑商品"
    assert saved_event.context.focused_element is not None
    assert saved_event.context.focused_element.identifier == "save-button"
    assert saved_event.context.focused_element.title == "保存"
    assert saved_event.context.node_suggestion is not None
    assert saved_event.context.node_suggestion["snapshot_id"] == "snap-enrich-1"
    assert saved_event.context.node_suggestion["target"]["strategy"] == "accessibility_id"


@pytest.mark.asyncio
async def test_persist_event_refreshes_recent_non_vision_capture_for_weak_operation(monkeypatch):
    _FakeRepository.saved_operations = []
    focus_service = _FakeFocusContextService(
        payload={
            "session_id": "session-hud-refresh",
            "snapshot_id": "snap-refresh-2",
            "timestamp": 1001,
            "state": {
                "active_window": {
                    "window_id": "window-refresh-1",
                    "title": "编辑商品",
                    "app_name": "Google Chrome",
                    "pid": 10,
                    "bounds": {"x": 0, "y": 0, "width": 1440, "height": 900},
                    "is_active": True,
                    "is_visible": True,
                    "url": "https://example.com/editor",
                    "browser_type": "chromium",
                },
            },
            "focused_details": {
                "label": "保存",
                "role": "button",
                "identifier": "save-button",
                "description": "保存当前记录",
                "functional_label": "保存按钮",
                "class_name": "AXButton",
                "is_enabled": True,
                "is_focused": True,
            },
            "node_suggestion": {
                "target": {
                    "strategy": "accessibility_id",
                    "accessibility_id": "save-button",
                    "title": "保存",
                    "role": "button",
                    "class_name": "AXButton",
                    "bounds": {"x": 120, "y": 220, "width": 80, "height": 32},
                    "url": "https://example.com/editor",
                }
            },
        }
    )
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-refresh"] = Session(
        id="session-hud-refresh",
        name="HUD Refresh Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-refresh"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-refresh"] = []
    manager._operation_counts["session-hud-refresh"] = 0
    manager._session_options["session-hud-refresh"] = {"hud": False}
    manager._latest_focus_capture["session-hud-refresh"] = {
        "session_id": "session-hud-refresh",
        "snapshot_id": "snap-refresh-1",
        "timestamp": 1000,
        "state": {
            "active_window": {
                "window_id": "window-refresh-1",
                "title": "编辑商品",
                "app_name": "Google Chrome",
                "pid": 10,
            }
        },
    }

    monkeypatch.setattr("src.recorder.session_manager.get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr("src.recorder.session_manager.Repository", _FakeRepository)

    await manager._persist_event(
        OperationEvent(
            id="op-refresh-1",
            session_id="session-hud-refresh",
            seq_num=1,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=120, y=220, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(platform="macos"),
        )
    )

    assert focus_service.calls == [
        {
            "session_id": "session-hud-refresh",
            "include_vision": True,
            "include_ocr": True,
            "include_llm": False,
        }
    ]
    saved_event = _FakeRepository.saved_operations[0]
    assert saved_event.context is not None
    assert saved_event.context.screenshot_path == "snapshot://snap-refresh-2"
    assert saved_event.context.focused_element is not None
    assert saved_event.context.focused_element.identifier == "save-button"
    assert saved_event.context.node_suggestion is not None
    assert saved_event.context.node_suggestion["snapshot_id"] == "snap-refresh-2"


@pytest.mark.asyncio
async def test_persist_event_merges_weak_focus_with_visual_node_suggestion(monkeypatch):
    _FakeRepository.saved_operations = []
    focus_service = _FakeFocusContextService(
        payload={
            "session_id": "session-hud-visual-anchor",
            "snapshot_id": "snap-visual-anchor-1",
            "timestamp": 1001,
            "state": {
                "active_window": {
                    "window_id": "window-visual-anchor-1",
                    "title": "企业微信",
                    "app_name": "企业微信",
                    "pid": 10,
                    "bounds": {"x": 0, "y": 0, "width": 1440, "height": 900},
                    "is_active": True,
                    "is_visible": True,
                },
                "focused_element": {
                    "role": "AXStaticText",
                    "functional_label": "文本",
                    "is_enabled": True,
                    "is_visible": True,
                    "is_focusable": True,
                    "is_focused": True,
                },
            },
            "node_suggestion": {
                "step_type": "click",
                "vision_source": "local_vlm",
                "confidence": 0.82,
                "target": {
                    "strategy": "text_match",
                    "text_contains": "AP80x Arc项目管理沟通群",
                    "role": "AXStaticText",
                    "class_name": "文本",
                    "window_title": "企业微信",
                    "vision_element": {
                        "label": "AP80x Arc项目管理沟通群",
                        "element_type": "text",
                        "confidence": 0.86,
                    },
                },
            },
        }
    )
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-visual-anchor"] = Session(
        id="session-hud-visual-anchor",
        name="HUD Visual Anchor Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-visual-anchor"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-visual-anchor"] = []
    manager._operation_counts["session-hud-visual-anchor"] = 0
    manager._session_options["session-hud-visual-anchor"] = {"hud": False}

    monkeypatch.setattr("src.recorder.session_manager.get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr("src.recorder.session_manager.Repository", _FakeRepository)

    await manager._persist_event(
        OperationEvent(
            id="op-visual-anchor-1",
            session_id="session-hud-visual-anchor",
            seq_num=1,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=320, y=220, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(platform="macos"),
        )
    )

    saved_event = _FakeRepository.saved_operations[0]
    assert saved_event.context is not None
    assert saved_event.context.focused_element is not None
    assert saved_event.context.focused_element.title == "AP80x Arc项目管理沟通群"
    assert saved_event.context.focused_element.value == "AP80x Arc项目管理沟通群"
    assert saved_event.context.node_suggestion is not None
    assert saved_event.context.node_suggestion["vision_source"] == "local_vlm"


@pytest.mark.asyncio
async def test_persist_event_derives_node_suggestion_from_click_aligned_vision_element(monkeypatch):
    _FakeRepository.saved_operations = []
    focus_service = _FakeFocusContextService(
        payload={
            "session_id": "session-hud-vision-click",
            "snapshot_id": "snap-vision-click-1",
            "timestamp": 1001,
            "state": {
                "active_window": {
                    "window_id": "window-vision-click-1",
                    "title": "SOP 自动生成",
                    "app_name": "访达",
                    "pid": 10,
                    "bounds": {"x": 0, "y": 0, "width": 1440, "height": 900},
                    "is_active": True,
                    "is_visible": True,
                }
            },
            "vision": {
                "source": "local_vlm",
                "actionable_elements": [
                    {
                        "element_type": "button",
                        "label": "发送",
                        "description": "发送当前内容",
                        "bbox": [300, 400, 120, 48],
                        "confidence": 0.88,
                        "actionable": True,
                        "suggested_action": "click",
                    }
                ],
            },
        }
    )
    manager = SessionManager(recorders_factory=lambda: [])
    manager._focus_context_service = focus_service
    manager._sessions["session-hud-vision-click"] = Session(
        id="session-hud-vision-click",
        name="HUD Vision Click Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-vision-click"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-vision-click"] = []
    manager._operation_counts["session-hud-vision-click"] = 0
    manager._session_options["session-hud-vision-click"] = {"hud": False}

    monkeypatch.setattr("src.recorder.session_manager.get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr("src.recorder.session_manager.Repository", _FakeRepository)

    await manager._persist_event(
        OperationEvent(
            id="op-vision-click-1",
            session_id="session-hud-vision-click",
            seq_num=1,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=340, y=420, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(platform="macos"),
        )
    )

    saved_event = _FakeRepository.saved_operations[0]
    assert saved_event.context is not None
    assert saved_event.context.focused_element is not None
    assert saved_event.context.focused_element.title == "发送"
    assert saved_event.context.focused_element.value == "发送"
    assert saved_event.context.node_suggestion is not None
    assert saved_event.context.node_suggestion["snapshot_id"] == "snap-vision-click-1"
    assert saved_event.context.node_suggestion["vision_source"] == "local_vlm"
    assert saved_event.context.node_suggestion["target"]["text_contains"] == "发送"


async def _async_noop(*_args, **_kwargs):
    return None


def test_on_operation_toggles_hud_from_recorded_hotkey(monkeypatch):
    hud_manager = _FakeHUDManager()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._hud_manager = hud_manager
    manager._settings_store = SimpleNamespace(get_settings=lambda: UserSettings())
    manager._sessions["session-hud-hotkey"] = Session(
        id="session-hud-hotkey",
        name="HUD Hotkey Session",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-hotkey"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-hotkey"] = []
    manager._operation_counts["session-hud-hotkey"] = 0
    manager._session_options["session-hud-hotkey"] = {"hud": True}
    manager._schedule_coro = lambda coro: coro.close()
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)

    manager._on_operation(
        OperationEvent(
            id="op-hud-hotkey",
            session_id="session-hud-hotkey",
            seq_num=1,
            timestamp=1000,
            type=OperationType.KEY_PRESS,
            data=KeyboardEventData(
                key="u",
                action=KeyAction.HOTKEY,
                modifiers=["Key.ctrl", "Key.shift"],
            ),
            context=OperationContext(platform="macos"),
        )
    )

    assert hud_manager.toggled == ["session-hud-hotkey"]


def test_on_operation_toggles_hud_when_macos_reports_cmd_for_ctrl(monkeypatch):
    hud_manager = _FakeHUDManager()
    manager = SessionManager(recorders_factory=lambda: [])
    manager._hud_manager = hud_manager
    manager._settings_store = SimpleNamespace(get_settings=lambda: UserSettings())
    manager._sessions["session-hud-hotkey-cmd"] = Session(
        id="session-hud-hotkey-cmd",
        name="HUD Hotkey Session CMD",
        status=SessionStatus.RECORDING,
    )
    manager._recorders["session-hud-hotkey-cmd"] = [SimpleNamespace()]
    manager._event_subscribers["session-hud-hotkey-cmd"] = []
    manager._operation_counts["session-hud-hotkey-cmd"] = 0
    manager._session_options["session-hud-hotkey-cmd"] = {"hud": True}
    manager._schedule_coro = lambda coro: coro.close()
    monkeypatch.setattr(manager, "_publish_to_event_bus", lambda *_args, **_kwargs: None)
    manager._on_operation(
        OperationEvent(
            id="op-hud-hotkey-cmd",
            session_id="session-hud-hotkey-cmd",
            seq_num=1,
            timestamp=1000,
            type=OperationType.KEY_PRESS,
            data=KeyboardEventData(
                key="u",
                action=KeyAction.HOTKEY,
                modifiers=["Key.cmd", "Key.shift"],
            ),
            context=OperationContext(platform="macos"),
        )
    )

    assert hud_manager.toggled == ["session-hud-hotkey-cmd"]
