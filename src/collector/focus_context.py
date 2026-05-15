from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from src.collector.snapshot import SnapshotManager, get_snapshot_manager
from src.executor.multimodal_locator import OCREngine, OCRResult
from src.llm.multimodal_service import MultimodalLLMService, VisionError
from src.llm.service import LLMService
from src.models.automation import LocateStrategy, StepType
from src.models.desktop import DesktopState, UIElement, WindowInfo
from src.models.vision import ScreenshotAnalysis

logger = logging.getLogger(__name__)

_GUIDANCE_TIMEOUT_SECONDS = 8.0

_INPUT_ROLES = {
    "combobox",
    "input",
    "searchfield",
    "textfield",
    "textbox",
    "textarea",
}

_SENSITIVE_ELEMENT_KEYWORDS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "otp",
    "pin",
    "验证码",
    "密码",
    "口令",
    "安全码",
}


def _stringify_text(value: Any | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _truncate_text(value: str, limit: int = 48) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _element_is_sensitive(element: UIElement | None) -> bool:
    if element is None:
        return False
    haystack = " ".join(
        part.casefold()
        for part in (
            _stringify_text(element.role),
            _stringify_text(element.title),
            _stringify_text(element.identifier),
            _stringify_text(element.description),
            _stringify_text(element.functional_label),
            _stringify_text(element.class_name),
            _stringify_text(element.input_type),
            _stringify_text(element.tag_name),
        )
        if part
    )
    return any(keyword in haystack for keyword in _SENSITIVE_ELEMENT_KEYWORDS)


def _element_text_preview(element: UIElement | None, *, allow_redacted: bool = True) -> str | None:
    if element is None:
        return None
    preview = _stringify_text(element.value)
    if not preview:
        return None
    if _element_is_sensitive(element):
        return "文本已脱敏" if allow_redacted else None
    return _truncate_text(preview)


def _focused_element_details(element: UIElement | None) -> dict[str, Any] | None:
    if element is None:
        return None
    return {
        "label": _element_label(element),
        "role": element.role or None,
        "title": element.title,
        "text_preview": _element_text_preview(element),
        "identifier": element.identifier,
        "description": element.description,
        "functional_label": element.functional_label,
        "class_name": element.class_name,
        "is_enabled": element.is_enabled,
        "is_focused": element.is_focused,
    }


def _window_label(window: WindowInfo | None) -> str:
    if window is None:
        return "未识别窗口"
    title = (window.title or "").strip()
    app_name = (window.app_name or "").strip()
    if title and app_name:
        return f"{app_name} · {title}"
    return title or app_name or "未识别窗口"


def _element_label(element: UIElement | None) -> str:
    if element is None:
        return "未识别元素"
    for candidate in (
        element.functional_label,
        element.title,
        element.description,
        _element_text_preview(element),
        element.identifier,
    ):
        if candidate:
            return str(candidate).strip()
    return (element.role or "未识别元素").strip() or "未识别元素"


def _normalize_text(value: Any | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().casefold().split())


class FocusContextService:
    def __init__(
        self,
        snapshot_manager: SnapshotManager | None = None,
        vision_service: Any | None = None,
        ocr_engine: OCREngine | None = None,
        llm_service: Any | None = None,
    ) -> None:
        self._snapshot_manager = snapshot_manager or get_snapshot_manager()
        self._vision_service = vision_service or MultimodalLLMService()
        self._ocr_engine = ocr_engine or OCREngine()
        self._llm = llm_service or LLMService()

    async def capture(
        self,
        session_id: str | None = None,
        *,
        include_vision: bool = True,
        include_ocr: bool = True,
        include_llm: bool = True,
        include_screenshot_base64: bool = False,
    ) -> dict[str, Any]:
        snapshot = await self._snapshot_manager.take_snapshot(session_id=session_id)
        screenshot_bytes = self._snapshot_manager.get_screenshot_bytes(snapshot.id) or b""
        screenshot_base64 = (
            base64.b64encode(screenshot_bytes).decode("utf-8")
            if screenshot_bytes
            else None
        )

        ocr_payload = await self._collect_ocr(screenshot_bytes, include_ocr)
        vision_payload = await self._analyze_vision(
            snapshot.state,
            screenshot_base64,
            include_vision,
            ocr_payload,
        )
        node_suggestion = self._build_node_suggestion(snapshot.state, vision_payload, ocr_payload)
        recording_guidance = await self._build_recording_guidance(
            snapshot.state,
            vision_payload,
            ocr_payload,
            node_suggestion,
            include_llm,
        )
        focus_summary = self._build_focus_summary(
            snapshot.state,
            vision_payload,
            ocr_payload,
            node_suggestion,
            recording_guidance,
        )

        payload: dict[str, Any] = {
            "session_id": session_id,
            "snapshot_id": snapshot.id,
            "timestamp": snapshot.timestamp,
            "state": snapshot.state.model_dump(mode="json"),
            "window_label": _window_label(snapshot.state.active_window),
            "focused_label": _element_label(snapshot.state.focused_element),
            "focused_details": _focused_element_details(snapshot.state.focused_element),
            "vision": vision_payload,
            "ocr": ocr_payload,
            "node_suggestion": node_suggestion,
            "recording_guidance": recording_guidance,
            "focus_summary": focus_summary,
            "llm_available": bool(getattr(self._llm, "is_configured", False)),
        }
        if include_screenshot_base64:
            payload["screenshot_base64"] = screenshot_base64
        return payload

    async def _analyze_vision(
        self,
        state: DesktopState,
        screenshot_base64: str | None,
        include_vision: bool,
        ocr_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not include_vision or not screenshot_base64:
            return None
        error_message: str | None = None
        try:
            analysis = await self._vision_service.analyze_screenshot(
                screenshot_base64,
                task_description=self._build_vision_prompt(state),
            )
        except VisionError as exc:
            logger.warning("Focus context vision analysis failed: %s", exc)
            error_message = str(exc)
        except Exception as exc:
            logger.warning("Focus context vision analysis crashed: %s", exc)
            error_message = str(exc)
        else:
            return self._serialize_vision_analysis(analysis)

        llm_fallback = await self._analyze_vision_with_llm_fallback(
            state,
            screenshot_base64,
            ocr_payload,
            error_message,
        )
        if llm_fallback is not None:
            return llm_fallback

        return self._build_context_fallback_vision(state, ocr_payload, error_message)

    async def _analyze_vision_with_llm_fallback(
        self,
        state: DesktopState,
        screenshot_base64: str,
        ocr_payload: dict[str, Any],
        error_message: str | None,
    ) -> dict[str, Any] | None:
        if not getattr(self._llm, "is_configured", False):
            return None

        try:
            response = await self._llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是 GUI 截图分析助手。请根据截图和焦点上下文输出严格 JSON。"
                            "JSON 结构必须包含 app_name、window_title、ui_state_description、"
                            "actionable_elements、user_intent、suggested_next_actions、confidence、thinking_trace。"
                            "actionable_elements 中每项都要包含 element_type、label、description、bbox、confidence、actionable、suggested_action。"
                            "如果无法完全确定，也要给出保守推断，不要输出解释性文字。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"当前窗口: {_window_label(state.active_window)}\n"
                                    f"当前焦点元素: {_element_label(state.focused_element)}\n"
                                    f"当前焦点文本: {_element_text_preview(state.focused_element) or '无'}\n"
                                    f"OCR 摘要: {ocr_payload.get('text_preview', '') or '无'}\n"
                                    "请输出当前界面状态、可交互元素、用户意图和建议下一步。"
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
        except Exception as exc:
            logger.warning("Focus context cloud vision fallback failed: %s", exc)
            return None

        parsed = self._parse_llm_json(response)
        if not isinstance(parsed, dict):
            return None
        return self._normalize_llm_fallback_vision(parsed, state, error_message)

    def _build_context_fallback_vision(
        self,
        state: DesktopState,
        ocr_payload: dict[str, Any],
        error_message: str | None,
    ) -> dict[str, Any]:
        element = state.focused_element
        role = (element.role or "").lower() if element else ""
        focused_label = _element_label(element)
        if element is not None and role in _INPUT_ROLES:
            user_intent = f"用户可能正在编辑 {focused_label} 的内容"
            suggested_next_actions = [
                f"确认输入目标“{focused_label}”可编辑",
                "输入完成后检查字段值是否已更新",
            ]
            actionable_elements = [self._serialize_focused_element(element, "type")]
        elif element is not None:
            user_intent = f"用户可能准备与 {focused_label} 交互"
            suggested_next_actions = [
                f"确认目标元素“{focused_label}”是否就是当前要记录的节点",
                "执行操作后补一次焦点采集，确认结果状态",
            ]
            actionable_elements = [self._serialize_focused_element(element, "click")]
        elif ocr_payload.get("text_preview"):
            user_intent = "用户可能正在查看当前界面的文本信息并确认状态"
            suggested_next_actions = [
                "根据 OCR 文本确认当前界面是否已经到达目标状态",
                "如果需要稳定节点，请再聚焦一次具体目标元素",
            ]
            actionable_elements = []
        else:
            user_intent = "用户可能正在确认当前界面状态"
            suggested_next_actions = [
                "先获取一个明确的焦点元素，再记录交互节点",
                "如果界面信息不足，建议切到目标窗口后重新采集",
            ]
            actionable_elements = []

        ui_state_description = (
            f"当前位于 {_window_label(state.active_window)}，"
            f"焦点元素为 {focused_label}。"
        )
        text_preview = _element_text_preview(element)
        if text_preview:
            ui_state_description += f" 焦点文本预览：{text_preview}。"
        if ocr_payload.get("text_preview"):
            ui_state_description += f" OCR 摘要包含：{ocr_payload['text_preview']}。"
        if error_message:
            ui_state_description += " 本地视觉模型当前不可用，已退回桌面上下文与 OCR 推断。"

        confidence = 0.4 if element is not None else 0.32
        return {
            "available": False,
            "source": "context_fallback",
            "error": error_message,
            "app_name": state.active_window.app_name if state.active_window else "unknown",
            "window_title": state.active_window.title if state.active_window else "unknown",
            "ui_state_description": ui_state_description,
            "actionable_elements": actionable_elements,
            "user_intent": user_intent,
            "suggested_next_actions": suggested_next_actions,
            "confidence": confidence,
            "thinking_trace": "fallback_from_desktop_state_and_ocr",
        }

    def _normalize_llm_fallback_vision(
        self,
        parsed: dict[str, Any],
        state: DesktopState,
        error_message: str | None,
    ) -> dict[str, Any]:
        actionable_elements = []
        for item in parsed.get("actionable_elements") or []:
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox")
            actionable_elements.append(
                {
                    "element_type": str(item.get("element_type") or "unknown"),
                    "label": str(item.get("label") or ""),
                    "description": str(item.get("description") or ""),
                    "bbox": list(bbox) if isinstance(bbox, (list, tuple)) else None,
                    "confidence": self._to_float(item.get("confidence"), 0.5),
                    "actionable": bool(item.get("actionable", True)),
                    "suggested_action": item.get("suggested_action"),
                }
            )

        suggested_next_actions = [
            str(item).strip()
            for item in (parsed.get("suggested_next_actions") or [])
            if str(item).strip()
        ]
        return {
            "available": True,
            "source": "cloud_llm_fallback",
            "error": error_message,
            "app_name": str(parsed.get("app_name") or (state.active_window.app_name if state.active_window else "unknown")),
            "window_title": str(parsed.get("window_title") or (state.active_window.title if state.active_window else "unknown")),
            "ui_state_description": str(parsed.get("ui_state_description") or ""),
            "actionable_elements": actionable_elements,
            "user_intent": str(parsed.get("user_intent") or ""),
            "suggested_next_actions": suggested_next_actions,
            "confidence": self._to_float(parsed.get("confidence"), 0.5),
            "thinking_trace": parsed.get("thinking_trace"),
        }

    def _serialize_focused_element(self, element: UIElement, suggested_action: str) -> dict[str, Any]:
        bbox = None
        if element.bounds is not None:
            bbox = [
                element.bounds.x + max(element.bounds.width // 2, 0),
                element.bounds.y + max(element.bounds.height // 2, 0),
                element.bounds.width,
                element.bounds.height,
            ]
        return {
            "element_type": element.role or "unknown",
            "label": _element_label(element),
            "description": element.description or "当前焦点元素",
            "text_preview": _element_text_preview(element),
            "identifier": element.identifier,
            "bbox": bbox,
            "confidence": 0.55,
            "actionable": True,
            "suggested_action": suggested_action,
        }

    def _parse_llm_json(self, response: str) -> dict[str, Any] | None:
        extractor = getattr(self._llm, "_extract_json", None)
        if callable(extractor):
            parsed = extractor(response)
            return parsed if isinstance(parsed, dict) else None
        try:
            parsed = json.loads(response)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    async def _collect_ocr(self, screenshot_bytes: bytes, include_ocr: bool) -> dict[str, Any]:
        if not include_ocr or not screenshot_bytes:
            return {
                "count": 0,
                "items": [],
                "text_preview": "",
            }

        try:
            results = await self._ocr_engine.detect_text(screenshot_bytes)
        except Exception as exc:
            logger.warning("Focus context OCR failed: %s", exc)
            return {
                "count": 0,
                "items": [],
                "text_preview": "",
                "error": str(exc),
            }

        items = [self._serialize_ocr_result(result) for result in results[:24]]
        text_preview = " | ".join(item["text"] for item in items[:8] if item.get("text"))
        return {
            "count": len(items),
            "items": items,
            "text_preview": text_preview,
        }

    async def _build_recording_guidance(
        self,
        state: DesktopState,
        vision_payload: dict[str, Any] | None,
        ocr_payload: dict[str, Any],
        node_suggestion: dict[str, Any],
        include_llm: bool,
    ) -> str | None:
        if not include_llm or not getattr(self._llm, "is_configured", False):
            return None

        context_payload = {
            "window": _window_label(state.active_window),
            "focused": _element_label(state.focused_element),
            "focused_text": _element_text_preview(state.focused_element),
            "vision": vision_payload,
            "ocr": {
                "count": ocr_payload.get("count", 0),
                "text_preview": ocr_payload.get("text_preview", ""),
            },
            "node_suggestion": node_suggestion,
        }

        try:
            return await asyncio.wait_for(
                self._llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是桌面录制辅助分析师。请根据当前焦点上下文，"
                                "给出简洁中文建议，帮助用户确认如何记录当前步骤，并提高后续自动化节点的置信度。"
                                "重点覆盖：记录动作、变量候选、前置检查、结果检查、风险点。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(context_payload, ensure_ascii=False),
                        },
                    ],
                    temperature=0.2,
                    top_p=0.9,
                    max_tokens=700,
                    stream=False,
                ),
                timeout=_GUIDANCE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("Focus context guidance generation failed: %s", exc)
            return None

    def _build_vision_prompt(self, state: DesktopState) -> str:
        focus_text = _element_text_preview(state.focused_element)
        return (
            "请聚焦分析当前桌面焦点上下文，输出窗口状态、主要可交互元素、用户当前意图，以及适合录制自动化的下一步建议。"
            f" 当前窗口: {_window_label(state.active_window)}。"
            f" 当前焦点元素: {_element_label(state.focused_element)}。"
            f" 当前焦点文本: {focus_text or '无'}。"
        )

    def _build_node_suggestion(
        self,
        state: DesktopState,
        vision_payload: dict[str, Any] | None,
        ocr_payload: dict[str, Any],
    ) -> dict[str, Any]:
        window = state.active_window
        element = state.focused_element
        role = (element.role or "").lower() if element else ""
        vision_element = self._select_best_vision_element(state, vision_payload)
        step_type = StepType.TYPE.value if role in _INPUT_ROLES else StepType.CLICK.value
        target: dict[str, Any] = {
            "strategy": LocateStrategy.POSITION.value,
            "window_title": window.title if window else None,
            "role": element.role if element else None,
        }
        confidence = 0.42
        confidence_reason = "当前只能从窗口和位置上下文推断目标。"
        text_match_target = None
        if element is not None:
            for candidate in (
                element.functional_label,
                element.title,
                element.description,
                _element_text_preview(element, allow_redacted=False) if role not in _INPUT_ROLES else None,
            ):
                normalized = _stringify_text(candidate)
                if normalized:
                    text_match_target = normalized
                    break

        if element is not None:
            if element.identifier:
                target["strategy"] = LocateStrategy.ACCESSIBILITY_ID.value
                target["accessibility_id"] = element.identifier
                confidence = 0.94
                confidence_reason = "焦点元素包含 identifier，可优先使用 accessibility_id 定位。"
            elif element.selector:
                target["strategy"] = LocateStrategy.CSS_SELECTOR.value
                target["selector"] = element.selector
                confidence = 0.88
                confidence_reason = "焦点元素包含 selector，可作为稳定定位。"
            elif element.xpath:
                target["strategy"] = LocateStrategy.XPATH.value
                target["xpath"] = element.xpath
                confidence = 0.84
                confidence_reason = "焦点元素包含 xpath，可作为备选稳定定位。"
            elif text_match_target:
                target["strategy"] = LocateStrategy.TEXT_MATCH.value
                target["text_contains"] = text_match_target
                confidence = 0.72 if element.title or element.functional_label else 0.66
                confidence_reason = (
                    "元素具备可识别标签，可先用文本匹配定位。"
                    if element.title or element.functional_label
                    else "元素缺少稳定属性，但焦点文本可见，可先用文本线索辅助定位。"
                )

            target["title"] = element.title
            target["text_preview"] = _element_text_preview(element)
            target["description"] = element.description
            target["class_name"] = element.class_name
            target["url"] = element.url
            target["frame"] = element.frame
            if element.bounds is not None:
                target["position"] = {
                    "x": element.bounds.x + max(element.bounds.width // 2, 0),
                    "y": element.bounds.y + max(element.bounds.height // 2, 0),
                }
                target["bounds"] = element.bounds.model_dump(mode="json")
            if vision_element is not None:
                target["vision_element"] = vision_element
                vision_label = str(vision_element.get("label") or "").strip()
                vision_confidence = self._to_float(vision_element.get("confidence"), 0.0)
                if target["strategy"] == LocateStrategy.POSITION.value and vision_label:
                    target["strategy"] = LocateStrategy.TEXT_MATCH.value
                    target["text_contains"] = vision_label
                    confidence = max(confidence, 0.78 if vision_confidence >= 0.7 else 0.72)
                    confidence_reason = "焦点元素缺少稳定属性，但视觉识别到了目标标签，可改用文本匹配定位。"
                elif target["strategy"] == LocateStrategy.TEXT_MATCH.value and vision_label:
                    current_text = _normalize_text(target.get("text_contains"))
                    if current_text and current_text == _normalize_text(vision_label):
                        confidence = max(confidence, 0.82)
                        confidence_reason = "焦点文本与视觉识别结果一致，可提高文本匹配定位置信度。"
        elif vision_element is not None:
            vision_label = str(vision_element.get("label") or "").strip()
            target["role"] = vision_element.get("element_type")
            target["strategy"] = LocateStrategy.TEXT_MATCH.value if vision_label else LocateStrategy.IMAGE_MATCH.value
            if vision_label:
                target["text_contains"] = vision_label
            target["vision_element"] = vision_element
            step_type = self._infer_step_type_from_vision(vision_element)
            confidence = 0.74 if self._to_float(vision_element.get("confidence"), 0.0) >= 0.75 else 0.62
            confidence_reason = "缺少本地焦点元素，已改用视觉识别结果生成节点建议。"
        else:
            step_type = StepType.WAIT.value

        if element is not None and role in _INPUT_ROLES:
            step_type = StepType.TYPE.value

        action_text = "输入" if step_type == StepType.TYPE.value else "点击" if step_type == StepType.CLICK.value else "检查"
        description_target = _element_label(element)
        if vision_element is not None and _normalize_text(description_target) in {"", _normalize_text("未识别元素")}:
            description_target = str(
                vision_element.get("label")
                or vision_element.get("description")
                or vision_element.get("element_type")
                or description_target
            ).strip()
        description = f"在 {_window_label(window)} 中对 {description_target} 执行{action_text}"
        if step_type == StepType.WAIT.value:
            description = f"确认 {_window_label(window)} 已进入目标状态"

        preconditions = []
        if window and window.title:
            preconditions.append(f"确认当前窗口标题包含“{window.title}”")
        if window and window.app_name:
            preconditions.append(f"确认当前应用仍为“{window.app_name}”")
        if element is not None and element.is_visible:
            preconditions.append(f"确认目标元素“{description_target}”可见")

        postconditions = []
        if step_type == StepType.TYPE.value:
            postconditions.append("确认输入框值已经更新")
        elif step_type == StepType.CLICK.value:
            if vision_payload and vision_payload.get("suggested_next_actions"):
                postconditions.append(f"点击后优先检查：{vision_payload['suggested_next_actions'][0]}")
            else:
                postconditions.append("确认点击后界面出现预期变化")
        else:
            postconditions.append("确认界面状态与预期一致")

        variables = []
        if step_type == StepType.TYPE.value:
            variables.append(
                {
                    "name": "input_value",
                    "reason": "当前焦点是输入类元素，推荐将输入内容参数化。",
                }
            )
        if window and window.url:
            variables.append(
                {
                    "name": "target_url",
                    "reason": "当前窗口包含 URL，可作为导航或校验变量。",
                }
            )

        risk_flags = []
        if target["strategy"] == LocateStrategy.POSITION.value:
            risk_flags.append("当前建议主要依赖位置坐标，界面变化后容易失效。")
        if vision_payload and vision_payload.get("source") in {"cloud_llm_fallback", "context_fallback"}:
            risk_flags.append("当前节点部分依赖降级视觉结果，建议再次抓取焦点确认目标元素。")
        if ocr_payload.get("count", 0) == 0:
            risk_flags.append("当前 OCR 未提取到有效文本，建议补充一次更清晰的界面截图。")
        if vision_payload and vision_payload.get("confidence") is not None and vision_payload["confidence"] < 0.6:
            risk_flags.append("视觉分析置信度偏低，建议让 HUD 再次确认目标元素。")

        return {
            "step_type": step_type,
            "description": description,
            "target": target,
            "vision_source": vision_payload.get("source") if vision_payload else None,
            "preconditions": preconditions,
            "postconditions": postconditions,
            "variables": variables,
            "confidence": round(confidence, 2),
            "confidence_reason": confidence_reason,
            "risk_flags": risk_flags,
        }

    def _build_focus_summary(
        self,
        state: DesktopState,
        vision_payload: dict[str, Any] | None,
        ocr_payload: dict[str, Any],
        node_suggestion: dict[str, Any],
        recording_guidance: str | None,
    ) -> str:
        lines = [
            f"当前窗口: {_window_label(state.active_window)}",
            f"当前焦点: {_element_label(state.focused_element)}",
        ]
        focused_details = _focused_element_details(state.focused_element)
        if focused_details and focused_details.get("text_preview"):
            lines.append(f"焦点文本: {focused_details['text_preview']}")
        if focused_details and focused_details.get("identifier"):
            lines.append(f"焦点标识: {focused_details['identifier']}")
        if vision_payload and vision_payload.get("source"):
            lines.append(f"视觉来源: {vision_payload['source']}")
        if vision_payload and vision_payload.get("ui_state_description"):
            lines.append(f"UI 上下文: {vision_payload['ui_state_description']}")
        if vision_payload and vision_payload.get("user_intent"):
            lines.append(f"当前意图: {vision_payload['user_intent']}")
        if ocr_payload.get("text_preview"):
            lines.append(f"OCR 摘要: {ocr_payload['text_preview']}")
        lines.append(
            "建议节点: "
            f"{node_suggestion.get('description', '暂无')}"
            f" | 定位: {node_suggestion.get('target', {}).get('strategy', 'unknown')}"
            f" | 置信度: {node_suggestion.get('confidence', 0.0)}"
        )
        if node_suggestion.get("confidence_reason"):
            lines.append(f"节点依据: {node_suggestion['confidence_reason']}")
        if recording_guidance:
            lines.append(f"LLM 建议: {recording_guidance}")
        return "\n".join(lines)

    def _serialize_vision_analysis(self, analysis: ScreenshotAnalysis) -> dict[str, Any]:
        return {
            "available": True,
            "source": "local_vlm",
            "error": None,
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

    def _select_best_vision_element(
        self,
        state: DesktopState,
        vision_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in (vision_payload or {}).get("actionable_elements") or []
            if isinstance(item, dict)
        ]
        if not candidates:
            return None

        focused_label = _normalize_text(_element_label(state.focused_element)) if state.focused_element else ""
        focused_role = _normalize_text(state.focused_element.role) if state.focused_element else ""

        def _score(item: dict[str, Any]) -> float:
            label = _normalize_text(item.get("label"))
            element_type = _normalize_text(item.get("element_type"))
            score = self._to_float(item.get("confidence"), 0.0)
            if item.get("actionable", True):
                score += 0.12
            if item.get("bbox"):
                score += 0.05
            if focused_label and label:
                if label == focused_label:
                    score += 0.28
                elif label in focused_label or focused_label in label:
                    score += 0.18
            if focused_role and element_type:
                if element_type == focused_role:
                    score += 0.12
                elif focused_role in _INPUT_ROLES and element_type in _INPUT_ROLES:
                    score += 0.08
            return score

        return max(candidates, key=_score)

    def _infer_step_type_from_vision(self, item: dict[str, Any]) -> str:
        suggested_action = _normalize_text(item.get("suggested_action"))
        element_type = _normalize_text(item.get("element_type"))
        if suggested_action in {"type", "input", "fill", "enter"} or element_type in _INPUT_ROLES:
            return StepType.TYPE.value
        if suggested_action in {"click", "tap", "press", "select", "open"}:
            return StepType.CLICK.value
        return StepType.CLICK.value if item.get("actionable", True) else StepType.WAIT.value

    def _serialize_ocr_result(self, result: OCRResult) -> dict[str, Any]:
        return {
            "text": result.text,
            "confidence": result.confidence,
            "center": {"x": result.center_x, "y": result.center_y},
            "bbox": result.bbox,
        }