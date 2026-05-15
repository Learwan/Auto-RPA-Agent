from src.models.desktop import Rect, WindowInfo
from src.platform.macos.browser_context import (
    enrich_browser_window,
    get_browser_element_at_screen_point,
    get_browser_focused_element,
)


def _window(app_name: str, title: str = "Browser") -> WindowInfo:
    return WindowInfo(
        window_id="win-1",
        title=title,
        app_name=app_name,
        pid=42,
        bounds=Rect(x=10, y=20, width=1280, height=720),
        is_active=True,
        is_visible=True,
    )


def test_enrich_browser_window_adds_url_and_browser_type(monkeypatch):
    monkeypatch.setattr(
        "src.platform.macos.browser_context._execute_browser_javascript",
        lambda app_name, family, script: '{"title":"Inbox","url":"https://example.com/inbox","tabId":"tab-1"}',
    )

    enriched = enrich_browser_window(_window("Google Chrome", title="Original"))

    assert enriched is not None
    assert enriched.title == "Inbox"
    assert enriched.url == "https://example.com/inbox"
    assert enriched.browser_type == "chromium"
    assert enriched.tab_id == "tab-1"


def test_get_browser_focused_element_returns_selector_metadata(monkeypatch):
    monkeypatch.setattr(
        "src.platform.macos.browser_context._execute_browser_javascript",
        lambda app_name, family, script: (
            '{"role":"button","title":"提交","value":null,"identifier":"submit-btn","description":"提交表单",'
            '"className":"btn primary","selector":"[data-testid=\\"submit-btn\\"]","xpath":"//*[@id=\\"submit\\"]",'
            '"url":"https://example.com/form","frame":null,"inputType":null,"tagName":"button","isEnabled":true,'
            '"isVisible":true,"isFocusable":true,"isFocused":true,"bounds":{"x":100,"y":200,"width":80,"height":32}}'
        ),
    )

    element = get_browser_focused_element("Google Chrome")

    assert element is not None
    assert element.selector == '[data-testid="submit-btn"]'
    assert element.xpath == '//*[@id="submit"]'
    assert element.url == "https://example.com/form"
    assert element.tag_name == "button"


def test_get_browser_element_at_screen_point_returns_none_for_unknown_browser(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        "src.platform.macos.browser_context._execute_browser_javascript",
        lambda app_name, family, script: calls.append((app_name, family, script)) or None,
    )

    element = get_browser_element_at_screen_point("Finder", 120, 240)

    assert element is None
    assert calls == []
