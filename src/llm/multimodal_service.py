from __future__ import annotations

import json
import logging
import re
import time
from base64 import b64decode
from io import BytesIO

from PIL import Image

from src.config import settings
from src.llm.local_engine import LocalLLMEngine
from src.llm.token_counter import TokenCounter
from src.models.vision import ScreenshotAnalysis, ScreenshotDiff, UIElement, VisualStep
from src.monitoring.metrics import MetricsCollector

logger = logging.getLogger(__name__)

_MAX_IMAGE_WIDTH = 1280
_MAX_IMAGE_DIMENSION = 4096
_JPEG_QUALITY = 85


def validate_and_normalize_image(base64_data: str) -> str:
    raw = base64_data.split(",", 1)[-1] if "," in base64_data else base64_data
    try:
        img_bytes = b64decode(raw)
    except Exception:
        raise VisionInvalidImageError("Invalid base64 image data")

    try:
        img = Image.open(BytesIO(img_bytes))
        img.verify()
    except Exception:
        raise VisionInvalidImageError("Cannot decode image data")

    img = Image.open(BytesIO(img_bytes))
    w, h = img.size
    if w > _MAX_IMAGE_DIMENSION or h > _MAX_IMAGE_DIMENSION:
        raise VisionInvalidImageError(f"Image too large: {w}x{h}, max {_MAX_IMAGE_DIMENSION}px")

    if w > _MAX_IMAGE_WIDTH or h > _MAX_IMAGE_WIDTH:
        ratio = _MAX_IMAGE_WIDTH / max(w, h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buffer = BytesIO()
    img_format = img.format or "PNG"
    if img_format.upper() in ("JPEG", "JPG"):
        img.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    else:
        img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=_JPEG_QUALITY)

    import base64

    return base64.b64encode(buffer.getvalue()).decode("utf-8")


_ANALYZE_SCREENSHOT_SYSTEM = (
    "你是一个专业的GUI界面分析助手。分析给定的屏幕截图，输出严格的JSON格式。\n"
    "\n"
    "输出格式：\n"
    "{\n"
    '  "app_name": "应用名称",\n'
    '  "window_title": "窗口标题",\n'
    '  "ui_state_description": "对该界面状态的简洁描述",\n'
    '  "actionable_elements": [\n'
    "    {\n"
    '      "element_type": "button|input|dropdown|checkbox|link|text|icon|menu|tab",\n'
    '      "label": "元素显示文本",\n'
    '      "description": "该元素的功能",\n'
    '      "bbox": [x_center, y_center, width, height],\n'
    '      "confidence": 0.95,\n'
    '      "actionable": true,\n'
    '      "suggested_action": "click|type|scroll|select|hover|drag|null"\n'
    "    }\n"
    "  ],\n"
    '  "user_intent": "用户在当前界面中可能想要执行的操作",\n'
    '  "suggested_next_actions": ["建议的下一步操作列表"],\n'
    '  "confidence": 0.9\n'
    "}\n"
    "\n"
    "坐标使用0-1000归一化坐标系统。\n"
    "仅输出JSON，不要添加任何额外的文字或标记。"
)

_COMPARE_SCREENSHOTS_SYSTEM = (
    "你是一个GUI界面变化检测专家。对比两张屏幕截图（before和after），识别UI变化。\n"
    "\n"
    "输出严格JSON：\n"
    "{\n"
    '  "added_elements": [\n'
    '    {"element_type": "button|input|dialog|text|...", "label": "元素描述",\n'
    '     "bbox": [x_center, y_center, width, height], "change_description": "发生了什么变化"}\n'
    "  ],\n"
    '  "removed_elements": [\n'
    '    {"element_type": "...", "label": "元素描述", "bbox": [...], "change_description": "..."}\n'
    "  ],\n"
    '  "changed_elements": [\n'
    '    {"element_type": "...", "label": "元素描述", "bbox": [...], "change_description": "..."}\n'
    "  ],\n"
    '  "state_transition": "从before到after的状态变化描述",\n'
    '  "is_significant_change": true\n'
    "}\n"
    "坐标使用0-1000归一化。仅输出JSON。"
)

_EXTRACT_UI_ELEMENTS_SYSTEM = (
    "你是一个GUI元素提取专家。列出截图中所有可交互的UI元素。\n"
    "\n"
    "输出严格JSON数组：\n"
    "[\n"
    "  {\n"
    '    "element_type": "button|input|dropdown|checkbox|link|text|icon|menu|tab",\n'
    '    "label": "元素显示的文本或描述",\n'
    '    "description": "功能描述",\n'
    '    "bbox": [x_center, y_center, width, height],\n'
    '    "confidence": 0.95,\n'
    '    "actionable": true,\n'
    '    "suggested_action": "click|type|scroll|select|null"\n'
    "  }\n"
    "]\n"
    "坐标0-1000归一化。仅输出JSON数组，不要其他内容。"
)

