from __future__ import annotations

import base64
import logging
from typing import Any

from src.models.automation import LocateStrategy, StepTarget
from src.models.desktop import Point, Rect, UIElement

logger = logging.getLogger(__name__)


class VisionLocator:
    """Screenshot -> LLM/OmniParser -> structured UI elements -> match target."""

    def __init__(self, adapter, grounding_engine=None):
        from src.platform.base import BasePlatformAdapter

        self._adapter: BasePlatformAdapter = adapter
        self._grounding_engine = grounding_engine

    def _grounding_runtime_status(self) -> dict[str, Any]:
        if self._grounding_engine is None or not hasattr(self._grounding_engine, "get_runtime_status"):
            return {}
        try:
            status = self._grounding_engine.get_runtime_status()
        except Exception:
            return {}
        return status if isinstance(status, dict) else {}

    async def locate(self, target: StepTarget):
        from src.executor.element_locator import LocatedElement

        if self._grounding_engine is None:
            return None

        try:
            screenshot_bytes = await self._adapter.capture_screen()
            if not screenshot_bytes:
                return None

            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            action_desc = self._build_description(target)
            screen_w, screen_h = await self._adapter.get_screen_size()

            result = await self._grounding_engine.ground_action(
                screenshot_base64=screenshot_b64,
                action_description=action_desc,
                action_type="click",
                image_width=screen_w,
                image_height=screen_h,
            )

            if not result or not result.found or result.confidence < 0.5:
                return None

            bbox = getattr(result, "bbox_pixel", None)
            if bbox and len(bbox) >= 2:
                center_x = int(bbox[0])
                center_y = int(bbox[1])
            else:
                return None

            if center_x <= 0 or center_y <= 0:
                return None

            runtime_status = self._grounding_runtime_status()
            element = UIElement(
                role="vision_match",
                title=f"Vision: {action_desc[:50]}",
                bounds=Rect(
                    x=center_x - 10,
                    y=center_y - 10,
                    width=20,
                    height=20,
                ) if bbox and len(bbox) >= 4 else None,
            )

            located = LocatedElement(
                element=element,
                strategy_used=LocateStrategy.IMAGE_MATCH,
                position=Point(x=center_x, y=center_y),
                confidence=result.confidence * 0.9,
                provider=getattr(result, "provider", None),
                provider_chain=getattr(result, "provider_chain", None) or runtime_status.get("provider_chain"),
                fallback_chain=getattr(result, "fallback_chain", None) or runtime_status.get("fallback_chain"),
                attempted_providers=getattr(result, "attempted_providers", None),
                healed=True,
            )
            logger.info(
                "VisionLocator found target at (%d, %d) confidence=%.2f",
                center_x,
                center_y,
                located.confidence,
            )
            return located

        except Exception as e:
            logger.debug("VisionLocator.locate failed: %s", e)
            return None

    async def parse_screen(self) -> list[UIElement]:
        """Parse entire screen into UI elements using vision."""
        if self._grounding_engine is None:
            return []

        try:
            screenshot_bytes = await self._adapter.capture_screen()
            if not screenshot_bytes:
                return []

            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            screen_w, screen_h = await self._adapter.get_screen_size()

            common_actions = [
                {"type": "button", "description": "页面上所有可点击的按钮"},
                {"type": "input", "description": "页面上所有输入框"},
                {"type": "link", "description": "页面上所有链接"},
            ]

            results = await self._grounding_engine.ground_batch(
                screenshot_base64=screenshot_b64,
                actions=common_actions,
                image_width=screen_w,
                image_height=screen_h,
            )

            elements: list[UIElement] = []
            for result in results:
                if not result.found:
                    continue
                bbox = getattr(result, "bbox_pixel", None)
                bounds = None
                if bbox and len(bbox) >= 4:
                    bounds = Rect(
                        x=int(bbox[0]) - int(bbox[2]) // 2,
                        y=int(bbox[1]) - int(bbox[3]) // 2,
                        width=int(bbox[2]),
                        height=int(bbox[3]),
                    )
                elements.append(
                    UIElement(
                        role=result.element_type or "unknown",
                        title=result.element_label or None,
                        bounds=bounds,
                    )
                )
            return elements

        except Exception as e:
            logger.debug("VisionLocator.parse_screen failed: %s", e)
            return []

    @staticmethod
    def _build_description(target: StepTarget) -> str:
        parts: list[str] = []
        if target.title:
            parts.append(f"标题为'{target.title}'")
        if target.role:
            parts.append(f"角色为'{target.role}'")
        if target.accessibility_id:
            parts.append(f"ID为'{target.accessibility_id}'")
        if target.selector:
            parts.append(f"选择器'{target.selector}'")
        if target.text_contains:
            parts.append(f"包含文本'{target.text_contains}'")
        if target.position:
            parts.append(f"位置({target.position.x},{target.position.y})附近")
        return "点击" + "，".join(parts) if parts else "点击目标元素"
