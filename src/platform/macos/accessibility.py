import contextlib
import logging
import subprocess

from src.models.desktop import Rect, UIElement
from src.platform.macos.browser_context import get_browser_element_at_screen_point, get_browser_focused_element

logger = logging.getLogger(__name__)


def _derive_functional_label(
    role: str,
    title: str | None,
    identifier: str | None,
    description: str | None,
    role_description: str | None,
    value: str | None,
) -> str | None:
    """Derive a human-readable functional label from accessibility attributes.

    Strategy (priority order):
    1. AXRoleDescription (e.g., "save button", "file menu") — most specific
    2. Title if available (e.g., "Save", "File")
    3. Identifier (developer-assigned accessibility ID)
    4. Description (help text)
    5. Role + Value (e.g., "AXButton Submit")
    6. Role only as fallback
    """

    if role_description:
        label = str(role_description).strip()
        if title:
            label = f"{title} - {label}"
        return label

    if title:
        label = str(title).strip()
        if role and role.lower() not in label.lower():
            label = f"{label} ({role})"
        return label

    if identifier:
        label = f"[{identifier}]"
        if role:
            label = f"{label} ({role})"
        return label

    if description:
        label = str(description).strip()
        if role:
            label = f"{label} ({role})"
        return label

    if value and role:
        return f"{value} ({role})"

    if role:
        return role

    return None


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _score_text(actual: str | None, expected: str | None, *, exact: int, contains: int) -> int:
    expected_text = _normalize_text(expected)
    actual_text = _normalize_text(actual)
    if not expected_text or not actual_text:
        return 0
    if actual_text == expected_text:
        return exact
    if expected_text in actual_text:
        return contains
    return 0


def _bounds_match(info_bounds: Rect | None, target_bounds: dict | None, tolerance: int = 20) -> bool:
    if info_bounds is None or not isinstance(target_bounds, dict):
        return False
    target_x = target_bounds.get("x")
    target_y = target_bounds.get("y")
    target_width = target_bounds.get("width")
    target_height = target_bounds.get("height")
    if not all(isinstance(value, int) for value in (target_x, target_y, target_width, target_height)):
        return False
    return (
        abs(info_bounds.x - target_x) <= tolerance
        and abs(info_bounds.y - target_y) <= tolerance
        and abs(info_bounds.width - target_width) <= max(4, tolerance)
        and abs(info_bounds.height - target_height) <= max(4, tolerance)
    )


def _matches_criteria(info: UIElement, criteria: dict) -> int:
    score = 0
    if not isinstance(criteria, dict):
        return score

    if criteria.get("role") and info.role and criteria["role"] == info.role:
        score += 2

    score += _score_text(info.title, criteria.get("title"), exact=3, contains=2)
    score += _score_text(info.identifier, criteria.get("accessibility_id"), exact=4, contains=2)
    score += _score_text(info.title, criteria.get("text_contains"), exact=2, contains=2)
    score += _score_text(info.class_name, criteria.get("class_name"), exact=2, contains=1)
    score += _score_text(info.value, criteria.get("value"), exact=3, contains=2)
    score += _score_text(info.description, criteria.get("description"), exact=3, contains=2)
    score += _score_text(info.label, criteria.get("label"), exact=2, contains=1)
    score += _score_text(info.functional_label, criteria.get("functional_label"), exact=3, contains=2)
    score += _score_text(info.input_type, criteria.get("input_type"), exact=2, contains=1)
    score += _score_text(info.tag_name, criteria.get("tag_name"), exact=2, contains=1)

    position = criteria.get("position")
    if isinstance(position, dict) and info.bounds:
        x = position.get("x")
        y = position.get("y")
        if isinstance(x, int) and isinstance(y, int):
            tolerance = 20
            if (
                info.bounds.x - tolerance <= x <= info.bounds.x + info.bounds.width + tolerance
                and info.bounds.y - tolerance <= y <= info.bounds.y + info.bounds.height + tolerance
            ):
                score += 2

    if _bounds_match(info.bounds, criteria.get("bounds")):
        score += 2

    return score