_GENERATE_WORKFLOW_SYSTEM = (
    "你是一个自动化流程生成专家。根据截图序列和操作日志，生成结构化的可视流程步骤。\n"
    "\n"
    "输出严格JSON数组：\n"
    "[\n"
    "  {\n"
    '    "step_index": 1,\n'
    '    "description": "操作描述",\n'
    '    "action_type": "click|type|scroll|select|hover|navigate|wait",\n'
    '    "target_label": "目标元素标签",\n'
    '    "confidence": 0.92\n'
    "  }\n"
    "]\n"
    "仅输出JSON数组。"
)


def _extract_json(text: str) -> dict | list | None:
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if json_match:
        text = json_match.group(1)
    elif "```" in text:
        text = text.replace("```", "")

    text = text.strip()
    best_result = None
    for start, end in [("[", "]"), ("{", "}")]:
        idx_start = text.find(start)
        idx_end = text.rfind(end)
        if idx_start != -1 and idx_end > idx_start:
            try:
                candidate = json.loads(text[idx_start : idx_end + 1])
                if best_result is None or idx_start < best_result[0]:
                    best_result = (idx_start, candidate)
            except json.JSONDecodeError:
                continue
    if best_result:
        return best_result[1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class VisionError(Exception):
    def __init__(self, message: str, trace_id: str = "", model: str = "") -> None:
        self.trace_id = trace_id
        self.model = model
        super().__init__(message)


class VisionModelUnavailableError(VisionError):
    pass


class VisionTimeoutError(VisionError):
    pass


class VisionGroundingFailedError(VisionError):
    pass


class VisionInvalidImageError(VisionError):
    pass


class MultimodalLLMService:
    def __init__(self) -> None:
        self._engine: LocalLLMEngine | None = None
        self._token_counter = TokenCounter.get_instance()
        self._metrics = MetricsCollector.get_instance()

    async def _get_engine(self) -> LocalLLMEngine:
        if self._engine is None:
            self._engine = await LocalLLMEngine.get_instance()
        return self._engine

    def _track_call(self, text_in: str, text_out: str, latency_ms: float, images_count: int, success: bool) -> None:
        tokens_in = TokenCounter.estimate_text_tokens(text_in)
        tokens_out = TokenCounter.estimate_text_tokens(text_out)
        model = settings.LOCAL_LLM_MODEL
        self._token_counter.record_call(model, tokens_in, tokens_out, images_count, latency_ms)
        self._metrics.track_llm_call(model, tokens_in, tokens_out, latency_ms, images_count, success)

    async def analyze_screenshot(
        self,
        screenshot_base64: str,
        task_description: str | None = None,
    ) -> ScreenshotAnalysis:
        normalized_image = validate_and_normalize_image(screenshot_base64)
        prompt = task_description or "请分析这张截图中的UI界面，列出所有可交互元素并推断用户意图。"
        engine = await self._get_engine()

        last_error = None
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                result = await engine.analyze_with_image(
                    prompt=prompt,
                    image_base64=normalized_image,
                    system_prompt=_ANALYZE_SCREENSHOT_SYSTEM,
                )
                latency_ms = (time.perf_counter() - t0) * 1000
                parsed = _extract_json(result.text)
                self._track_call(prompt, result.text, latency_ms, 1, True)
                logger.info(
                    "analyze_screenshot latency=%.0fms tokens=%d success=true attempt=%d",
                    latency_ms,
                    result.tokens_generated,
                    attempt + 1,
                )
                break
            except Exception as e:
                latency_ms = (time.perf_counter() - t0) * 1000
                last_error = e
                self._track_call(prompt, "", latency_ms, 1, False)
                logger.warning(
                    "analyze_screenshot latency=%.0fms attempt=%d error=%s",
                    latency_ms,
                    attempt + 1,
                    e,
                )
                if attempt < 2:
                    await __import__("asyncio").sleep(1.0 * (2**attempt))
        else:
            raise VisionModelUnavailableError(str(last_error)) from last_error

        if not parsed or not isinstance(parsed, dict):
            return ScreenshotAnalysis(
                app_name="unknown",
                window_title="unknown",
                ui_state_description=result.text[:500],
                actionable_elements=[],
                user_intent="",
                suggested_next_actions=[],
                confidence=0.0,
            )

        elements = []
        for el in parsed.get("actionable_elements", []):
            bbox_raw = el.get("bbox")
            bbox = tuple(bbox_raw) if isinstance(bbox_raw, list) and len(bbox_raw) == 4 else None
            elements.append(
                UIElement(
                    element_type=el.get("element_type", "unknown"),
                    label=el.get("label", ""),
                    description=el.get("description", ""),
                    bbox=bbox,
                    confidence=float(el.get("confidence", 0.0)),
                    actionable=bool(el.get("actionable", False)),
                    suggested_action=el.get("suggested_action"),
                )
            )

        return ScreenshotAnalysis(
            app_name=parsed.get("app_name", "unknown"),
            window_title=parsed.get("window_title", ""),
            ui_state_description=parsed.get("ui_state_description", ""),
            actionable_elements=elements,
            user_intent=parsed.get("user_intent", ""),
            suggested_next_actions=parsed.get("suggested_next_actions", []),
            confidence=float(parsed.get("confidence", 0.0)),
            thinking_trace=parsed.get("thinking_trace"),
        )

    async def compare_screenshots(
        self,
        before_base64: str,
        after_base64: str,
    ) -> ScreenshotDiff:
        engine = await self._get_engine()
        prompt = (
            "第一张是变更前的截图，第二张是变更后的截图。请对比两张截图，列出新增、移除和变更的UI元素。使用中文回答。"
        )

        t0 = time.perf_counter()
        try:
            result = await engine.generate_with_images(
                prompt_text=prompt,
                image_base64s=[before_base64, after_base64],
                system_prompt=_COMPARE_SCREENSHOTS_SYSTEM,
            )
        except Exception:
            result = await engine.analyze_with_image(
                prompt=prompt,
                image_base64=before_base64,
                system_prompt=_COMPARE_SCREENSHOTS_SYSTEM,
            )
            _ = await engine.analyze_with_image(
                prompt=prompt,
                image_base64=after_base64,
                system_prompt=_COMPARE_SCREENSHOTS_SYSTEM,
            )
            result.text = result.text + "\n" + _.text

        latency_ms = (time.perf_counter() - t0) * 1000
        parsed = _extract_json(result.text)
        self._track_call(prompt, result.text, latency_ms, 2, True)

        if not parsed or not isinstance(parsed, dict):
            return ScreenshotDiff(state_transition=result.text[:300])

        def _parse_els(raw_list: list) -> list[UIElement]:
            els = []
            for el in raw_list:
                bbox_raw = el.get("bbox")
                bbox = tuple(bbox_raw) if isinstance(bbox_raw, list) and len(bbox_raw) == 4 else None
                els.append(
                    UIElement(
                        element_type=el.get("element_type", "unknown"),
                        label=el.get("label", ""),
                        description=el.get("change_description", el.get("description", "")),
                        bbox=bbox,
                        confidence=float(el.get("confidence", 0.0)),
                        actionable=False,
                        suggested_action=None,
                    )
                )
            return els

        return ScreenshotDiff(
            added_elements=_parse_els(parsed.get("added_elements", [])),
            removed_elements=_parse_els(parsed.get("removed_elements", [])),
            changed_elements=_parse_els(parsed.get("changed_elements", [])),
            state_transition=parsed.get("state_transition", ""),
            is_significant_change=bool(parsed.get("is_significant_change", False)),
        )

    async def extract_ui_elements(
        self,
        screenshot_base64: str,
    ) -> list[UIElement]:
        engine = await self._get_engine()

        t0 = time.perf_counter()
        result = await engine.analyze_with_image(
            prompt="列出这张截图中所有可交互的UI元素。",
            image_base64=screenshot_base64,
            system_prompt=_EXTRACT_UI_ELEMENTS_SYSTEM,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        parsed = _extract_json(result.text)
        self._track_call("extract_ui_elements", result.text, latency_ms, 1, True)

        if not parsed:
            return []

        items = parsed if isinstance(parsed, list) else parsed.get("elements", parsed.get("actionable_elements", []))
        elements = []
        for el in items:
            bbox_raw = el.get("bbox")
            bbox = tuple(bbox_raw) if isinstance(bbox_raw, list) and len(bbox_raw) == 4 else None
            elements.append(
                UIElement(
                    element_type=el.get("element_type", "unknown"),
                    label=el.get("label", ""),
                    description=el.get("description", ""),
                    bbox=bbox,
                    confidence=float(el.get("confidence", 0.0)),
                    actionable=bool(el.get("actionable", True)),
                    suggested_action=el.get("suggested_action"),
                )
            )
        return elements

    async def generate_workflow_from_trace(
        self,
        screenshots_base64: list[str],
        operations: list[dict],
    ) -> list[VisualStep]:
        if not screenshots_base64:
            return []

        engine = await self._get_engine()
        ops_text = "\n".join(
            f"[{i}] {op.get('type', '?')}: {op.get('description', op.get('data', ''))}"
            for i, op in enumerate(operations)
        )

        prompt = f"以下是用户操作记录：\n{ops_text}\n\n请根据截图和操作记录，生成结构化的自动化流程步骤。"

        t0 = time.perf_counter()
        result = await engine.analyze_with_image(
            prompt=prompt,
            image_base64=screenshots_base64[0],
            system_prompt=_GENERATE_WORKFLOW_SYSTEM,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        parsed = _extract_json(result.text)
        self._track_call(prompt, result.text, latency_ms, len(screenshots_base64), True)

        if not parsed or not isinstance(parsed, list):
            return []

        steps = []
        for s in parsed:
            steps.append(
                VisualStep(
                    step_index=int(s.get("step_index", len(steps) + 1)),
                    description=s.get("description", ""),
                    action_type=s.get("action_type", ""),
                    target_element=None,
                    before_screenshot_ref=screenshots_base64[0] if screenshots_base64 else None,
                    after_screenshot_ref=None,
                    confidence=float(s.get("confidence", 0.0)),
                )
            )
        return steps
