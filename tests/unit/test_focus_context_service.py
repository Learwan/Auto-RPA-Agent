import asyncio

import pytest

import src.collector.focus_context as focus_context_module
from src.collector.focus_context import FocusContextService
from src.collector.snapshot import DesktopSnapshot
from src.executor.multimodal_locator import OCRResult
from src.llm.multimodal_service import VisionError
from src.models.desktop import DesktopState, Rect, UIElement, WindowInfo
from src.models.vision import ScreenshotAnalysis, UIElement as VisionUIElement


class _FakeSnapshotManager:
    def __init__(self):
        self.snapshot = DesktopSnapshot(
            snapshot_id="snap-focus-1",
            session_id="session-1",
            state=DesktopState(
                timestamp=123456789,
                active_window=WindowInfo(
                    window_id=1,
                    title="编辑商品",
                    app_name="Chrome",
                    pid=10,
                    bounds=Rect(x=0, y=0, width=1440, height=900),
                    is_active=True,
                ),
                focused_element=UIElement(
                    role="button",
                    title="保存",
                    identifier="save-btn",
                    bounds=Rect(x=200, y=100, width=88, height=32),
                    is_focused=True,
                ),
            ),
            screenshot_bytes=b"fake-png-bytes",
        )

    async def take_snapshot(self, session_id=None):
        self.snapshot.session_id = session_id
        return self.snapshot

    def get_screenshot_bytes(self, snapshot_id):
        assert snapshot_id == "snap-focus-1"
        return b"fake-png-bytes"


class _FakeVisionService:
    async def analyze_screenshot(self, screenshot_base64, task_description=None):
        assert screenshot_base64
        assert task_description
        return ScreenshotAnalysis(
            app_name="Chrome",
            window_title="编辑商品",
            ui_state_description="商品编辑表单已打开，保存按钮可用。",
            actionable_elements=[
                VisionUIElement(
                    element_type="button",
                    label="保存",
                    description="提交当前更改",
                    bbox=(500, 120, 80, 30),
                    confidence=0.93,
                    actionable=True,
                    suggested_action="click",
                )
            ],
            user_intent="用户准备保存商品编辑结果",
            suggested_next_actions=["点击保存按钮", "确认提示信息出现"],
            confidence=0.91,
            thinking_trace="button identified",
        )


class _FakeOCREngine:
    async def detect_text(self, screenshot_bytes):
        assert screenshot_bytes == b"fake-png-bytes"
        return [
            OCRResult(
                text="保存",
                center_x=240,
                center_y=116,
                confidence=0.98,
                bbox=[[200, 100], [280, 100], [280, 132], [200, 132]],
            )
        ]


class _FakeLLMService:
    is_configured = True

    async def chat(self, messages, **kwargs):
        assert messages
        return "建议记录为点击保存节点，并在点击后检查成功提示。"


class _SlowGuidanceLLMService:
    is_configured = True

    async def chat(self, messages, **kwargs):
        assert messages
        await asyncio.sleep(0.05)
        return "这条建议不应该及时返回"


class _FakeVisionFailureService:
    async def analyze_screenshot(self, screenshot_base64, task_description=None):
        raise VisionError("No module named 'mlx_vlm'")


class _FakeVisionFallbackLLMService:
    is_configured = True

    async def chat(self, messages, **kwargs):
        assert isinstance(messages[1]["content"], list)
        return (
            '{'
            '"app_name": "Chrome", '
            '"window_title": "编辑商品", '
            '"ui_state_description": "表单已打开，保存按钮处于可点击状态。", '
            '"actionable_elements": ['
            '  {'
            '    "element_type": "button", '
            '    "label": "保存", '
            '    "description": "提交当前更改", '
            '    "bbox": [500, 120, 80, 30], '
            '    "confidence": 0.9, '
            '    "actionable": true, '
            '    "suggested_action": "click"'
            '  }'
            '], '
            '"user_intent": "用户准备保存当前编辑结果", '
            '"suggested_next_actions": ["点击保存", "确认保存成功提示"], '
            '"confidence": 0.86, '
            '"thinking_trace": "cloud fallback"'
            '}'
        )

    def _extract_json(self, text):
        import json

        return json.loads(text)


