from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING

from src.config import settings
from src.models.vision import GroundingResult

if TYPE_CHECKING:
    from src.llm.ui_tars_adapter import UITarsAdapter

logger = logging.getLogger(__name__)

_GROUNDING_SYSTEM = (
    "你是一个精确的GUI元素定位专家。给定截图和操作描述，找出目标元素的精确位置。\n"
    "\n"
    "输出严格JSON：\n"
    "{\n"
    '  "found": true,\n'
    '  "element_type": "button|input|dropdown|checkbox|link|text|icon|menu|tab",\n'
    '  "element_label": "元素的显示文本或描述",\n'
    '  "bbox": [x_center, y_center, width, height],\n'
    '  "confidence": 0.92,\n'
    '  "alternative_targets": [\n'
    '    {"label": "备选元素描述", "bbox": [...], "confidence": 0.7}\n'
    "  ],\n"
    '  "reasoning": "简要说明定位依据"\n'
    "}\n"
    "\n"
    "坐标使用0-1000归一化坐标系统。bbox格式：[x_center, y_center, width, height]。\n"
    "如果找不到匹配元素，found设为false，bbox设为null。\n"
    "仅输出JSON，不要添加任何额外的文字或标记。"
)

_GROUNDING_BATCH_SYSTEM = (
    "你是一个精确的GUI元素定位专家。给定截图和多个操作描述，找出每个操作对应的目标元素位置。\n"
    "\n"
    "输出严格JSON对象，键为操作索引(从0开始)：\n"
    "{\n"
    '  "0": {"found": true, "bbox": [x_center, y_center, width, height], "confidence": 0.92},\n'
    '  "1": {"found": false, "bbox": null, "confidence": 0.0}\n'
    "}\n"
    "\n"
    "坐标使用0-1000归一化。仅输出JSON对象。"
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


class GUIGroundingEngine:
    def __init__(self) -> None:
        self._threshold = settings.VISION_GROUNDING_CONFIDENCE_THRESHOLD
        self._ui_tars_adapter: UITarsAdapter | None = None
        self._grounding_engine = getattr(settings, "GROUNDING_ENGINE", "qwen")
        self._cache: dict[str, GroundingResult] = {}
        self._cache_max_size = 128
        self._calibration_history: list[dict] = []

    def _cache_key(self, screenshot_base64: str, action_description: str, action_type: str) -> str:
        import hashlib

        screenshot_hash = (
            hashlib.md5(screenshot_base64.encode()).hexdigest()[:16]
            if len(screenshot_base64) > 64
            else screenshot_base64[:16]
        )
        return f"{screenshot_hash}:{action_type}:{action_description[:64]}"

    def _get_cached(self, key: str) -> GroundingResult | None:
        return self._cache.get(key)

    def _set_cached(self, key: str, result: GroundingResult) -> None:
        if len(self._cache) >= self._cache_max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[key] = result

    def _calibrate_confidence(self, raw_confidence: float, action_type: str) -> float:
        if not self._calibration_history:
            return raw_confidence
        relevant = [h for h in self._calibration_history if h.get("action_type") == action_type]
        if len(relevant) < 5:
            return raw_confidence
        avg_error = sum(abs(h["predicted"] - h["actual"]) for h in relevant) / len(relevant)
        if avg_error > 0.3:
            calibrated = raw_confidence * 0.8
        elif avg_error > 0.15:
            calibrated = raw_confidence * 0.9
        else:
            calibrated = raw_confidence
        return round(max(0.0, min(1.0, calibrated)), 4)

    def record_calibration(self, action_type: str, predicted: float, actual: float) -> None:
        self._calibration_history.append(
            {
                "action_type": action_type,
                "predicted": predicted,
                "actual": actual,
            }
        )
        if len(self._calibration_history) > 1000:
            self._calibration_history = self._calibration_history[-500:]

    async def _get_engine(self):
        from src.llm.local_engine import LocalLLMEngine

        return await LocalLLMEngine.get_instance()

    async def _get_ui_tars(self) -> UITarsAdapter:
        if self._ui_tars_adapter is None:
            from src.llm.ui_tars_adapter import UITarsAdapter

            self._ui_tars_adapter = UITarsAdapter()
        return self._ui_tars_adapter

    def _should_use_ui_tars(self) -> bool:
        engine = self._grounding_engine
        if engine == "ui_tars":
            return True
        if engine == "auto":
            ui_tars_path = getattr(settings, "UI_TARS_MODEL_PATH", "")
            ui_tars_url = getattr(settings, "UI_TARS_SERVER_URL", "")
            return bool(ui_tars_path or ui_tars_url)
        return False

    def _normalized_to_pixel(
        self,
        bbox_normalized: list,
        image_width: int,
        image_height: int,
    ) -> tuple[int, int, int, int]:
        x_c, y_c, w_n, h_n = bbox_normalized
        x = int(x_c * image_width / 1000.0)
        y = int(y_c * image_height / 1000.0)
        w = int(w_n * image_width / 1000.0)
        h = int(h_n * image_height / 1000.0)
        return (x, y, w, h)

    def _parse_grounding_result(
        self,
        data: dict,
        action_type: str,
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> GroundingResult:
        found = bool(data.get("found", False))
        bbox_raw = data.get("bbox")
        bbox_pixel = None
        bbox_normalized = None
        confidence = float(data.get("confidence", 0.0))

        if found and isinstance(bbox_raw, list) and len(bbox_raw) == 4:
            bbox_normalized = tuple(bbox_raw)
            bbox_pixel = self._normalized_to_pixel(bbox_raw, image_width, image_height)
        else:
            confidence = 0.0

        alternatives = []
        for alt in data.get("alternative_targets", []):
            alt_bbox_raw = alt.get("bbox")
            alt_bbox_pixel = None
            if isinstance(alt_bbox_raw, list) and len(alt_bbox_raw) == 4:
                alt_bbox_pixel = self._normalized_to_pixel(alt_bbox_raw, image_width, image_height)
            alternatives.append(
                {
                    "label": alt.get("label", ""),
                    "bbox_pixel": alt_bbox_pixel,
                    "confidence": float(alt.get("confidence", 0.0)),
                }
            )

        return GroundingResult(
            found=found and confidence >= self._threshold,
            element_type=data.get("element_type", action_type),
            element_label=data.get("element_label", ""),
            bbox_pixel=bbox_pixel,
            bbox_normalized=bbox_normalized,
            confidence=confidence,
            alternative_targets=alternatives,
            reasoning=data.get("reasoning", ""),
        )

    async def ground_action(
        self,
        screenshot_base64: str,
        action_description: str,
        action_type: str,
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> GroundingResult:
        cache_key = self._cache_key(screenshot_base64, action_description, action_type)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        if self._should_use_ui_tars():
            try:
                adapter = await self._get_ui_tars()
                result = await adapter.ground_action(
                    screenshot_base64,
                    action_description,
                    action_type,
                    image_width,
                    image_height,
                )
                if result.found and result.confidence >= self._threshold:
                    result.confidence = self._calibrate_confidence(result.confidence, action_type)
                    self._set_cached(cache_key, result)
                    return result
                logger.info("UI-TARS result below threshold, falling back to Qwen")
            except Exception as e:
                logger.warning("UI-TARS grounding failed, falling back to Qwen: %s", e)

        result = await self._ground_action_qwen(
            screenshot_base64,
            action_description,
            action_type,
            image_width,
            image_height,
        )
        result.confidence = self._calibrate_confidence(result.confidence, action_type)
        self._set_cached(cache_key, result)
        return result

    async def _ground_action_qwen(
        self,
        screenshot_base64: str,
        action_description: str,
        action_type: str,
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> GroundingResult:
        engine = await self._get_engine()
        prompt = f"操作描述：{action_description}\n操作类型：{action_type}\n\n请找出截图中与该操作最匹配的UI元素。"

        t0 = time.perf_counter()
        result = await engine.analyze_with_image(
            prompt=prompt,
            image_base64=screenshot_base64,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "ground_action latency=%.0fms type=%s desc=%s",
            latency_ms,
            action_type,
            action_description[:80],
        )

        parsed = _extract_json(result.text)
        if not parsed or not isinstance(parsed, dict):
            return GroundingResult(
                found=False,
                element_type=action_type,
                element_label=action_description,
                bbox_pixel=None,
                bbox_normalized=None,
                confidence=0.0,
                reasoning=result.text[:200],
            )

        return self._parse_grounding_result(parsed, action_type, image_width, image_height)

    async def ground_batch(
        self,
        screenshot_base64: str,
        actions: list[dict],
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> list[GroundingResult]:
        if not actions:
            return []

        if self._should_use_ui_tars():
            try:
                adapter = await self._get_ui_tars()
                results = await adapter.ground_batch(
                    screenshot_base64,
                    actions,
                    image_width,
                    image_height,
                )
                all_above = all(r.found and r.confidence >= self._threshold for r in results)
                if all_above:
                    return results
                logger.info("UI-TARS batch results partially below threshold, using mixed results")
                return results
            except Exception as e:
                logger.warning("UI-TARS batch grounding failed, falling back to Qwen: %s", e)

        return await self._ground_batch_qwen(
            screenshot_base64,
            actions,
            image_width,
            image_height,
        )

    async def _ground_batch_qwen(
        self,
        screenshot_base64: str,
        actions: list[dict],
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> list[GroundingResult]:
        engine = await self._get_engine()
        actions_text = "\n".join(
            f"[{i}] 类型={a.get('type', 'unknown')} 描述={a.get('description', '')}" for i, a in enumerate(actions)
        )

        prompt = f"需要定位以下操作的目标元素：\n{actions_text}\n\n请为每个操作输出定位结果，使用操作索引作为键。"

        t0 = time.perf_counter()
        result = await engine.analyze_with_image(
            prompt=prompt,
            image_base64=screenshot_base64,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info("ground_batch latency=%.0fms actions=%d", latency_ms, len(actions))

        parsed = _extract_json(result.text)
        if not parsed or not isinstance(parsed, dict):
            return [
                GroundingResult(
                    found=False,
                    element_type=a.get("type", "unknown"),
                    element_label=a.get("description", ""),
                    bbox_pixel=None,
                    bbox_normalized=None,
                    confidence=0.0,
                )
                for a in actions
            ]

        results = []
        for i, a in enumerate(actions):
            key = str(i)
            data = parsed.get(key, {})
            if isinstance(data, dict):
                results.append(
                    self._parse_grounding_result(
                        data,
                        a.get("type", "unknown"),
                        image_width,
                        image_height,
                    )
                )
            else:
                results.append(
                    GroundingResult(
                        found=False,
                        element_type=a.get("type", "unknown"),
                        element_label=a.get("description", ""),
                        bbox_pixel=None,
                        bbox_normalized=None,
                        confidence=0.0,
                    )
                )

        return results

    async def verify_action(
        self,
        before_base64: str,
        after_base64: str,
        expected_action: dict,
    ) -> tuple[bool, str]:
        engine = await self._get_engine()
        action_desc = expected_action.get("description", str(expected_action))

        prompt = (
            "第一张是操作前的截图，第二张是操作后的截图。"
            f"判断这个操作是否成功执行：{action_desc}。"
            "请严格按以下JSON格式输出：\n"
            '{"success": true/false, "confidence": 0.0-1.0, "reasoning": "判断依据"}\n'
            "仅输出JSON，不要添加额外文字。"
        )

        t0 = time.perf_counter()
        try:
            result = await engine.generate_with_images(
                prompt_text=prompt,
                image_base64s=[before_base64, after_base64],
                system_prompt="你是GUI操作验证专家。对比两张截图判断操作是否成功。严格输出JSON格式。",
            )
        except Exception:
            result = await engine.analyze_with_image(
                prompt=prompt + "\n\n请描述这张截图中的UI状态。",
                image_base64=after_base64,
            )

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info("verify_action latency=%.0fms action=%s", latency_ms, action_desc[:50])

        parsed = _extract_json(result.text)
        if parsed and isinstance(parsed, dict):
            success = bool(parsed.get("success", False))
            confidence = float(parsed.get("confidence", 0.0))
            reasoning = parsed.get("reasoning", result.text[:200])
            if confidence < 0.3:
                success = False
            return success, reasoning

        text = result.text.strip().lower()
        success = any(kw in text for kw in ["成功", "完成", "正确", "success", "已执行"])
        return success, result.text[:200]
