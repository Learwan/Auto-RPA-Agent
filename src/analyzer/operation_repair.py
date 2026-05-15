from __future__ import annotations

import base64
import contextlib
import io
import logging
from typing import Any

from src.collector.snapshot import DesktopSnapshot, SnapshotManager, get_snapshot_manager
from src.collector.visual_targeting import build_click_node_suggestion, is_meaningful_text
from src.executor.multimodal_locator import OCREngine, OCRResult
from src.llm.multimodal_service import MultimodalLLMService
from src.models.desktop import Rect, UIElement
from src.models.operation import MouseAction, MouseEventData, OperationContext, OperationEvent, OperationType

logger = logging.getLogger(__name__)
MAX_SNAPSHOT_MATCH_WINDOW_MS = 8000


class OperationRepairService:
    def __init__(
        self,
        snapshot_manager: SnapshotManager | None = None,
        vision_service: MultimodalLLMService | None = None,
        ocr_engine: OCREngine | None = None,
    ) -> None:
        self._snapshot_manager = snapshot_manager or get_snapshot_manager()
        self._vision_service = vision_service or MultimodalLLMService()
        self._ocr_engine = ocr_engine or OCREngine()
        self._vision_cache: dict[str, dict[str, Any]] = {}
        self._ocr_cache: dict[str, dict[str, Any]] = {}
        self._session_snapshot_cache: dict[str, list[DesktopSnapshot]] = {}

    async def repair_operations(self, operations: list[OperationEvent]) -> list[OperationEvent]:
        repaired: list[OperationEvent] = []
        for operation in operations:
            repaired.append(await self._repair_operation(operation))
        return repaired

    async def _repair_operation(self, operation: OperationEvent) -> OperationEvent:
        if not self._should_repair(operation):
            return operation

        snapshot_id = self._select_snapshot_id(operation)
        if not snapshot_id:
            return operation

        screenshot_bytes = self._snapshot_manager.get_screenshot_bytes(snapshot_id)
        if not screenshot_bytes:
            return operation

        vision_payload = await self._get_vision_payload(snapshot_id, screenshot_bytes, operation)
        ocr_payload = await self._get_ocr_payload(snapshot_id, screenshot_bytes)
        active_window = operation.context.active_window.model_dump(mode="json") if operation.context and operation.context.active_window else None

        node_suggestion = build_click_node_suggestion(
            click_x=operation.data.x,
            click_y=operation.data.y,
            active_window=active_window,
            vision_payload=vision_payload,
            ocr_payload=ocr_payload,
            snapshot_id=snapshot_id,
        )
        if not node_suggestion:
            return operation

        return self._merge_repair(operation, node_suggestion)

    def _should_repair(self, operation: OperationEvent) -> bool:
        if operation.type != OperationType.MOUSE_CLICK or not isinstance(operation.data, MouseEventData):
            return False
        if operation.data.action not in {MouseAction.CLICK, MouseAction.DOUBLE_CLICK, MouseAction.RIGHT_CLICK}:
            return False
        context = operation.context
        if context is None:
            return False
        if self._is_web_context(context):
            return False
        if self._has_strong_context_target(context):
            return False
        return True

    @staticmethod
    def _extract_snapshot_id(context: OperationContext | None) -> str | None:
        screenshot_path = getattr(context, "screenshot_path", None)
        if not screenshot_path or not screenshot_path.startswith("snapshot://"):
            return None
        snapshot_id = screenshot_path.removeprefix("snapshot://").strip()
        return snapshot_id or None

    def _select_snapshot_id(self, operation: OperationEvent) -> str | None:
        snapshot_id = self._extract_snapshot_id(operation.context)
        if snapshot_id:
            return snapshot_id

        session_id = operation.session_id
        if not session_id:
            return None

        snapshots = self._session_snapshot_cache.get(session_id)
        if snapshots is None:
            snapshots = self._snapshot_manager.list_snapshots(session_id=session_id, limit=500)
            self._session_snapshot_cache[session_id] = snapshots
        if not snapshots:
            return None

        current_window = operation.context.active_window if operation.context else None
        best_score = float("-inf")
        best_snapshot_id: str | None = None

        for snapshot in snapshots:
            delta_ms = abs(int(operation.timestamp) - int(snapshot.timestamp))
            if delta_ms > MAX_SNAPSHOT_MATCH_WINDOW_MS:
                continue

            score = -delta_ms / 1000.0
            snap_window = snapshot.state.active_window
            if current_window and snap_window:
                if _normalized_match(current_window.app_name, snap_window.app_name):
                    score += 1.6
                if _normalized_match(current_window.title, snap_window.title):
                    score += 1.8
            if snapshot.state.focused_element is not None:
                score += 0.15

            if score > best_score:
                best_score = score
                best_snapshot_id = snapshot.id

        return best_snapshot_id

    @staticmethod
    def _is_web_context(context: OperationContext) -> bool:
        active_window = context.active_window
        element = context.focused_element
        return bool(
            context.platform == "web"
            or (active_window and (active_window.url or active_window.browser_type))
            or (element and (element.selector or element.xpath or element.url or element.frame))
        )

    @staticmethod
    def _has_strong_context_target(context: OperationContext) -> bool:
        node_suggestion = context.node_suggestion or {}
        target = node_suggestion.get("target") or {}
        strategy = str(target.get("strategy") or "")
        if strategy in {"accessibility_id", "css_selector", "xpath"}:
            return True
        if strategy == "text_match" and is_meaningful_text(target.get("text_contains") or target.get("title")):
            return True

        element = context.focused_element
        if element is None:
            return False
        return bool(
            element.identifier
            or element.selector
            or element.xpath
            or is_meaningful_text(element.title)
            or is_meaningful_text(element.value)
            or is_meaningful_text(element.functional_label)
            or is_meaningful_text(element.description)
        )

    async def _get_vision_payload(
        self,
        snapshot_id: str,
        screenshot_bytes: bytes,
        operation: OperationEvent,
    ) -> dict[str, Any]:
        cache_key = f"{snapshot_id}:{operation.data.x}:{operation.data.y}"
        cached = self._vision_cache.get(cache_key)
        if cached is not None:
            return cached

        payload: dict[str, Any] = {"available": False, "actionable_elements": []}
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        prompt = (
            "请分析这张桌面截图中的可交互元素，并重点关注点击位置附近的候选目标。"
            f"用户点击坐标约为 ({operation.data.x}, {operation.data.y})，"
            "请尽量给出 actionable_elements 的准确标签、描述和 bbox。"
        )
        try:
            analysis = await self._vision_service.analyze_screenshot(screenshot_base64, task_description=prompt)
            payload = self._serialize_vision_analysis(analysis, source="local_vlm")
            if not payload["actionable_elements"]:
                crop_bytes, offset = self._crop_click_region(screenshot_bytes, operation.data.x, operation.data.y)
                if crop_bytes is not None and offset is not None:
                    crop_analysis = await self._vision_service.analyze_screenshot(
                        base64.b64encode(crop_bytes).decode("utf-8"),
                        task_description=(
                            "这是一张用户点击附近的局部桌面截图，点击点位于图像中心附近。"
                            "请重点识别局部区域里的可交互元素，并返回准确标签、描述和 bbox。"
                        ),
                    )
                    cropped_payload = self._serialize_vision_analysis(crop_analysis, source="local_vlm_crop")
                    payload = self._translate_vision_payload(
                        cropped_payload,
                        offset_x=offset[0],
                        offset_y=offset[1],
                    )
        except Exception as exc:
            logger.debug("Visual repair vision analysis failed for %s: %s", snapshot_id, exc)

        self._vision_cache[cache_key] = payload
        return payload

    @staticmethod
    def _serialize_vision_analysis(analysis, *, source: str) -> dict[str, Any]:
        return {
            "available": True,
            "source": source,
            "app_name": analysis.app_name,
            "window_title": analysis.window_title,
            "ui_state_description": analysis.ui_state_description,
            "actionable_elements": [
                {
                    "element_type": item.element_type,
                    "label": item.label,
                    "description": item.description,
                    "bbox": list(item.bbox) if item.bbox else None,
                    "confidence": item.confidence,
                    "actionable": item.actionable,
                    "suggested_action": item.suggested_action,
                }
                for item in analysis.actionable_elements
            ],
            "user_intent": analysis.user_intent,
            "suggested_next_actions": analysis.suggested_next_actions,
            "confidence": analysis.confidence,
            "thinking_trace": analysis.thinking_trace,
        }

    @staticmethod
    def _translate_vision_payload(
        payload: dict[str, Any],
        *,
        offset_x: int,
        offset_y: int,
    ) -> dict[str, Any]:
        translated = dict(payload)
        translated["actionable_elements"] = []
        for item in payload.get("actionable_elements") or []:
            translated_item = dict(item)
            bbox = translated_item.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                translated_item["bbox"] = [
                    int(bbox[0]) + offset_x,
                    int(bbox[1]) + offset_y,
                    int(bbox[2]),
                    int(bbox[3]),
                ]
            translated["actionable_elements"].append(translated_item)
        return translated

    @staticmethod
    def _crop_click_region(
        screenshot_bytes: bytes,
        click_x: int,
        click_y: int,
        *,
        width: int = 520,
        height: int = 340,
    ) -> tuple[bytes | None, tuple[int, int] | None]:
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(screenshot_bytes))
            image_width, image_height = image.size
            left = max(int(click_x - width // 2), 0)
            top = max(int(click_y - height // 2), 0)
            right = min(left + width, image_width)
            bottom = min(top + height, image_height)
            left = max(right - width, 0)
            top = max(bottom - height, 0)
            cropped = image.crop((left, top, right, bottom))
            output = io.BytesIO()
            cropped.save(output, format="PNG")
            return output.getvalue(), (left, top)
        except Exception as exc:
            logger.debug("Visual repair crop failed: %s", exc)
            return None, None

    async def _get_ocr_payload(self, snapshot_id: str, screenshot_bytes: bytes) -> dict[str, Any]:
        cached = self._ocr_cache.get(snapshot_id)
        if cached is not None:
            return cached

        payload = {"count": 0, "items": [], "text_preview": ""}
        try:
            results = await self._ocr_engine.detect_text(screenshot_bytes)
            items = [self._serialize_ocr_result(result) for result in results[:32]]
            payload = {
                "count": len(items),
                "items": items,
                "text_preview": " | ".join(item["text"] for item in items[:8] if item.get("text")),
            }
        except Exception as exc:
            logger.debug("Visual repair OCR failed for %s: %s", snapshot_id, exc)

        self._ocr_cache[snapshot_id] = payload
        return payload

    def _merge_repair(self, operation: OperationEvent, node_suggestion: dict[str, Any]) -> OperationEvent:
        context = operation.context or OperationContext()
        repaired_element = self._build_focus_element(node_suggestion)
        merged_element = self._merge_ui_element(context.focused_element, repaired_element)

        updates: dict[str, Any] = {"node_suggestion": node_suggestion}
        if merged_element is not None:
            updates["focused_element"] = merged_element
        if not context.screenshot_path and node_suggestion.get("snapshot_id"):
            updates["screenshot_path"] = f"snapshot://{node_suggestion['snapshot_id']}"
        merged_context = context.model_copy(update=updates)
        return operation.model_copy(update={"context": merged_context})

    @staticmethod
    def _build_focus_element(node_suggestion: dict[str, Any]) -> UIElement | None:
        target = (node_suggestion or {}).get("target") or {}
        if not isinstance(target, dict) or not target:
            return None

        title = target.get("title") or target.get("text_contains") or target.get("text_preview")
        role = target.get("role") or ((target.get("vision_element") or {}).get("element_type")) or "generic"
        if not title and not target.get("description") and not target.get("accessibility_id"):
            return None

        payload: dict[str, Any] = {
            "role": role,
            "title": title,
            "value": target.get("text_preview") or title,
            "identifier": target.get("accessibility_id"),
            "description": target.get("description"),
            "functional_label": title,
            "class_name": target.get("class_name"),
            "selector": target.get("selector"),
            "xpath": target.get("xpath"),
            "url": target.get("url"),
            "frame": target.get("frame"),
            "is_enabled": True,
            "is_focused": True,
        }
        bounds = target.get("bounds")
        if isinstance(bounds, dict):
            with contextlib.suppress(Exception):
                payload["bounds"] = Rect.model_validate(bounds)
        with contextlib.suppress(Exception):
            return UIElement.model_validate(payload)
        return None

    @staticmethod
    def _merge_ui_element(current: UIElement | None, enriched: UIElement | None) -> UIElement | None:
        if enriched is None:
            return current
        if current is None:
            return enriched

        updates: dict[str, Any] = {}
        for field in (
            "title",
            "value",
            "identifier",
            "description",
            "functional_label",
            "class_name",
            "selector",
            "xpath",
            "url",
            "frame",
            "bounds",
            "input_type",
            "tag_name",
        ):
            current_value = getattr(current, field, None)
            enriched_value = getattr(enriched, field, None)
            if current_value in (None, "", 0) and enriched_value not in (None, "", 0):
                updates[field] = enriched_value

        if not current.is_focused and enriched.is_focused:
            updates["is_focused"] = True
        if not current.is_enabled and enriched.is_enabled:
            updates["is_enabled"] = True

        return current.model_copy(update=updates) if updates else current

    @staticmethod
    def _serialize_ocr_result(result: OCRResult) -> dict[str, Any]:
        return {
            "text": result.text,
            "confidence": result.confidence,
            "center": {"x": result.center_x, "y": result.center_y},
            "bbox": result.bbox,
        }


def _normalized_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    left_normalized = left.strip().lower()
    right_normalized = right.strip().lower()
    return (
        left_normalized == right_normalized
        or left_normalized in right_normalized
        or right_normalized in left_normalized
    )