class _FakeUnavailableLLMService:
    is_configured = False


class _WeakFocusSnapshotManager:
    def __init__(self):
        self.snapshot = DesktopSnapshot(
            snapshot_id="snap-focus-weak",
            session_id="session-weak",
            state=DesktopState(
                timestamp=123456790,
                active_window=WindowInfo(
                    window_id=2,
                    title="编辑商品",
                    app_name="Chrome",
                    pid=11,
                    bounds=Rect(x=0, y=0, width=1440, height=900),
                    is_active=True,
                ),
                focused_element=UIElement(
                    role="button",
                    bounds=Rect(x=200, y=100, width=88, height=32),
                    is_focused=True,
                ),
            ),
            screenshot_bytes=b"fake-png-bytes",
        )

    async def take_snapshot(self, session_id=None):
        self.snapshot.session_id = session_id
        return self.snapshot

    def get_screenshot_bytes(self, snapshot_id):
        assert snapshot_id == "snap-focus-weak"
        return b"fake-png-bytes"


class _ValueOnlyFocusSnapshotManager:
    def __init__(self, *, title: str | None = None, value: str | None = None, identifier: str | None = None):
        self.snapshot = DesktopSnapshot(
            snapshot_id="snap-focus-value",
            session_id="session-value",
            state=DesktopState(
                timestamp=123456791,
                active_window=WindowInfo(
                    window_id=3,
                    title="编辑商品",
                    app_name="Chrome",
                    pid=12,
                    bounds=Rect(x=0, y=0, width=1440, height=900),
                    is_active=True,
                ),
                focused_element=UIElement(
                    role="textfield",
                    title=title,
                    value=value,
                    identifier=identifier,
                    is_focused=True,
                ),
            ),
            screenshot_bytes=b"fake-png-bytes",
        )

    async def take_snapshot(self, session_id=None):
        self.snapshot.session_id = session_id
        return self.snapshot

    def get_screenshot_bytes(self, snapshot_id):
        assert snapshot_id == "snap-focus-value"
        return b"fake-png-bytes"


@pytest.mark.asyncio
async def test_capture_focus_context_includes_vision_ocr_and_node_suggestion():
    service = FocusContextService(
        snapshot_manager=_FakeSnapshotManager(),
        vision_service=_FakeVisionService(),
        ocr_engine=_FakeOCREngine(),
        llm_service=_FakeLLMService(),
    )

    capture = await service.capture("session-1")

    assert capture["session_id"] == "session-1"
    assert capture["snapshot_id"] == "snap-focus-1"
    assert capture["window_label"] == "Chrome · 编辑商品"
    assert capture["focused_label"] == "保存"
    assert capture["vision"]["available"] is True
    assert capture["vision"]["source"] == "local_vlm"
    assert capture["vision"]["user_intent"] == "用户准备保存商品编辑结果"
    assert capture["ocr"]["count"] == 1
    assert capture["ocr"]["items"][0]["text"] == "保存"
    assert capture["node_suggestion"]["step_type"] == "click"
    assert capture["node_suggestion"]["target"]["strategy"] == "accessibility_id"
    assert capture["node_suggestion"]["vision_source"] == "local_vlm"
    assert capture["recording_guidance"] == "建议记录为点击保存节点，并在点击后检查成功提示。"
    assert "建议节点:" in capture["focus_summary"]
    assert "视觉来源: local_vlm" in capture["focus_summary"]
    assert "当前意图: 用户准备保存商品编辑结果" in capture["focus_summary"]


