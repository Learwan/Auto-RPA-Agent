from __future__ import annotations

import math
from typing import Any

GENERIC_TEXT_HINTS = {
    "",
    "text",
    "label",
    "static text",
    "group",
    "view",
    "window",
    "button",
    "icon",
    "item",
    "container",
    "文本",
    "标签",
    "按钮",
    "图标",
    "项目",
}
CLICK_MATCH_RADIUS_PX = 120.0


def build_click_node_suggestion(
    *,
    click_x: int,
    click_y: int,
    active_window: dict[str, Any] | None = None,
    vision_payload: dict[str, Any] | None = None,
    ocr_payload: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any] | None:
    vision_candidates = [
        item
        for item in (vision_payload or {}).get("actionable_elements") or []
        if isinstance(item, dict)
    ]
    vision_match = select_vision_element_for_click(click_x, click_y, vision_candidates)
    matched_vision = vision_match[0] if vision_match is not None else None
    matched_bbox = vision_match[1] if vision_match is not None else None
    matched_ocr = select_ocr_text_for_click(click_x, click_y, ocr_payload, preferred_bbox=matched_bbox)

    vision_label = str((matched_vision or {}).get("label") or "").strip()
    vision_description = str((matched_vision or {}).get("description") or "").strip()
    ocr_text = str((matched_ocr or {}).get("text") or "").strip()
    label = _select_best_label(vision_label, ocr_text, vision_description)

    if not label:
        return None

    role = (matched_vision or {}).get("element_type")
    target: dict[str, Any] = {
        "strategy": "text_match",
        "title": label,
        "text_contains": label,
        "text_preview": ocr_text or None,
        "role": role,
        "class_name": None,
        "description": vision_description or None,
        "vision_element": matched_vision,
        "window_title": (active_window or {}).get("title") or (active_window or {}).get("app_name"),
    }

    chosen_bbox = matched_bbox or normalize_ocr_bbox((matched_ocr or {}).get("bbox"))
    if chosen_bbox is not None:
        x, y, width, height = chosen_bbox
        target["bounds"] = {"x": x, "y": y, "width": width, "height": height}
        target["position"] = {"x": x + max(width // 2, 0), "y": y + max(height // 2, 0)}

    confidence, confidence_reason = _derive_confidence(matched_vision, matched_ocr, label_source="ocr" if ocr_text and label == ocr_text else "vision")

    payload: dict[str, Any] = {
        "step_type": "click",
        "vision_source": (vision_payload or {}).get("source") or ("ocr_salvage" if matched_ocr else None),
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "target": target,
    }
    if snapshot_id not in (None, ""):
        payload["snapshot_id"] = snapshot_id
    if matched_ocr:
        payload["ocr_text"] = matched_ocr.get("text")
        payload["ocr_confidence"] = matched_ocr.get("confidence")
    return payload


def select_vision_element_for_click(
    click_x: int,
    click_y: int,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[int, int, int, int] | None] | None:
    containing: list[tuple[float, dict[str, Any], tuple[int, int, int, int]]] = []
    nearby: list[tuple[float, dict[str, Any], tuple[int, int, int, int]]] = []

    for item in candidates:
        bbox = normalize_vision_bbox(item.get("bbox"))
        if bbox is None:
            continue

        confidence = to_float(item.get("confidence"), 0.0)
        score = confidence + (0.12 if item.get("actionable", True) else 0.0)
        if is_meaningful_text(item.get("label")):
            score += 0.08

        x, y, width, height = bbox
        center_x = x + width / 2
        center_y = y + height / 2
        distance = math.hypot(click_x - center_x, click_y - center_y)
        radius = max(width, height, CLICK_MATCH_RADIUS_PX)
        within_bounds = x <= click_x <= x + width and y <= click_y <= y + height

        if within_bounds:
            containing.append((score + 0.8, item, bbox))
        elif distance <= radius:
            nearby.append((score + 0.3 - distance / max(radius * 4, 1), item, bbox))

    if containing:
        containing.sort(key=lambda item: item[0], reverse=True)
        best = containing[0]
        return best[1], best[2]
    if nearby:
        nearby.sort(key=lambda item: item[0], reverse=True)
        best = nearby[0]
        return best[1], best[2]
    return None


def select_ocr_text_for_click(
    click_x: int,
    click_y: int,
    ocr_payload: dict[str, Any] | None,
    *,
    preferred_bbox: tuple[int, int, int, int] | None = None,
) -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any]]] = []

    for item in (ocr_payload or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not is_meaningful_text(text):
            continue

        bbox = normalize_ocr_bbox(item.get("bbox"))
        confidence = to_float(item.get("confidence"), 0.0)
        center_x = to_float(item.get("center", {}).get("x"), to_float(item.get("center_x"), click_x))
        center_y = to_float(item.get("center", {}).get("y"), to_float(item.get("center_y"), click_y))
        distance = math.hypot(click_x - center_x, click_y - center_y)
        score = confidence + min(len(text) / 30.0, 0.12)

        if preferred_bbox is not None and bbox is not None and _bbox_overlaps(preferred_bbox, bbox):
            score += 0.5
        elif preferred_bbox is not None and bbox is None and distance <= CLICK_MATCH_RADIUS_PX:
            score += 0.18
        elif distance <= CLICK_MATCH_RADIUS_PX:
            score += 0.28 - distance / max(CLICK_MATCH_RADIUS_PX * 4, 1)
        else:
            continue

        candidates.append((score, item))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def normalize_vision_bbox(payload: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(payload, (list, tuple)) or len(payload) != 4:
        return None
    try:
        x, y, width, height = (int(float(value)) for value in payload)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def normalize_ocr_bbox(payload: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(payload, (list, tuple)) or not payload:
        return None
    if len(payload) == 4 and all(not isinstance(value, (list, tuple, dict)) for value in payload):
        return normalize_vision_bbox(payload)
    if len(payload) != 4:
        return None

    points: list[tuple[int, int]] = []
    for point in payload:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        try:
            px, py = int(float(point[0])), int(float(point[1]))
        except Exception:
            return None
        points.append((px, py))

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        return None
    return min_x, min_y, width, height


def is_meaningful_text(value: Any) -> bool:
    normalized = _normalize_text(value)
    if not normalized or normalized in GENERIC_TEXT_HINTS:
        return False
    return True


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _select_best_label(vision_label: str, ocr_text: str, vision_description: str) -> str | None:
    meaningful_vision_label = vision_label if is_meaningful_text(vision_label) else ""
    meaningful_ocr_text = ocr_text if is_meaningful_text(ocr_text) else ""
    meaningful_description = vision_description if is_meaningful_text(vision_description) else ""

    if meaningful_ocr_text and not meaningful_vision_label:
        return meaningful_ocr_text
    if meaningful_ocr_text and meaningful_vision_label:
        if meaningful_vision_label in meaningful_ocr_text or meaningful_ocr_text in meaningful_vision_label:
            return meaningful_ocr_text if len(meaningful_ocr_text) >= len(meaningful_vision_label) else meaningful_vision_label
        return meaningful_ocr_text if len(meaningful_ocr_text) > len(meaningful_vision_label) else meaningful_vision_label
    if meaningful_vision_label:
        return meaningful_vision_label
    if meaningful_description and len(meaningful_description) <= 48:
        return meaningful_description
    return None


def _derive_confidence(
    matched_vision: dict[str, Any] | None,
    matched_ocr: dict[str, Any] | None,
    *,
    label_source: str,
) -> tuple[float, str]:
    vision_confidence = to_float((matched_vision or {}).get("confidence"), 0.0)
    ocr_confidence = to_float((matched_ocr or {}).get("confidence"), 0.0)

    if matched_vision and matched_ocr:
        return round(max(0.84, min(0.94, 0.52 + vision_confidence * 0.24 + ocr_confidence * 0.22)), 2), (
            "点击坐标命中了视觉候选元素，且附近 OCR 文本可作为稳定标签。"
        )
    if matched_vision and label_source == "vision":
        return round(max(0.78, min(0.9, 0.56 + vision_confidence * 0.32)), 2), (
            "点击坐标命中了视觉候选元素，并且候选标签可直接作为定位文本。"
        )
    if matched_ocr:
        return round(max(0.72, min(0.86, 0.5 + ocr_confidence * 0.32)), 2), (
            "视觉标签不足时，已改用点击附近 OCR 文本补强定位。"
        )
    return 0.0, "未能从视觉或 OCR 中恢复稳定目标。"


def _bbox_overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()
