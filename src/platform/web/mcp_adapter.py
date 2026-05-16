from __future__ import annotations

import logging

from src.models.desktop import ElementCriteria, Rect, UIElement
from src.platform.web.adapter import WebAdapter

logger = logging.getLogger(__name__)


class PlaywrightMCPAdapter(WebAdapter):
    """Web adapter using Playwright accessibility tree for deterministic element targeting.

    Falls back to regular Playwright locators when accessibility matching fails.
    """

    async def get_accessibility_snapshot(self) -> dict | None:
        """Get structured accessibility tree from current page."""
        try:
            page = await self._ensure_page()
            snapshot = await page.accessibility.snapshot()
            return snapshot
        except Exception as e:
            logger.debug("Accessibility snapshot failed: %s", e)
            return None

    async def find_element(self, criteria: ElementCriteria) -> UIElement | None:
        if criteria.position is not None:
            return await self.get_element_at_position(criteria.position.x, criteria.position.y)

        ax_element = await self._find_by_accessibility(criteria)
        if ax_element is not None:
            return ax_element

        return await super().find_element(criteria)

    async def find_element_by_ref(self, ref: str) -> UIElement | None:
        """Direct ref-based lookup via accessibility snapshot."""
        snapshot = await self.get_accessibility_snapshot()
        if not snapshot:
            return None

        node = self._find_node_by_ref(snapshot, ref)
        if node:
            return self._ax_node_to_element(node)
        return None

    async def _find_by_accessibility(self, criteria: ElementCriteria) -> UIElement | None:
        snapshot = await self.get_accessibility_snapshot()
        if not snapshot:
            return None

        node = self._match_node(snapshot, criteria)
        if node:
            element = self._ax_node_to_element(node)
            page = await self._ensure_page()
            locator = await self._ax_node_to_locator(page, node)
            if locator:
                try:
                    box = await locator.bounding_box()
                    if box:
                        element.bounds = Rect(
                            x=int(box["x"]),
                            y=int(box["y"]),
                            width=int(box["width"]),
                            height=int(box["height"]),
                        )
                except Exception:
                    pass
            return element

        return None

    def _match_node(self, node: dict, criteria: ElementCriteria) -> dict | None:
        """Recursively search accessibility tree for matching node."""
        if self._node_matches(node, criteria):
            return node

        for child in node.get("children", []):
            result = self._match_node(child, criteria)
            if result:
                return result

        return None

    @staticmethod
    def _node_matches(node: dict, criteria: ElementCriteria) -> bool:
        role = node.get("role", "")
        name = node.get("name", "")

        if criteria.role and role.lower() != criteria.role.lower():
            return False

        if criteria.title and criteria.title not in name:
            return False

        if criteria.text_contains and criteria.text_contains not in name:
            return False

        if criteria.accessibility_id:
            node_id = node.get("automationId", "") or node.get("description", "")
            if criteria.accessibility_id not in node_id and criteria.accessibility_id not in name:
                return False

        return bool(
            criteria.role or criteria.title or criteria.text_contains or criteria.accessibility_id
        )

    @staticmethod
    def _find_node_by_ref(node: dict, ref: str) -> dict | None:
        """Find a node by its name or role reference."""
        if node.get("name") == ref or node.get("role") == ref:
            return node
        for child in node.get("children", []):
            result = PlaywrightMCPAdapter._find_node_by_ref(child, ref)
            if result:
                return result
        return None

    @staticmethod
    def _ax_node_to_element(node: dict) -> UIElement:
        return UIElement(
            role=node.get("role", "unknown"),
            title=node.get("name") or None,
            value=node.get("value") or None,
            description=node.get("description") or None,
            is_enabled=not node.get("disabled", False),
            is_visible=True,
            is_focusable=node.get("focused", False) or node.get("role") in {
                "button", "link", "textbox", "combobox", "checkbox",
                "radio", "menuitem", "tab", "searchbox",
            },
            is_focused=node.get("focused", False),
        )

    @staticmethod
    async def _ax_node_to_locator(page, node: dict):
        """Try to build a Playwright locator from an accessibility tree node."""
        role = node.get("role", "")
        name = node.get("name", "")

        try:
            if role and name:
                locator = page.get_by_role(role, name=name).first
                if await locator.count() > 0:
                    return locator
            if name:
                locator = page.get_by_text(name, exact=True).first
                if await locator.count() > 0:
                    return locator
        except Exception:
            pass

        return None

    def get_platform_name(self) -> str:
        return "web"