@pytest.mark.asyncio
async def test_capture_focus_context_uses_vision_to_upgrade_weak_focus_target():
    service = FocusContextService(
        snapshot_manager=_WeakFocusSnapshotManager(),
        vision_service=_FakeVisionService(),
        ocr_engine=_FakeOCREngine(),
        llm_service=_FakeUnavailableLLMService(),
    )

    capture = await service.capture("session-weak", include_llm=False)

    assert capture["vision"]["source"] == "local_vlm"
    assert capture["node_suggestion"]["target"]["strategy"] == "text_match"
    assert capture["node_suggestion"]["target"]["text_contains"] == "保存"
    assert capture["node_suggestion"]["target"]["vision_element"]["label"] == "保存"
    assert capture["node_suggestion"]["confidence"] >= 0.72
    assert "视觉识别到了目标标签" in capture["node_suggestion"]["confidence_reason"]


@pytest.mark.asyncio
async def test_capture_focus_context_includes_text_preview_when_value_exists():
    service = FocusContextService(
        snapshot_manager=_ValueOnlyFocusSnapshotManager(value="sku-001", identifier="sku-field"),
        vision_service=_FakeVisionFailureService(),
        ocr_engine=_FakeOCREngine(),
        llm_service=_FakeUnavailableLLMService(),
    )

    capture = await service.capture("session-value", include_llm=False)

    assert capture["focused_label"] == "sku-001"
    assert capture["focused_details"]["text_preview"] == "sku-001"
    assert capture["node_suggestion"]["target"]["text_preview"] == "sku-001"
    assert "焦点文本: sku-001" in capture["focus_summary"]


@pytest.mark.asyncio
async def test_capture_focus_context_redacts_sensitive_value_preview():
    service = FocusContextService(
        snapshot_manager=_ValueOnlyFocusSnapshotManager(title="密码", value="super-secret"),
        vision_service=_FakeVisionFailureService(),
        ocr_engine=_FakeOCREngine(),
        llm_service=_FakeUnavailableLLMService(),
    )

    capture = await service.capture("session-value", include_llm=False)

    assert capture["focused_label"] == "密码"
    assert capture["focused_details"]["text_preview"] == "文本已脱敏"
    assert "super-secret" not in capture["focus_summary"]


@pytest.mark.asyncio
async def test_capture_focus_context_times_out_slow_guidance(monkeypatch):
    monkeypatch.setattr(focus_context_module, "_GUIDANCE_TIMEOUT_SECONDS", 0.01)
    service = FocusContextService(
        snapshot_manager=_FakeSnapshotManager(),
        vision_service=_FakeVisionService(),
        ocr_engine=_FakeOCREngine(),
        llm_service=_SlowGuidanceLLMService(),
    )

    capture = await service.capture("session-1")

    assert capture["recording_guidance"] is None
    assert "LLM 建议:" not in capture["focus_summary"]


@pytest.mark.asyncio
async def test_capture_focus_context_uses_llm_when_local_vision_unavailable():
    service = FocusContextService(
        snapshot_manager=_FakeSnapshotManager(),
        vision_service=_FakeVisionFailureService(),
        ocr_engine=_FakeOCREngine(),
        llm_service=_FakeVisionFallbackLLMService(),
    )

    capture = await service.capture("session-1", include_llm=False)

    assert capture["vision"]["available"] is True
    assert capture["vision"]["source"] == "cloud_llm_fallback"
    assert capture["vision"]["error"] == "No module named 'mlx_vlm'"
    assert capture["vision"]["user_intent"] == "用户准备保存当前编辑结果"
    assert capture["vision"]["actionable_elements"][0]["label"] == "保存"


@pytest.mark.asyncio
async def test_capture_focus_context_uses_context_fallback_when_vision_and_llm_are_unavailable():
    service = FocusContextService(
        snapshot_manager=_FakeSnapshotManager(),
        vision_service=_FakeVisionFailureService(),
        ocr_engine=_FakeOCREngine(),
        llm_service=_FakeUnavailableLLMService(),
    )

    capture = await service.capture("session-1", include_llm=False)

    assert capture["vision"]["available"] is False
    assert capture["vision"]["source"] == "context_fallback"
    assert "本地视觉模型当前不可用" in capture["vision"]["ui_state_description"]
    assert capture["vision"]["suggested_next_actions"]