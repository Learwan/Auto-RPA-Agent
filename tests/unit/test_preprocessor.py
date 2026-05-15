from src.analyzer.preprocessor import OperationPreprocessor
from src.models.desktop import Rect, WindowInfo
from src.models.operation import (
    ClipboardEventData,
    ClipboardType,
    FileEventData,
    FileOperation,
    KeyAction,
    KeyboardEventData,
    MouseAction,
    MouseButton,
    MouseEventData,
    NavigationEventData,
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)


def _make_click_op(
    seq: int, ts: int, x: int = 100, y: int = 200, action: MouseAction = MouseAction.CLICK
) -> OperationEvent:
    return OperationEvent(
        id=f"op-{seq}",
        session_id="s1",
        seq_num=seq,
        timestamp=ts,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=x, y=y, button=MouseButton.LEFT, action=action),
        context=OperationContext(platform="desktop"),
    )


def _make_key_op(
    seq: int,
    ts: int,
    key: str = "a",
    action: KeyAction = KeyAction.INPUT,
    text: str | None = None,
    modifiers: list[str] | None = None,
) -> OperationEvent:
    return OperationEvent(
        id=f"op-{seq}",
        session_id="s1",
        seq_num=seq,
        timestamp=ts,
        type=OperationType.KEY_INPUT,
        data=KeyboardEventData(key=key, action=action, text=text, modifiers=modifiers or []),
        context=OperationContext(platform="desktop"),
    )


class TestOperationPreprocessorEmpty:
    def test_empty_input_returns_empty(self):
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess([])
        assert result == []


