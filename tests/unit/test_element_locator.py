from unittest.mock import AsyncMock

import pytest

from src.executor.element_locator import ElementLocator, LocatedElement
from src.models.automation import LocateStrategy, StepTarget
from src.models.desktop import Point, Rect, UIElement


@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.get_screen_size = AsyncMock(return_value=(1920, 1080))
    adapter.capture_screen = AsyncMock(return_value=b"")
    return adapter


@pytest.fixture
def locator(mock_adapter):
    return ElementLocator(platform_adapter=mock_adapter)


def _make_ui_element(role="button", title="Test", identifier=None, bounds=None, selector=None, xpath=None):
    return UIElement(
        role=role,
        title=title,
        identifier=identifier,
        bounds=bounds or Rect(x=100, y=100, width=80, height=30),
        selector=selector,
        xpath=xpath,
    )


class TestElementLocatorStrategyOrder:
    def test_accessibility_id_strategy_first(self, locator):
        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="btn-save",
        )
        strategies = locator._get_strategy_order(target)
        assert strategies[0] == LocateStrategy.ACCESSIBILITY_ID

    def test_text_match_strategy_first(self, locator):
        target = StepTarget(
            strategy=LocateStrategy.TEXT_MATCH,
            title="Save",
            text_contains="Save",
        )
        strategies = locator._get_strategy_order(target)
        assert strategies[0] == LocateStrategy.TEXT_MATCH

    def test_css_selector_strategy_first(self, locator):
        target = StepTarget(
            strategy=LocateStrategy.CSS_SELECTOR,
            selector="#save-btn",
        )
        strategies = locator._get_strategy_order(target)
        assert strategies[0] == LocateStrategy.CSS_SELECTOR

    def test_xpath_strategy_first(self, locator):
        target = StepTarget(
            strategy=LocateStrategy.XPATH,
            xpath="//button[@id='save']",
        )
        strategies = locator._get_strategy_order(target)
        assert strategies[0] == LocateStrategy.XPATH

    def test_position_strategy_first(self, locator):
        target = StepTarget(
            strategy=LocateStrategy.POSITION,
            position=Point(x=100, y=200),
        )
        strategies = locator._get_strategy_order(target)
        assert strategies[0] == LocateStrategy.POSITION

    def test_fallback_strategies_when_no_match(self, locator):
        target = StepTarget(strategy=LocateStrategy.POSITION)
        strategies = locator._get_strategy_order(target)
        assert len(strategies) >= 5
        assert LocateStrategy.ACCESSIBILITY_ID in strategies
        assert LocateStrategy.TEXT_MATCH in strategies
        assert LocateStrategy.POSITION in strategies


class TestElementLocatorLocate:
    @pytest.mark.asyncio
    async def test_locate_by_accessibility_id(self, locator, mock_adapter):
        element = _make_ui_element(identifier="btn-save")
        mock_adapter.find_element = AsyncMock(return_value=element)

        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="btn-save",
        )
        result = await locator.locate(target)

        assert result is not None
        assert result.strategy_used == LocateStrategy.ACCESSIBILITY_ID
        assert result.element.identifier == "btn-save"

    @pytest.mark.asyncio
    async def test_locate_by_text_match(self, locator, mock_adapter):
        element = _make_ui_element(title="Submit")
        mock_adapter.find_element = AsyncMock(return_value=element)

        target = StepTarget(
            strategy=LocateStrategy.TEXT_MATCH,
            title="Submit",
        )
        result = await locator.locate(target)

        assert result is not None
        assert result.strategy_used == LocateStrategy.TEXT_MATCH

    @pytest.mark.asyncio
    async def test_locate_by_css_selector(self, locator, mock_adapter):
        element = _make_ui_element(selector="#submit-btn")
        mock_adapter.find_element = AsyncMock(return_value=element)

        target = StepTarget(
            strategy=LocateStrategy.CSS_SELECTOR,
            selector="#submit-btn",
        )
        result = await locator.locate(target)

        assert result is not None
        assert result.strategy_used == LocateStrategy.CSS_SELECTOR

    @pytest.mark.asyncio
    async def test_locate_by_xpath(self, locator, mock_adapter):
        element = _make_ui_element(xpath="//button[@type='submit']")
        mock_adapter.find_element = AsyncMock(return_value=element)

        target = StepTarget(
            strategy=LocateStrategy.XPATH,
            xpath="//button[@type='submit']",
        )
        result = await locator.locate(target)

        assert result is not None
        assert result.strategy_used == LocateStrategy.XPATH

    @pytest.mark.asyncio
    async def test_locate_by_position(self, locator, mock_adapter):
        target = StepTarget(
            strategy=LocateStrategy.POSITION,
            position=Point(x=500, y=300),
        )
        result = await locator.locate(target)

        assert result is not None
        assert result.strategy_used == LocateStrategy.POSITION
        assert result.center.x == 500
        assert result.center.y == 300

    @pytest.mark.asyncio
    async def test_locate_returns_none_when_not_found(self, locator, mock_adapter):
        mock_adapter.find_element = AsyncMock(return_value=None)

        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="nonexistent",
        )
        result = await locator.locate(target)

        assert result is None

    @pytest.mark.asyncio
    async def test_locate_fallback_to_next_strategy(self, locator, mock_adapter):
        call_count = 0

        async def mock_find(criteria):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return _make_ui_element(title="Save")

        mock_adapter.find_element = AsyncMock(side_effect=mock_find)

        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="btn-save",
            title="Save",
        )
        result = await locator.locate(target)

        assert result is not None
        assert result.strategy_used == LocateStrategy.TEXT_MATCH


class TestElementLocatorVerify:
    @pytest.mark.asyncio
    async def test_verify_element_found(self, locator, mock_adapter):
        element = _make_ui_element(identifier="btn-ok")
        mock_adapter.find_element = AsyncMock(return_value=element)

        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="btn-ok",
        )
        result = await locator.verify_element(target, timeout_ms=1000)
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_element_not_found(self, locator, mock_adapter):
        mock_adapter.find_element = AsyncMock(return_value=None)

        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="nonexistent",
        )
        result = await locator.verify_element(target, timeout_ms=500)
        assert result is False


class TestLocatedElement:
    def test_center_from_bounds(self):
        element = UIElement(
            role="button",
            title="Test",
            bounds=Rect(x=100, y=200, width=80, height=40),
        )
        located = LocatedElement(element, LocateStrategy.POSITION)
        assert located.center.x == 140
        assert located.center.y == 220

    def test_center_from_position(self):
        element = UIElement(role="button", title="Test", bounds=None)
        located = LocatedElement(
            element,
            LocateStrategy.POSITION,
            position=Point(x=50, y=75),
        )
        assert located.center.x == 50
        assert located.center.y == 75
