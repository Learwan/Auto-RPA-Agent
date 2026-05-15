import contextlib
import ctypes
from ctypes import wintypes
from functools import lru_cache

from src.models.desktop import ElementCriteria, Rect, UIElement

CONTROL_TYPE_NAMES = {
    50000: "button",
    50001: "calendar",
    50002: "checkbox",
    50003: "combobox",
    50004: "edit",
    50005: "hyperlink",
    50006: "image",
    50007: "list_item",
    50008: "list",
    50009: "menu",
    50010: "menubar",
    50011: "menu_item",
    50012: "progressbar",
    50013: "radio_button",
    50014: "scrollbar",
    50015: "slider",
    50016: "spinner",
    50017: "statusbar",
    50018: "tab",
    50019: "tab_item",
    50020: "text",
    50021: "toolbar",
    50022: "tooltip",
    50023: "tree",
    50024: "tree_item",
    50025: "custom",
    50026: "group",
    50027: "thumb",
    50028: "datatable",
    50029: "header",
    50030: "header_item",
    50031: "table",
    50032: "title_bar",
    50033: "separator",
    50034: "semantic_zoom",
    50035: "app_bar",
    50036: "window",
    50037: "pane",
}

MAX_SEARCH_DEPTH = 12
MAX_SEARCH_NODES = 2500


@lru_cache(maxsize=1)
def _automation_bundle():
    from comtypes.client import CreateObject, GetModule

    GetModule("UIAutomationCore.dll")

    from comtypes.gen import UIAutomationClient as UIA

    automation_cls = getattr(UIA, "CUIAutomation8", UIA.CUIAutomation)
    automation = CreateObject(automation_cls, interface=UIA.IUIAutomation)
    return automation, UIA


def _ui_rect(bounds) -> Rect | None:
    left = int(getattr(bounds, "left", 0))
    top = int(getattr(bounds, "top", 0))
    right = int(getattr(bounds, "right", left))
    bottom = int(getattr(bounds, "bottom", top))
    width = max(0, right - left)
    height = max(0, bottom - top)
    if width == 0 or height == 0:
        return None
    return Rect(x=left, y=top, width=width, height=height)


def _safe_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _count_children(automation, element) -> int:
    try:
        walker = automation.ControlViewWalker
        count = 0
        child = walker.GetFirstChildElement(element)
        while child is not None and count < 200:
            count += 1
            child = walker.GetNextSiblingElement(child)
        return count
    except Exception:
        return 0


def _element_to_ui(automation, element) -> UIElement | None:
    try:
        role = CONTROL_TYPE_NAMES.get(int(element.CurrentControlType), str(int(element.CurrentControlType)))
        name = _safe_str(element.CurrentName)
        automation_id = _safe_str(element.CurrentAutomationId)
        class_name = _safe_str(element.CurrentClassName)
        description = _safe_str(getattr(element, "CurrentHelpText", None))
        value = None
        try:
            value = _safe_str(element.GetCurrentPropertyValue(30045))
        except Exception:
            value = None
        bounds = _ui_rect(element.CurrentBoundingRectangle)
        return UIElement(
            role=role,
            title=name,
            value=value,
            identifier=automation_id,
            description=description,
            bounds=bounds,
            class_name=class_name,
            is_enabled=bool(element.CurrentIsEnabled),
            is_visible=not bool(element.CurrentIsOffscreen),
            is_focusable=True,
            is_focused=bool(element.CurrentHasKeyboardFocus),
            children_count=_count_children(automation, element),
        )
    except Exception:
        return None


def _matches_criteria(info: UIElement, criteria: ElementCriteria) -> int:
    score = 0

    if criteria.accessibility_id:
        if info.identifier == criteria.accessibility_id:
            score += 6
        elif info.identifier and criteria.accessibility_id.lower() in info.identifier.lower():
            score += 3

    if criteria.title:
        if info.title == criteria.title:
            score += 5
        elif info.title and criteria.title.lower() in info.title.lower():
            score += 2

    if criteria.text_contains and info.title and criteria.text_contains.lower() in info.title.lower():
        score += 4

    if criteria.role and info.role == criteria.role:
        score += 2

    if criteria.class_name and info.class_name == criteria.class_name:
        score += 2

    if criteria.position and info.bounds:
        tolerance = criteria.position_tolerance
        if (
            info.bounds.x - tolerance <= criteria.position.x <= info.bounds.x + info.bounds.width + tolerance
            and info.bounds.y - tolerance <= criteria.position.y <= info.bounds.y + info.bounds.height + tolerance
        ):
            score += 2

    return score


def _iter_subtree(automation, root):
    walker = automation.ControlViewWalker
    stack: list[tuple[object, int]] = [(root, 0)]
    visited = 0

    while stack and visited < MAX_SEARCH_NODES:
        element, depth = stack.pop()
        visited += 1
        yield element

        if depth >= MAX_SEARCH_DEPTH:
            continue

        try:
            children = []
            child = walker.GetFirstChildElement(element)
            while child is not None:
                children.append(child)
                child = walker.GetNextSiblingElement(child)
            for child in reversed(children):
                stack.append((child, depth + 1))
        except Exception:
            continue


def _foreground_root(automation):
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if hwnd:
        try:
            return automation.ElementFromHandle(hwnd)
        except Exception:
            return None
    return None


async def get_focused_element() -> UIElement | None:
    try:
        automation, _uia = _automation_bundle()
        element = automation.GetFocusedElement()
        if element is None:
            return None
        return _element_to_ui(automation, element)
    except Exception:
        return None


async def get_element_at_position(x: int, y: int) -> UIElement | None:
    try:
        automation, _uia = _automation_bundle()
        point = wintypes.POINT(x=int(x), y=int(y))
        element = automation.ElementFromPoint(point)
        if element is None:
            return None
        return _element_to_ui(automation, element)
    except Exception:
        return None


async def find_element_by_criteria(criteria: ElementCriteria) -> UIElement | None:
    try:
        automation, _uia = _automation_bundle()
        search_roots = []
        active_root = _foreground_root(automation)
        if active_root is not None:
            search_roots.append(active_root)
        with contextlib.suppress(Exception):
            search_roots.append(automation.GetRootElement())

        best_match: UIElement | None = None
        best_score = 0

        for root in search_roots:
            if root is None:
                continue
            for element in _iter_subtree(automation, root):
                info = _element_to_ui(automation, element)
                if info is None:
                    continue
                score = _matches_criteria(info, criteria)
                if score > best_score:
                    best_score = score
                    best_match = info
                    if score >= 8:
                        return best_match

        return best_match if best_score > 0 else None
    except Exception:
        return None