class TestOperationPreprocessorFiltering:
    def test_filters_mouse_move_and_press_release(self):
        ops = [
            _make_click_op(0, 1000, action=MouseAction.PRESS),
            _make_click_op(1, 1100, action=MouseAction.RELEASE),
            _make_click_op(2, 1200, action=MouseAction.CLICK),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert all(r.op_type != "mouse_press" for r in result)
        assert all(r.op_type != "mouse_release" for r in result)
        assert any(r.op_type == "mouse_click" for r in result)

    def test_filters_duplicate_clicks_within_debounce(self):
        ops = [
            _make_click_op(0, 1000, x=100, y=200),
            _make_click_op(1, 1050, x=102, y=201),
            _make_click_op(2, 2000, x=100, y=200),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        click_ops = [r for r in result if r.op_type == "mouse_click"]
        assert len(click_ops) <= 2

    def test_keeps_scroll_events(self):
        ops = [
            OperationEvent(
                id="op-0",
                session_id="s1",
                seq_num=0,
                timestamp=1000,
                type=OperationType.MOUSE_SCROLL,
                data=MouseEventData(x=100, y=200, button=MouseButton.LEFT, action=MouseAction.SCROLL_DOWN, scroll_dy=3),
                context=OperationContext(platform="desktop"),
            ),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert any(r.op_type == "mouse_scroll" for r in result)

    def test_filters_key_release_events(self):
        ops = [
            _make_key_op(0, 1000, key="a", action=KeyAction.PRESS),
            _make_key_op(1, 1100, key="a", action=KeyAction.RELEASE),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert all(r.op_type != "key_release" for r in result)


class TestOperationPreprocessorMerging:
    def test_merges_adjacent_text_inputs(self):
        ops = [
            _make_key_op(0, 1000, key="h", action=KeyAction.INPUT, text="h"),
            _make_key_op(1, 1100, key="e", action=KeyAction.INPUT, text="e"),
            _make_key_op(2, 1200, key="l", action=KeyAction.INPUT, text="l"),
            _make_key_op(3, 1300, key="l", action=KeyAction.INPUT, text="l"),
            _make_key_op(4, 1400, key="o", action=KeyAction.INPUT, text="o"),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        type_ops = [r for r in result if r.op_type == "type_text"]
        assert len(type_ops) == 1
        assert type_ops[0].data["text"] == "hello"

    def test_does_not_merge_modifier_keys_into_text(self):
        ops = [
            _make_key_op(0, 1000, key="c", action=KeyAction.INPUT, text="c", modifiers=["ctrl"]),
            _make_key_op(1, 1100, key="v", action=KeyAction.INPUT, text="v", modifiers=["ctrl"]),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        hotkey_ops = [r for r in result if r.op_type == "hotkey"]
        assert len(hotkey_ops) >= 1

    def test_merges_adjacent_scroll_events(self):
        ops = [
            OperationEvent(
                id="op-0",
                session_id="s1",
                seq_num=0,
                timestamp=1000,
                type=OperationType.MOUSE_SCROLL,
                data=MouseEventData(x=100, y=200, button=MouseButton.LEFT, action=MouseAction.SCROLL_DOWN, scroll_dy=3),
                context=OperationContext(platform="desktop"),
            ),
            OperationEvent(
                id="op-1",
                session_id="s1",
                seq_num=1,
                timestamp=1200,
                type=OperationType.MOUSE_SCROLL,
                data=MouseEventData(x=105, y=205, button=MouseButton.LEFT, action=MouseAction.SCROLL_DOWN, scroll_dy=2),
                context=OperationContext(platform="desktop"),
            ),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        scroll_ops = [r for r in result if r.op_type == "mouse_scroll"]
        assert len(scroll_ops) == 1
        assert scroll_ops[0].data["scroll_dy"] == 5


class TestOperationPreprocessorNormalization:
    def test_normalizes_click_to_step_type(self):
        ops = [_make_click_op(0, 1000)]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert len(result) == 1
        assert result[0].op_type == "mouse_click"
        assert result[0].data["x"] == 100
        assert result[0].data["y"] == 200

    def test_normalizes_window_switch(self):
        ops = [
            OperationEvent(
                id="op-0",
                session_id="s1",
                seq_num=0,
                timestamp=1000,
                type=OperationType.WINDOW_SWITCH,
                data=WindowEventData(
                    to_window=WindowInfo(
                        window_id=1,
                        title="Chrome",
                        app_name="Google Chrome",
                        pid=1234,
                        bounds=Rect(x=0, y=0, width=1920, height=1080),
                    ),
                ),
                context=OperationContext(platform="desktop"),
            ),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert len(result) == 1
        assert result[0].op_type == "switch_window"
        assert result[0].data["app_name"] == "Google Chrome"

    def test_normalizes_navigation(self):
        ops = [
            OperationEvent(
                id="op-0",
                session_id="s1",
                seq_num=0,
                timestamp=1000,
                type=OperationType.NAVIGATION,
                data=NavigationEventData(url="https://example.com", title="Example"),
                context=OperationContext(platform="web"),
            ),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert len(result) == 1
        assert result[0].op_type == "navigation"
        assert result[0].data["url"] == "https://example.com"

    def test_normalizes_clipboard(self):
        ops = [
            OperationEvent(
                id="op-0",
                session_id="s1",
                seq_num=0,
                timestamp=1000,
                type=OperationType.CLIPBOARD,
                data=ClipboardEventData(content_type=ClipboardType.TEXT, content_preview="hello"),
                context=OperationContext(platform="desktop"),
            ),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert len(result) == 1
        assert result[0].op_type == "clipboard"

    def test_normalizes_file_upload(self):
        ops = [
            OperationEvent(
                id="op-0",
                session_id="s1",
                seq_num=0,
                timestamp=1000,
                type=OperationType.FILE_OP,
                data=FileEventData(operation=FileOperation.UPLOAD, src_path="/tmp/file.txt"),
                context=OperationContext(platform="desktop"),
            ),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        assert len(result) == 1
        assert result[0].op_type == "upload_file"

    def test_operations_sorted_by_timestamp(self):
        ops = [
            _make_click_op(0, 3000),
            _make_click_op(1, 1000),
            _make_click_op(2, 2000),
        ]
        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)
        timestamps = [r.timestamp for r in result]
        assert timestamps == sorted(timestamps)

    def test_backfills_missing_active_window_from_previous_switch(self):
        target_window = WindowInfo(
            window_id=1,
            title="企业微信",
            app_name="企业微信",
            pid=8107,
            bounds=Rect(x=0, y=0, width=1600, height=900),
        )
        ops = [
            OperationEvent(
                id="op-switch",
                session_id="s1",
                seq_num=0,
                timestamp=1000,
                type=OperationType.WINDOW_SWITCH,
                data=WindowEventData(to_window=target_window),
                context=OperationContext(
                    platform="desktop",
                    active_window=target_window,
                    process_name="企业微信",
                    process_pid=8107,
                ),
            ),
            OperationEvent(
                id="op-click",
                session_id="s1",
                seq_num=1,
                timestamp=1200,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=300, y=400, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(platform="desktop"),
            ),
        ]

        preprocessor = OperationPreprocessor()
        result = preprocessor.preprocess(ops)

        assert len(result) == 2
        assert result[1].context["active_window"]["title"] == "企业微信"
        assert result[1].context["process_name"] == "企业微信"
        assert result[1].context["active_window_inferred"] is True
