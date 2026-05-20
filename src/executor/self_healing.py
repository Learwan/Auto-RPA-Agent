from __future__ import annotations

import base64
import json
import logging
import time

from src.models.automation import LocateStrategy, StepTarget

logger = logging.getLogger(__name__)

HEALED_CACHE_TTL_S = 300
HEALED_CACHE_MAX_SIZE = 64


class SelfHealingLocator:
    """Wraps ElementLocator with LLM-powered recovery on failure.

    Implements the DOM accessibility tree approach (zero-cost self-healing,
    2026) with LLM fallback. Cache entries expire after HEALED_CACHE_TTL_S
    to prevent stale targets from persisting across UI changes.
    """

    def __init__(self, base_locator, adapter, llm_service=None):
        from src.executor.element_locator import ElementLocator
        from src.platform.base import BasePlatformAdapter

        self._base: ElementLocator = base_locator
        self._adapter: BasePlatformAdapter = adapter
        self._llm_service = llm_service
        self._healed_cache: dict[str, tuple[StepTarget, float]] = {}

    async def locate(self, target: StepTarget, step_id: str = ""):
        cached = self._get_cached(step_id)
        if cached is not None:
            result = await self._base.locate(cached)
            if result:
                logger.info("SelfHealingLocator: cache hit for step %s", step_id)
                return result
            self._evict(step_id)

        result = await self._base.locate(target)
        if result:
            return result

        if self._llm_service is None:
            return None

        logger.info("SelfHealingLocator: primary locate failed, attempting LLM recovery")

        suggested = await self._attempt_recovery(target)
        if suggested is None:
            return None

        result = await self._base.locate(suggested)
        if result:
            self._put_cached(step_id, suggested)
            logger.info(
                "SelfHealingLocator: healed target for step %s via LLM suggestion",
                step_id,
            )
            return result

        return None

    def _get_cached(self, step_id: str) -> StepTarget | None:
        if not step_id or step_id not in self._healed_cache:
            return None
        target, ts = self._healed_cache[step_id]
        if time.time() - ts > HEALED_CACHE_TTL_S:
            del self._healed_cache[step_id]
            return None
        return target

    def _put_cached(self, step_id: str, target: StepTarget) -> None:
        if not step_id:
            return
        if len(self._healed_cache) >= HEALED_CACHE_MAX_SIZE:
            oldest_key = min(self._healed_cache, key=lambda k: self._healed_cache[k][1])
            del self._healed_cache[oldest_key]
        self._healed_cache[step_id] = (target, time.time())

    def _evict(self, step_id: str) -> None:
        self._healed_cache.pop(step_id, None)

    async def _attempt_recovery(self, target: StepTarget) -> StepTarget | None:
        failure_context = await self._build_failure_context(target)
        if not failure_context:
            return None
        return await self._llm_suggest_locator(failure_context)

    async def _build_failure_context(self, target: StepTarget) -> str:
        parts: list[str] = []
        parts.append("=== 元素定位失败上下文 ===")
        parts.append(f"目标描述: {self._describe_target(target)}")
        parts.append(f"原始策略: {target.strategy.value}")

        try:
            screenshot_bytes = await self._adapter.capture_screen()
            if screenshot_bytes:
                b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                parts.append(f"当前屏幕截图 (base64, 长度={len(b64)})")
        except Exception as e:
            parts.append(f"截图获取失败: {e}")

        try:
            snapshot = await self._get_accessibility_snapshot()
            if snapshot:
                parts.append(f"可访问性快照:\n{snapshot[:3000]}")
        except Exception as e:
            parts.append(f"可访问性快照获取失败: {e}")

        return "\n".join(parts)

    async def _get_accessibility_snapshot(self) -> str | None:
        for method_name in ("get_accessibility_snapshot", "get_accessibility_tree", "get_ui_tree"):
            getter = getattr(self._adapter, method_name, None)
            if not callable(getter):
                continue
            try:
                snapshot = await getter()
                if snapshot:
                    if isinstance(snapshot, (dict, list)):
                        return json.dumps(snapshot, ensure_ascii=False, indent=2)
                    return str(snapshot)
            except Exception:
                continue
        return None

    async def _llm_suggest_locator(self, failure_context: str) -> StepTarget | None:
        try:
            prompt = (
                "你是一个UI自动化修复专家。元素定位失败了，请根据以下上下文信息，"
                "建议一个新的定位策略。\n\n"
                f"{failure_context}\n\n"
                "请严格输出JSON格式：\n"
                '{"strategy": "css_selector|xpath|text_match|accessibility_id", '
                '"selector": "CSS选择器(如适用)", '
                '"xpath": "XPath(如适用)", '
                '"text_contains": "文本匹配(如适用)", '
                '"title": "标题(如适用)", '
                '"accessibility_id": "可访问性ID(如适用)"}\n'
                "仅输出JSON。"
            )

            response = await self._llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
            )

            text = response if isinstance(response, str) else getattr(response, "text", str(response))
            return self._parse_suggestion(text)

        except Exception as e:
            logger.debug("SelfHealingLocator LLM suggestion failed: %s", e)
            return None

    @staticmethod
    def _parse_suggestion(text: str) -> StepTarget | None:
        import re

        data = None
        for match in re.finditer(r"\{", text):
            start = match.start()
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(text[start : i + 1])
                            break
                        except json.JSONDecodeError:
                            break
            if data is not None:
                break

        if not isinstance(data, dict):
            return None

        strategy_str = data.get("strategy", "text_match")
        strategy_map = {
            "css_selector": LocateStrategy.CSS_SELECTOR,
            "xpath": LocateStrategy.XPATH,
            "text_match": LocateStrategy.TEXT_MATCH,
            "accessibility_id": LocateStrategy.ACCESSIBILITY_ID,
            "image_match": LocateStrategy.IMAGE_MATCH,
        }
        strategy = strategy_map.get(strategy_str, LocateStrategy.TEXT_MATCH)

        return StepTarget(
            strategy=strategy,
            selector=data.get("selector"),
            xpath=data.get("xpath"),
            text_contains=data.get("text_contains"),
            title=data.get("title"),
            accessibility_id=data.get("accessibility_id"),
            window_title=data.get("window_title"),
        )

    @staticmethod
    def _describe_target(target: StepTarget) -> str:
        parts: list[str] = []
        if target.title:
            parts.append(f"title='{target.title}'")
        if target.role:
            parts.append(f"role='{target.role}'")
        if target.accessibility_id:
            parts.append(f"id='{target.accessibility_id}'")
        if target.selector:
            parts.append(f"selector='{target.selector}'")
        if target.xpath:
            parts.append(f"xpath='{target.xpath}'")
        if target.text_contains:
            parts.append(f"text='{target.text_contains}'")
        if target.position:
            parts.append(f"pos=({target.position.x},{target.position.y})")
        return ", ".join(parts) if parts else "(no target info)"
