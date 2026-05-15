from __future__ import annotations

import json
import subprocess
from typing import Any

from src.models.desktop import Rect, UIElement, WindowInfo

_SCRIPT_TIMEOUT_SECONDS = 0.5

_BROWSER_SPECS: dict[str, dict[str, str]] = {
    "Google Chrome": {"browser_type": "chromium", "family": "chromium"},
    "Chromium": {"browser_type": "chromium", "family": "chromium"},
    "Brave Browser": {"browser_type": "chromium", "family": "chromium"},
    "Microsoft Edge": {"browser_type": "chromium", "family": "chromium"},
    "Vivaldi": {"browser_type": "chromium", "family": "chromium"},
    "Opera": {"browser_type": "chromium", "family": "chromium"},
    "Arc": {"browser_type": "chromium", "family": "chromium"},
    "Safari": {"browser_type": "webkit", "family": "safari"},
}

_SERIALIZER_JS = r"""
(() => {
  const safeEscape = (value) => {
    if (!value) return "";
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, (char) => `\\${char}`);
  };
  const quoteValue = (value) => String(value).replace(/"/g, '\\"');
  const buildSelector = (element) => {
    if (!(element instanceof Element)) return null;
    const dataTestId = element.getAttribute("data-testid") || element.getAttribute("data-test");
    if (dataTestId) return `[data-testid="${quoteValue(dataTestId)}"]`;
    if (element.id) return `#${safeEscape(element.id)}`;
    const parts = [];
    let node = element;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      let selector = node.tagName.toLowerCase();
      const name = node.getAttribute("name");
      if (name) selector += `[name="${quoteValue(name)}"]`;
      const classes = Array.from(node.classList || []).slice(0, 2);
      if (classes.length) selector += classes.map((className) => `.${safeEscape(className)}`).join("");
      if (node.parentElement) {
        const siblings = Array.from(node.parentElement.children).filter((child) => child.tagName === node.tagName);
        if (siblings.length > 1) selector += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(selector);
      if (node.id || node.getAttribute("role") || node.getAttribute("name")) break;
      node = node.parentElement;
    }
    return parts.join(" > ") || null;
  };
  const buildXPath = (element) => {
    if (!(element instanceof Element)) return null;
    if (element.id) return `//*[@id="${quoteValue(element.id)}"]`;
    const segments = [];
    let node = element;
    while (node && node.nodeType === Node.ELEMENT_NODE) {
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === node.tagName) index += 1;
        sibling = sibling.previousElementSibling;
      }
      segments.unshift(`${node.tagName.toLowerCase()}[${index}]`);
      node = node.parentElement;
    }
    return `/${segments.join("/")}`;
  };
  const viewportLeft = Math.round(window.screenX + Math.max(0, (window.outerWidth - window.innerWidth) / 2));
  const viewportTop = Math.round(window.screenY + Math.max(0, window.outerHeight - window.innerHeight));
  const serializeElement = (element) => {
    if (!(element instanceof Element)) return null;
    const rect = element.getBoundingClientRect();
    return {
      role: (element.getAttribute("role") || element.tagName || "unknown").toLowerCase(),
      title: (
        element.innerText ||
        element.getAttribute("aria-label") ||
        element.getAttribute("title") ||
        ""
      ).trim() || null,
      value: typeof element.value === "string" ? element.value : null,
      identifier: element.getAttribute("data-testid") || element.id || element.getAttribute("name") || null,
      description: element.getAttribute("aria-label") || element.getAttribute("placeholder") || null,
      className: typeof element.className === "string" ? element.className : null,
      selector: buildSelector(element),
      xpath: buildXPath(element),
      url: window.location.href,
      frame: null,
      inputType: element.getAttribute("type") || null,
      tagName: element.tagName ? element.tagName.toLowerCase() : null,
      isEnabled: !element.hasAttribute("disabled") && element.getAttribute("aria-disabled") !== "true",
      isVisible: !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length),
      isFocusable: typeof element.focus === "function",
      isFocused: document.activeElement === element,
      bounds: {
        x: Math.round(rect.x + viewportLeft),
        y: Math.round(rect.y + viewportTop),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
    };
  };
  const elementAtScreenPoint = (screenX, screenY) => {
    const clientX = Math.round(screenX - viewportLeft);
    const clientY = Math.round(screenY - viewportTop);
    if (clientX < 0 || clientY < 0 || clientX > window.innerWidth || clientY > window.innerHeight) return null;
    return serializeElement(document.elementFromPoint(clientX, clientY));
  };
  return { serializeElement, elementAtScreenPoint };
})()
"""


