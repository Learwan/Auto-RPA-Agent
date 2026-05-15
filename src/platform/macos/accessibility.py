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

        target_role = criteria.get("role")
        target_title = criteria.get("title")
        target_identifier = criteria.get("accessibility_id")
        target_text_contains = criteria.get("text_contains")

        best_match = None
        best_score = 0

        def _search(element, depth=0):
            nonlocal best_match, best_score
            if depth > 8 or best_score >= 3:
                return

            info = _extract_element_info(element)
            if info:
                score = 0
                if target_role and info.role and target_role == info.role:
                    score += 1
                if target_title and info.title and target_title.lower() == info.title.lower():
                    score += 1
                if target_identifier and info.identifier and target_identifier == info.identifier:
                    score += 1
                if target_text_contains and info.title and target_text_contains.lower() in info.title.lower():
                    score += 1
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
