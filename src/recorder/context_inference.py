from __future__ import annotations

from src.models.desktop import UIElement, WindowInfo
from src.models.operation import OperationContext

_MODE_ALIASES = {
    "desktop": "desktop",
    "browser": "web",
    "web": "web",
    "auto": "auto",
    "hybrid": "auto",
}

_BROWSER_SIGNATURES = {
    "google chrome": "chromium",
    "chrome": "chromium",
    "chromium": "chromium",
    "microsoft edge": "chromium",
    "msedge": "chromium",
    "brave": "chromium",
    "vivaldi": "chromium",
    "opera": "chromium",
    "arc": "chromium",
    "firefox": "firefox",
    "zen browser": "firefox",
    "safari": "webkit",
    "orion": "webkit",
}

_BROWSER_ALIASES = {
    "chromium": "chromium",
    "chrome": "chromium",
    "google chrome": "chromium",
    "edge": "chromium",
    "microsoft edge": "chromium",
    "msedge": "chromium",
    "brave": "chromium",
    "vivaldi": "chromium",
    "opera": "chromium",
    "arc": "chromium",
    "firefox": "firefox",
    "zen": "firefox",
    "zen browser": "firefox",
    "webkit": "webkit",
    "safari": "webkit",
    "orion": "webkit",
}

_WEB_URL_PREFIXES = (
    "http://",
    "https://",
    "file://",
    "about:",
    "chrome://",
    "edge://",
    "devtools://",
    "moz-extension://",
    "chrome-extension://",
    "safari-web-extension://",
)


def normalize_recording_mode(mode: str | None) -> str:
    candidate = (mode or "desktop").strip().lower()
    normalized = _MODE_ALIASES.get(candidate)
    if normalized is None:
        raise ValueError(f"Unsupported recording mode: {mode}")
    return normalized


def normalize_browser_name(browser: str | None) -> str | None:
    if browser is None:
        return None
    candidate = browser.strip().lower()
    if not candidate:
        return None
    return _BROWSER_ALIASES.get(candidate, candidate)


def detect_browser_type(
    app_name: str | None = None,
    process_name: str | None = None,
    window_title: str | None = None,
) -> str | None:
    haystack = " ".join(part for part in (app_name, process_name, window_title) if part).casefold()
    if not haystack:
        return None

    for signature, browser_type in _BROWSER_SIGNATURES.items():
        if signature in haystack:
            return browser_type
    return None


def enrich_window_info(window: WindowInfo | None, process_name: str | None = None) -> WindowInfo | None:
    if window is None:
        return None

    browser_type = window.browser_type or detect_browser_type(
        app_name=window.app_name,
        process_name=process_name,
        window_title=window.title,
    )
    if browser_type == window.browser_type:
        return window

    return window.model_copy(update={"browser_type": browser_type})


def infer_operation_platform(
    base_platform: str | None,
    *,
    active_window: WindowInfo | None = None,
    focused_element: UIElement | None = None,
    process_name: str | None = None,
) -> str:
    normalized_base = (base_platform or "").strip().lower()
    if normalized_base == "web":
        return "web"

    enriched_window = enrich_window_info(active_window, process_name=process_name)
    if _has_web_element_signals(focused_element):
        return "web"
    if enriched_window and (
        _looks_like_web_url(enriched_window.url) or enriched_window.tab_id or enriched_window.browser_type
    ):
        return "web"

    if normalized_base == "auto":
        return "desktop"
    return base_platform or "desktop"


def enrich_operation_context(
    context: OperationContext,
    *,
    fallback_platform: str | None = None,
) -> OperationContext:
    active_window = enrich_window_info(context.active_window, process_name=context.process_name)
    platform = infer_operation_platform(
        context.platform or fallback_platform,
        active_window=active_window,
        focused_element=context.focused_element,
        process_name=context.process_name,
    )

    updates = {}
    if active_window != context.active_window:
        updates["active_window"] = active_window
    if platform != context.platform:
        updates["platform"] = platform

    if not updates:
        return context
    return context.model_copy(update=updates)


def _has_web_element_signals(element: UIElement | None) -> bool:
    if element is None:
        return False
    return bool(
        element.selector
        or element.xpath
        or _looks_like_web_url(element.url)
        or element.frame
        or element.tab_id
    )


def _looks_like_web_url(url: str | None) -> bool:
    if not url:
        return False
    normalized = url.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _WEB_URL_PREFIXES)