def enrich_browser_window(window: WindowInfo | None) -> WindowInfo | None:
    if window is None:
        return None
    spec = _BROWSER_SPECS.get(window.app_name)
    if spec is None:
        return window

    payload = _run_browser_json(
        window.app_name,
        (
            "JSON.stringify({title: document.title || null, "
            "url: window.location.href || null, tabId: window.name || null})"
        ),
    )
    if payload is None:
        if window.browser_type == spec["browser_type"]:
            return window
        return window.model_copy(update={"browser_type": spec["browser_type"]})

    return window.model_copy(
        update={
            "title": str(payload.get("title") or window.title),
            "url": _string_or_none(payload.get("url")) or window.url,
            "browser_type": spec["browser_type"],
            "tab_id": _string_or_none(payload.get("tabId")) or window.tab_id,
        }
    )


def get_browser_focused_element(app_name: str) -> UIElement | None:
    if app_name not in _BROWSER_SPECS:
        return None
    payload = _run_browser_json(
        app_name,
        "JSON.stringify(__copilotBrowserContext.serializeElement(document.activeElement))",
    )
    return _payload_to_element(payload)


def get_browser_element_at_screen_point(app_name: str, x: int, y: int) -> UIElement | None:
    if app_name not in _BROWSER_SPECS:
        return None
    payload = _run_browser_json(
        app_name,
        f"JSON.stringify(__copilotBrowserContext.elementAtScreenPoint({int(x)}, {int(y)}))",
    )
    return _payload_to_element(payload)


def _run_browser_json(app_name: str, expression: str) -> dict[str, Any] | None:
    spec = _BROWSER_SPECS.get(app_name)
    if spec is None:
        return None

    bootstrap = f"if (!window.__copilotBrowserContext) window.__copilotBrowserContext = {_SERIALIZER_JS};"
    script = f"{bootstrap} {expression}"
    result = _execute_browser_javascript(app_name, spec["family"], script)
    if not result:
        return None
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _execute_browser_javascript(app_name: str, family: str, script: str) -> str | None:
    script_literal = json.dumps(script)
    if family == "safari":
        applescript = f'''
        tell application "{app_name}"
            if (count of windows) is 0 then return ""
            return do JavaScript {script_literal} in current tab of front window
        end tell
        '''
    else:
        applescript = f'''
        tell application "{app_name}"
            if (count of windows) is 0 then return ""
            return execute active tab of front window javascript {script_literal}
        end tell
        '''

    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _payload_to_element(payload: dict[str, Any] | None) -> UIElement | None:
    if not payload:
        return None

    role = str(payload.get("role") or "").strip()
    if not role:
        return None

    bounds = None
    raw_bounds = payload.get("bounds")
    if isinstance(raw_bounds, dict):
        try:
            bounds = Rect(
                x=int(raw_bounds["x"]),
                y=int(raw_bounds["y"]),
                width=int(raw_bounds["width"]),
                height=int(raw_bounds["height"]),
            )
        except (KeyError, TypeError, ValueError):
            bounds = None

    return UIElement(
        role=role,
        title=_string_or_none(payload.get("title")),
        value=_string_or_none(payload.get("value")),
        identifier=_string_or_none(payload.get("identifier")),
        description=_string_or_none(payload.get("description")),
        bounds=bounds,
        class_name=_string_or_none(payload.get("className")),
        selector=_string_or_none(payload.get("selector")),
        xpath=_string_or_none(payload.get("xpath")),
        url=_string_or_none(payload.get("url")),
        frame=_string_or_none(payload.get("frame")),
        input_type=_string_or_none(payload.get("inputType")),
        tag_name=_string_or_none(payload.get("tagName")),
        is_enabled=bool(payload.get("isEnabled", True)),
        is_visible=bool(payload.get("isVisible", True)),
        is_focusable=bool(payload.get("isFocusable", True)),
        is_focused=bool(payload.get("isFocused", False)),
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