def _extract_element_info(element) -> UIElement | None:
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            kAXDescriptionAttribute,
            kAXEnabledAttribute,
            kAXErrorSuccess,
            kAXFocusedAttribute,
            kAXHelpAttribute,
            kAXIdentifierAttribute,
            kAXPositionAttribute,
            kAXRoleAttribute,
            kAXRoleDescriptionAttribute,
            kAXSizeAttribute,
            kAXSubroleAttribute,
            kAXTitleAttribute,
            kAXValueAttribute,
            kAXWindowAttribute,
        )

        def _get_attr(el, attr):
            val = None
            err, val = AXUIElementCopyAttributeValue(el, attr, None)
            return val if err == kAXErrorSuccess else None

        position = _get_attr(element, kAXPositionAttribute)
        size = _get_attr(element, kAXSizeAttribute)
        bounds = None
        if position and size:
            with contextlib.suppress(TypeError, IndexError):
                bounds = Rect(
                    x=int(position[0]),
                    y=int(position[1]),
                    width=int(size[0]),
                    height=int(size[1]),
                )

        window = _get_attr(element, kAXWindowAttribute)
        if window:
            win_title = _get_attr(window, kAXTitleAttribute)
            if win_title:
                str(win_title)

        role = str(_get_attr(element, kAXRoleAttribute) or "")
        title = str(_get_attr(element, kAXTitleAttribute) or "") or None
        value = str(_get_attr(element, kAXValueAttribute) or "") or None
        identifier = str(_get_attr(element, kAXIdentifierAttribute) or "") or None
        description = str(_get_attr(element, kAXDescriptionAttribute) or "") or None
        subrole = _get_attr(element, kAXSubroleAttribute)
        role_description = _get_attr(element, kAXRoleDescriptionAttribute)
        help_text = _get_attr(element, kAXHelpAttribute)

        class_name_parts = []
        if subrole:
            class_name_parts.append(str(subrole))
        if role_description:
            class_name_parts.append(str(role_description))
        class_name = " - ".join(class_name_parts) if class_name_parts else None

        if not role and not title and not identifier:
            return None

        description_text = description
        if help_text and not description_text:
            description_text = str(help_text)

        functional_label = _derive_functional_label(
            role=role,
            title=title,
            identifier=identifier,
            description=description_text,
            role_description=str(role_description) if role_description else None,
            value=value,
        )

        window_handle = None
        if window:
            with contextlib.suppress(Exception):
                window_handle = _get_attr(window, kAXIdentifierAttribute)

        return UIElement(
            role=role,
            title=title,
            value=value,
            identifier=identifier,
            description=description_text,
            functional_label=functional_label,
            bounds=bounds,
            class_name=class_name,
            is_enabled=_get_attr(element, kAXEnabledAttribute) is not False,
            is_focused=_get_attr(element, kAXFocusedAttribute) is True,
            window_handle=window_handle,
        )
    except Exception as e:
        logger.debug(f"Failed to extract element info: {e}")
        return None


async def get_focused_element() -> UIElement | None:
    browser_focused = await _get_browser_focused_element()
    if browser_focused is not None:
        return browser_focused

    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            kAXErrorSuccess,
            kAXFocusedAttribute,
        )

        system_wide = AXUIElementCreateSystemWide()

        err, focused = AXUIElementCopyAttributeValue(system_wide, kAXFocusedAttribute, None)
        if err != kAXErrorSuccess:
            return None

        return _extract_element_info(focused)
    except ImportError:
        return None


async def get_element_at_position(x: int, y: int) -> UIElement | None:
    browser_element = await _get_browser_element_at_position(x, y)
    if browser_element is not None:
        return browser_element

    try:
        from ApplicationServices import (
            AXUIElementCopyElementAtPosition,
            AXUIElementCreateSystemWide,
            kAXErrorSuccess,
        )

        system_wide = AXUIElementCreateSystemWide()

        err, element = AXUIElementCopyElementAtPosition(system_wide, float(x), float(y), None)
        if err != kAXErrorSuccess or element is None:
            logger.debug(f"AXUIElementCopyElementAtPosition failed at ({x}, {y}): error={err}")
            return None

        ui_element = _extract_element_info(element)
        if ui_element is None:
            logger.debug(f"Could not extract element info at ({x}, {y})")
        return ui_element
    except ImportError:
        logger.warning("ApplicationServices not available, cannot get element at position")
        return None
    except Exception as e:
        logger.debug(f"Error getting element at position ({x}, {y}): {e}")
        return None


async def _get_browser_focused_element() -> UIElement | None:
    app_name = await _frontmost_application_name()
    if not app_name:
        return None
    try:
        return get_browser_focused_element(app_name)
    except Exception as e:
        logger.debug(f"Browser focused element lookup failed: {e}")
        return None


async def _get_browser_element_at_position(x: int, y: int) -> UIElement | None:
    app_name = await _frontmost_application_name()
    if not app_name:
        return None
    try:
        return get_browser_element_at_screen_point(app_name, x, y)
    except Exception as e:
        logger.debug(f"Browser element lookup failed at ({x}, {y}): {e}")
        return None


async def _frontmost_application_name() -> str | None:
    try:
        script = 'tell application "System Events" to get name of first process whose frontmost is true'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=1.0, check=False)
        if result.returncode != 0:
            return None
        name = result.stdout.strip()
        return name or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


async def find_element_by_criteria(criteria: dict) -> UIElement | None:
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            kAXChildrenAttribute,
            kAXErrorSuccess,
            kAXFocusedAttribute,
            kAXWindowsAttribute,
        )

        system_wide = AXUIElementCreateSystemWide()
        err, focused_app = AXUIElementCopyAttributeValue(system_wide, kAXFocusedAttribute, None)
        if err != kAXErrorSuccess:
            return None

        best_match = None
        best_score = 0

        def _search(element, depth=0):
            nonlocal best_match, best_score
            if depth > 8 or best_score >= 6:
                return

            info = _extract_element_info(element)
            if info:
                score = _matches_criteria(info, criteria)
                if score > best_score:
                    best_score = score
                    best_match = info

            err_val, children = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute, None)
            if err_val == kAXErrorSuccess and children:
                for child in children[:20]:
                    _search(child, depth + 1)

        err_val, windows = AXUIElementCopyAttributeValue(focused_app, kAXWindowsAttribute, None)
        if err_val == kAXErrorSuccess and windows:
            for win in windows[:5]:
                _search(win)

        if best_score >= 1:
            return best_match
        return None
    except Exception as e:
        logger.debug(f"find_element_by_criteria failed: {e}")
        return None
