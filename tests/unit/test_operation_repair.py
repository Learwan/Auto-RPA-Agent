from types import SimpleNamespace

from src.analyzer.operation_repair import OperationRepairService
from src.executor.multimodal_locator import OCRResult
from src.models.desktop import Rect, UIElement, WindowInfo
from src.models.operation import MouseAction, MouseButton, MouseEventData, OperationContext, OperationEvent, OperationType
from src.models.vision import ScreenshotAnalysis, UIElement as VisionUIElement


class _FakeSnapshotManager:
    def __init__(self):
        self.snapshots = [
            SimpleNamespace(
                id="snap-repair-1",
                timestamp=1001,
                session_id="session-1",
                state=SimpleNamespace(
                    active_window=SimpleNamespace(title="企业微信", app_name="企业微信"),
                    focused_element=SimpleNamespace(role="button"),
                ),
            )
        ]

    def get_screenshot_bytes(self, snapshot_id: str) -> bytes | None:
        assert snapshot_id == "snap-repair-1"
        return b"fake-png-bytes"

    def list_snapshots(self, session_id: str | None = None, limit: int = 50):
        assert session_id == "session-1"
        return self.snapshots[:limit]


class _FakeVisionService:
    def __init__(self):
        self.calls = 0

    async def analyze_screenshot(self, screenshot_base64, task_description=None):
        self.calls += 1
        assert screenshot_base64
        assert task_description
        return ScreenshotAnalysis(
            app_name="企业微信",
            window_title="企业微信",
            ui_state_description="聊天窗口已经打开。",
            actionable_elements=[
                VisionUIElement(
                    element_type="button",
                    label="button",
                    description="发送消息按钮",
                    bbox=(300, 400, 120, 48),
                    confidence=0.88,
                    actionable=True,
                    suggested_action="click",
                )
            ],
            user_intent="发送消息",
            suggested_next_actions=["点击发送"],
            confidence=0.91,
            thinking_trace="aligned candidate",
        )


class _FakeOCREngine:
    def __init__(self):
        self.calls = 0

    async def detect_text(self, screenshot_bytes):
        self.calls += 1
        assert screenshot_bytes == b"fake-png-bytes"
        return [
            OCRResult(
                text="发送",
                center_x=340,
                center_y=420,
                confidence=0.96,
                bbox=[[302, 402], [418, 402], [418, 446], [302, 446]],
            )
        ]


async def test_repair_operations_salvages_weak_click_with_vision_and_ocr():
    service = OperationRepairService(
        snapshot_manager=_FakeSnapshotManager(),
        vision_service=_FakeVisionService(),
        ocr_engine=_FakeOCREngine(),
    )
    operation = OperationEvent(
        id="op-repair-1",
        session_id="session-1",
        seq_num=1,
        timestamp=1000,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=342, y=421, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(
            platform="macos",
            screenshot_path="snapshot://snap-repair-1",
            active_window=WindowInfo(
                window_id="window-1",
                title="企业微信",
                app_name="企业微信",
                pid=1,
                bounds=Rect(x=0, y=0, width=1440, height=900),
                is_active=True,
            ),
            focused_element=UIElement(role="AXStaticText", functional_label="文本", is_focused=True),
        ),
    )

    repaired = await service.repair_operations([operation])

    assert len(repaired) == 1
    repaired_op = repaired[0]
    assert repaired_op.context is not None
    assert repaired_op.context.node_suggestion is not None
    assert repaired_op.context.node_suggestion["target"]["strategy"] == "text_match"
    assert repaired_op.context.node_suggestion["target"]["text_contains"] == "发送"
    assert repaired_op.context.node_suggestion["ocr_text"] == "发送"
    assert "OCR" in repaired_op.context.node_suggestion["confidence_reason"]
    assert repaired_op.context.focused_element is not None
    assert repaired_op.context.focused_element.title == "发送"
    assert repaired_op.context.focused_element.value == "发送"


async def test_repair_operations_skips_already_strong_locator():
    vision = _FakeVisionService()
    ocr = _FakeOCREngine()
    service = OperationRepairService(
        snapshot_manager=_FakeSnapshotManager(),
        vision_service=vision,
        ocr_engine=ocr,
    )
    operation = OperationEvent(
        id="op-repair-strong",
        session_id="session-1",
        seq_num=1,
        timestamp=1000,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=342, y=421, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(
            platform="macos",
            screenshot_path="snapshot://snap-repair-1",
            active_window=WindowInfo(
                window_id="window-1",
                title="企业微信",
                app_name="企业微信",
                pid=1,
                bounds=Rect(x=0, y=0, width=1440, height=900),
                is_active=True,
            ),
            focused_element=UIElement(role="AXButton", identifier="send-button", title="发送", is_focused=True),
        ),
    )

    repaired = await service.repair_operations([operation])

    assert repaired[0] == operation
    assert vision.calls == 0
    assert ocr.calls == 0


async def test_repair_operations_matches_nearest_session_snapshot_without_inline_reference():
    service = OperationRepairService(
        snapshot_manager=_FakeSnapshotManager(),
        vision_service=_FakeVisionService(),
        ocr_engine=_FakeOCREngine(),
    )
    operation = OperationEvent(
        id="op-repair-nearest",
        session_id="session-1",
        seq_num=1,
        timestamp=1000,
        type=OperationType.MOUSE_CLICK,
        data=MouseEventData(x=342, y=421, button=MouseButton.LEFT, action=MouseAction.CLICK),
        context=OperationContext(
            platform="macos",
            active_window=WindowInfo(
                window_id="window-1",
                title="企业微信",
                app_name="企业微信",
                pid=1,
                bounds=Rect(x=0, y=0, width=1440, height=900),
                is_active=True,
            ),
        ),
    )

    repaired = await service.repair_operations([operation])

    assert repaired[0].context is not None
    assert repaired[0].context.screenshot_path == "snapshot://snap-repair-1"
    assert repaired[0].context.node_suggestion is not None
    assert repaired[0].context.node_suggestion["snapshot_id"] == "snap-repair-1"
