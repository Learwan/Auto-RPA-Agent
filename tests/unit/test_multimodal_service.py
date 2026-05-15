import base64
from io import BytesIO

from PIL import Image

from src.llm.local_engine import InferenceResult
from src.llm.multimodal_service import MultimodalLLMService


class _FakeEngine:
    def __init__(self):
        self.calls = []

    async def analyze_with_image(self, prompt, image_base64=None, system_prompt=None, image_path=None):
        self.calls.append(
            {
                "prompt": prompt,
                "image_base64": image_base64,
                "system_prompt": system_prompt,
                "image_path": image_path,
            }
        )
        return InferenceResult(
            text=(
                '{'
                '"app_name": "Test App", '
                '"window_title": "Main Window", '
                '"ui_state_description": "测试界面包含一个主要按钮。", '
                '"actionable_elements": ['
                '  {'
                '    "element_type": "button", '
                '    "label": "保存", '
                '    "description": "提交当前更改", '
                '    "bbox": [500, 120, 80, 30], '
                '    "confidence": 0.92, '
                '    "actionable": true, '
                '    "suggested_action": "click"'
                '  }'
                '], '
                '"user_intent": "用户准备保存修改", '
                '"suggested_next_actions": ["点击保存按钮"], '
                '"confidence": 0.88'
                '}'
            ),
            tokens_generated=42,
            elapsed_ms=10.0,
            tokens_per_second=4.2,
        )


async def test_analyze_screenshot_passes_structured_system_prompt():
    service = MultimodalLLMService()
    fake_engine = _FakeEngine()
    service._engine = fake_engine
    image = Image.new("RGB", (2, 2), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    result = await service.analyze_screenshot(
        image_b64,
        task_description="请分析测试截图",
    )

    assert fake_engine.calls
    assert "输出格式" in fake_engine.calls[0]["system_prompt"]
    assert result.app_name == "Test App"
    assert result.actionable_elements[0].label == "保存"