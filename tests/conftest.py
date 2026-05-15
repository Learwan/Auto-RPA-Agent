import pytest


@pytest.fixture
def sample_mouse_click_ops():
    from src.models.operation import (
        MouseAction,
        MouseButton,
        MouseEventData,
        OperationContext,
        OperationEvent,
        OperationType,
    )

    ops = []
    for i in range(10):
        ops.append(
            OperationEvent(
                id=f"op-{i}",
                session_id="test-session",
                seq_num=i,
                timestamp=1000 + i * 500,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(
                    x=100 + i * 10,
                    y=200 + i * 5,
                    button=MouseButton.LEFT,
                    action=MouseAction.CLICK,
                ),
                context=OperationContext(platform="desktop"),
            )
        )
    return ops


@pytest.fixture
def sample_repeating_ops():
    from src.models.operation import (
        KeyAction,
        KeyboardEventData,
        MouseAction,
        MouseButton,
        MouseEventData,
        OperationContext,
        OperationEvent,
        OperationType,
    )

    ops = []
    base_ts = 1000
    for cycle in range(3):
        offset = cycle * 5000
        ops.append(
            OperationEvent(
                id=f"click-{cycle}-0",
                session_id="test-session",
                seq_num=len(ops),
                timestamp=base_ts + offset,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=100, y=200, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(platform="desktop"),
            )
        )
        ops.append(
            OperationEvent(
                id=f"type-{cycle}-1",
                session_id="test-session",
                seq_num=len(ops),
                timestamp=base_ts + offset + 500,
                type=OperationType.KEY_INPUT,
                data=KeyboardEventData(key="text_input", action=KeyAction.INPUT, text="hello"),
                context=OperationContext(platform="desktop"),
            )
        )
        ops.append(
            OperationEvent(
                id=f"click-{cycle}-2",
                session_id="test-session",
                seq_num=len(ops),
                timestamp=base_ts + offset + 1500,
                type=OperationType.MOUSE_CLICK,
                data=MouseEventData(x=300, y=400, button=MouseButton.LEFT, action=MouseAction.CLICK),
                context=OperationContext(platform="desktop"),
            )
        )
    return ops


@pytest.fixture
def sample_mixed_ops():
    from src.models.desktop import Rect
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
        WindowInfo,
    )

    ops = []
    ops.append(
        OperationEvent(
            id="op-0",
            session_id="test-session",
            seq_num=0,
            timestamp=1000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=100, y=200, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(platform="desktop"),
        )
    )
    ops.append(
        OperationEvent(
            id="op-1",
            session_id="test-session",
            seq_num=1,
            timestamp=2000,
            type=OperationType.KEY_INPUT,
            data=KeyboardEventData(key="text_input", action=KeyAction.INPUT, text="test"),
            context=OperationContext(platform="desktop"),
        )
    )
    ops.append(
        OperationEvent(
            id="op-2",
            session_id="test-session",
            seq_num=2,
            timestamp=3000,
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
        )
    )
    ops.append(
        OperationEvent(
            id="op-3",
            session_id="test-session",
            seq_num=3,
            timestamp=4000,
            type=OperationType.MOUSE_CLICK,
            data=MouseEventData(x=500, y=600, button=MouseButton.LEFT, action=MouseAction.CLICK),
            context=OperationContext(platform="desktop"),
        )
    )
    return ops
