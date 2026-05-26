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
        self._remote_llm = None
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

    async def _get_remote_llm(self):
        if self._remote_llm is None:
            from src.llm.service import LLMService

            self._remote_llm = LLMService()
        return self._remote_llm

    def _remote_llm_available(self) -> bool:
        return bool(settings.llm_configured)

    def _should_use_ui_tars(self) -> bool:
        engine = self._grounding_engine
        if engine == "ui_tars":
            return True
        if engine == "auto":
            ui_tars_path = getattr(settings, "UI_TARS_MODEL_PATH", "")
            ui_tars_url = getattr(settings, "UI_TARS_SERVER_URL", "")
            return bool(ui_tars_path or ui_tars_url)
        return False

    def _resolve_backend(self) -> str:
        return "ui_tars" if self._should_use_ui_tars() else "qwen"

    def _provider_chain(self) -> list[str]:
        providers = []
        preferred = self._resolve_backend()
        if preferred == "ui_tars":
            providers.append("ui_tars")
        providers.append("qwen_local")
        if self._remote_llm_available():
            providers.append("cloud_llm")
        return providers

    def _annotate_result_provenance(
        self,
        result: GroundingResult,
        provider: str,
        attempted_providers: list[str],
    ) -> GroundingResult:
        provider_chain = self._provider_chain()
        result.provider = provider
        result.provider_chain = provider_chain
        result.fallback_chain = provider_chain[1:]
        result.attempted_providers = list(attempted_providers)
        return result

    def _is_usable_result(self, result: GroundingResult | None) -> bool:
        return bool(result and result.found and result.confidence >= self._threshold)

    def get_runtime_status(self) -> dict:
        from src.llm.local_engine import LocalLLMEngine

        runtime = LocalLLMEngine.get_runtime_snapshot()
        preferred_backend = self._resolve_backend()
        ui_tars_model_path = getattr(settings, "UI_TARS_MODEL_PATH", "")
        ui_tars_server_url = getattr(settings, "UI_TARS_SERVER_URL", "")
        ui_tars_server_enabled = bool(ui_tars_server_url)
        ui_tars_config_source = (
            "server_url"
            if ui_tars_server_url
            else ("model_path" if ui_tars_model_path else "implicit_local_adapter")
        )
        vision_enabled = bool(getattr(settings, "VISION_ENABLED", False))
        local_llm_enabled = bool(getattr(settings, "LOCAL_LLM_ENABLED", False))
        remote_llm_enabled = self._remote_llm_available()
        runtime_loaded = bool(runtime.get("vision_loaded"))
        runtime_backend = runtime.get("vision_backend")

        if not vision_enabled:
            status = "disabled"
            detail = "视觉分析已关闭，grounding 不会被调用。"
        elif preferred_backend == "ui_tars":
            status = "ready" if (ui_tars_server_enabled or runtime_loaded or remote_llm_enabled) else "deferred"
            if ui_tars_server_enabled:
                detail = "优先使用远端 UI-TARS provider；失败时会回退到本地 qwen，必要时再回退到云端多模态 LLM。"
            else:
                detail = "优先使用本地 UI-TARS 适配层；失败时会回退到本地 qwen，必要时再回退到云端多模态 LLM。"
        elif runtime_loaded:
            status = "ready"
            detail = f"优先使用 qwen；本地视觉已加载（{runtime_backend or 'unknown'}）。"
        elif remote_llm_enabled:
            status = "degraded"
            detail = "本地视觉尚未加载，将直接回退到云端多模态 LLM 尝试 grounding。"
        else:
            status = "deferred"
            detail = "优先使用 qwen；本地视觉尚未加载到运行时，且未配置云端多模态 fallback。"

        return {
            "requested_engine": self._grounding_engine,
            "preferred_backend": preferred_backend,
            "status": status,
            "detail": detail,
            "can_attempt_grounding": vision_enabled,
            "vision_enabled": vision_enabled,
            "local_llm_enabled": local_llm_enabled,
            "remote_llm_enabled": remote_llm_enabled,
            "fallback_chain": self._provider_chain()[1:],
            "provider_chain": self._provider_chain(),
            "runtime_loaded": runtime_loaded,
            "runtime_backend": runtime_backend,
            "runtime_device": runtime.get("vision_device"),
            "resolved_local_engine": runtime.get("resolved_engine"),
            "backends": {
                "ui_tars": {
                    "selected": preferred_backend == "ui_tars",
                    "config_source": ui_tars_config_source,
                    "server_enabled": ui_tars_server_enabled,
                    "model_path_configured": bool(ui_tars_model_path),
                    "server_url_configured": bool(ui_tars_server_url),
                },
                "qwen": {
                    "selected": preferred_backend == "qwen",
                    "model_path": runtime.get("vision_model_path") or runtime.get("model_path"),
                    "runtime_loaded": runtime_loaded,
                    "runtime_backend": runtime_backend,
                    "runtime_device": runtime.get("vision_device"),
                    "resolved_local_engine": runtime.get("resolved_engine"),
                },
                "cloud_llm": {
                    "selected": preferred_backend == "qwen" and not runtime_loaded and remote_llm_enabled,
                    "configured": remote_llm_enabled,
                },
            },
        }

    async def _ground_action_cloud_llm(
        self,
        screenshot_base64: str,
        action_description: str,
        action_type: str,
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> GroundingResult:
        llm = await self._get_remote_llm()
        response = await llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": _GROUNDING_SYSTEM,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"操作描述：{action_description}\n"
                                f"操作类型：{action_type}\n\n"
                                "请找出截图中与该操作最匹配的 UI 元素。"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"},
                        },
                    ],
                },
            ],
            temperature=0.2,
            top_p=0.9,
            max_tokens=900,
            stream=False,
        )
        parsed = _extract_json(response)
        if not parsed or not isinstance(parsed, dict):
            return GroundingResult(
                found=False,
                element_type=action_type,
                element_label=action_description,
                bbox_pixel=None,
                bbox_normalized=None,
                confidence=0.0,
                reasoning=response[:200],
                provider="cloud_llm",
            )

        result = self._parse_grounding_result(parsed, action_type, image_width, image_height)
        result.provider = "cloud_llm"
        return result

    async def _ground_batch_cloud_llm(
        self,
        screenshot_base64: str,
        actions: list[dict],
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> list[GroundingResult]:
        llm = await self._get_remote_llm()
        response = await llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": _GROUNDING_BATCH_SYSTEM,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "需要定位以下操作的目标元素：\n"
                                + "\n".join(
                                    f"[{i}] 类型={a.get('type', 'unknown')} 描述={a.get('description', '')}"
                                    for i, a in enumerate(actions)
                                )
                                + "\n\n请为每个操作输出定位结果，使用操作索引作为键。"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"},
                        },
                    ],
                },
            ],
            temperature=0.2,
            top_p=0.9,
            max_tokens=1400,
            stream=False,
        )
        parsed = _extract_json(response)
        if not parsed or not isinstance(parsed, dict):
            return [
                GroundingResult(
                    found=False,
                    element_type=a.get("type", "unknown"),
                    element_label=a.get("description", ""),
                    bbox_pixel=None,
                    bbox_normalized=None,
                    confidence=0.0,
                    reasoning=response[:200],
                    provider="cloud_llm",
                )
                for a in actions
            ]

        results = []
        for i, action in enumerate(actions):
            data = parsed.get(str(i), {})
            if isinstance(data, dict):
                result = self._parse_grounding_result(data, action.get("type", "unknown"), image_width, image_height)
                result.provider = "cloud_llm"
                results.append(result)
            else:
                results.append(
                    GroundingResult(
                        found=False,
                        element_type=action.get("type", "unknown"),
                        element_label=action.get("description", ""),
                        bbox_pixel=None,
                        bbox_normalized=None,
                        confidence=0.0,
                        provider="cloud_llm",
                    )
                )
        return results

    async def _verify_action_cloud_llm(
        self,
        before_base64: str,
        after_base64: str,
        action_desc: str,
    ) -> tuple[bool, str]:
        llm = await self._get_remote_llm()
        response = await llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 GUI 操作验证专家。对比前后两张截图，判断预期操作是否真正生效。"
                        "请严格输出 JSON：{\"success\": true/false, \"confidence\": 0.0-1.0, \"reasoning\": \"判断依据\"}。"
                        "只输出 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"操作描述：{action_desc}。请判断该操作是否成功执行。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{before_base64}"},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{after_base64}"},
                        },
                    ],
                },
            ],
            temperature=0.2,
            top_p=0.9,
            max_tokens=500,
            stream=False,
        )
        parsed = _extract_json(response)
        if parsed and isinstance(parsed, dict):
            success = bool(parsed.get("success", False))
            confidence = float(parsed.get("confidence", 0.0))
            reasoning = str(parsed.get("reasoning", response[:200]))
            if confidence < 0.3:
                success = False
            return success, reasoning
        text = str(response).strip().lower()
        success = any(kw in text for kw in ["成功", "完成", "正确", "success", "已执行"])
        return success, str(response)[:200]

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

        best_result: GroundingResult | None = None
        attempted_providers: list[str] = []

        if self._should_use_ui_tars():
            try:
                attempted_providers.append("ui_tars")
                adapter = await self._get_ui_tars()
                result = await adapter.ground_action(
                    screenshot_base64,
                    action_description,
                    action_type,
                    image_width,
                    image_height,
                )
                result = self._annotate_result_provenance(result, "ui_tars", attempted_providers)
                if self._is_usable_result(result):
                    result.confidence = self._calibrate_confidence(result.confidence, action_type)
                    self._set_cached(cache_key, result)
                    return result
                best_result = result
                logger.info("UI-TARS result below threshold, falling back to Qwen")
            except Exception as e:
                logger.warning("UI-TARS grounding failed, falling back to Qwen: %s", e)

        try:
            attempted_providers.append("qwen_local")
            result = await self._ground_action_qwen(
                screenshot_base64,
                action_description,
                action_type,
                image_width,
                image_height,
            )
            result = self._annotate_result_provenance(result, "qwen_local", attempted_providers)
            result.confidence = self._calibrate_confidence(result.confidence, action_type)
            if self._is_usable_result(result):
                self._set_cached(cache_key, result)
                return result
            best_result = result
        except Exception as e:
            logger.warning("Local grounding failed, trying fallback providers: %s", e)

        if self._remote_llm_available():
            try:
                attempted_providers.append("cloud_llm")
                result = await self._ground_action_cloud_llm(
                    screenshot_base64,
                    action_description,
                    action_type,
                    image_width,
                    image_height,
                )
                result = self._annotate_result_provenance(result, "cloud_llm", attempted_providers)
                result.confidence = self._calibrate_confidence(result.confidence, action_type)
                if self._is_usable_result(result):
                    self._set_cached(cache_key, result)
                    return result
                best_result = result if best_result is None or result.confidence > best_result.confidence else best_result
            except Exception as e:
                logger.warning("Cloud LLM grounding fallback failed: %s", e)

        final_result = best_result or GroundingResult(
            found=False,
            element_type=action_type,
            element_label=action_description,
            bbox_pixel=None,
            bbox_normalized=None,
            confidence=0.0,
        )
        if best_result is not None and getattr(final_result, "provider", None):
            final_result = self._annotate_result_provenance(
                final_result,
                getattr(final_result, "provider"),
                attempted_providers,
            )
        else:
            final_result.provider_chain = self._provider_chain()
            final_result.fallback_chain = self._provider_chain()[1:]
            final_result.attempted_providers = list(attempted_providers)
        self._set_cached(cache_key, final_result)
        return final_result

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

        current_results: list[GroundingResult] | None = None

        if self._should_use_ui_tars():
            try:
                adapter = await self._get_ui_tars()
                results = await adapter.ground_batch(
                    screenshot_base64,
                    actions,
                    image_width,
                    image_height,
                )
                for result in results:
                    result.provider = "ui_tars"
                all_above = all(self._is_usable_result(r) for r in results)
                if all_above:
                    return results
                current_results = results
                logger.info("UI-TARS batch results partially below threshold, falling back to additional providers")
            except Exception as e:
                logger.warning("UI-TARS batch grounding failed, falling back to Qwen: %s", e)

        try:
            qwen_results = await self._ground_batch_qwen(
                screenshot_base64,
                actions,
                image_width,
                image_height,
            )
            for result in qwen_results:
                result.provider = "qwen_local"
            if current_results is None:
                current_results = qwen_results
            else:
                current_results = [
                    existing if self._is_usable_result(existing) else replacement
                    for existing, replacement in zip(current_results, qwen_results, strict=False)
                ]
            if all(self._is_usable_result(r) for r in current_results):
                return current_results
        except Exception as e:
            logger.warning("Local batch grounding failed, trying fallback providers: %s", e)

        if self._remote_llm_available():
            try:
                cloud_results = await self._ground_batch_cloud_llm(
                    screenshot_base64,
                    actions,
                    image_width,
                    image_height,
                )
                if current_results is None:
                    return cloud_results
                merged_results = []
                for existing, fallback in zip(current_results, cloud_results, strict=False):
                    merged_results.append(existing if self._is_usable_result(existing) else fallback)
                return merged_results
            except Exception as e:
                logger.warning("Cloud LLM batch grounding fallback failed: %s", e)

        return current_results or [
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
        except Exception as exc:
            logger.warning("Local verify_action failed, falling back: %s", exc)
            if self._remote_llm_available():
                try:
                    return await self._verify_action_cloud_llm(before_base64, after_base64, action_desc)
                except Exception as fallback_exc:
                    logger.warning("Cloud LLM verify_action fallback failed: %s", fallback_exc)
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
