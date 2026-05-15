from src.models.desktop import Rect, UIElement, WindowInfo
from src.models.operation import OperationContext
from src.recorder.context_inference import (
    enrich_operation_context,
    normalize_browser_name,
    normalize_recording_mode,
)


def _window(app_name: str, *, title: str = "Demo", browser_type: str | None = None) -> WindowInfo:
    return WindowInfo(
        window_id="win-1",
        title=title,
        app_name=app_name,
        pid=123,
        bounds=Rect(x=0, y=0, width=1280, height=720),
        is_active=True,
        browser_type=browser_type,
    )


def test_normalize_recording_mode_accepts_auto_and_browser_aliases():
    assert normalize_recording_mode("auto") == "auto"
    assert normalize_recording_mode("browser") == "web"
    assert normalize_recording_mode("hybrid") == "auto"
    assert normalize_browser_name("chrome") == "chromium"


def test_enrich_operation_context_marks_browser_window_as_web():
    context = OperationContext(
        active_window=_window("Google Chrome"),
        process_name="Google Chrome",
        platform="macos",
    )

    enriched = enrich_operation_context(context)

    assert enriched.platform == "web"
    assert enriched.active_window is not None
    assert enriched.active_window.browser_type == "chromium"


def test_enrich_operation_context_marks_selector_backed_element_as_web():
    context = OperationContext(
        focused_element=UIElement(role="button", title="提交", selector="[data-testid='submit']"),
        platform="macos",
    )

    enriched = enrich_operation_context(context)

    assert enriched.platform == "web"


def test_enrich_operation_context_keeps_desktop_app_as_desktop():
    context = OperationContext(
        active_window=_window("Finder"),
        process_name="Finder",
        platform="macos",
    )

    enriched = enrich_operation_context(context)

    assert enriched.platform == "macos"
    assert enriched.active_window is not None
    assert enriched.active_window.browser_type is None
