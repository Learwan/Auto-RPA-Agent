from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

from src.config import settings
from src.models.desktop import Rect, UIElement, WindowInfo
from src.models.operation import (
    FileEventData,
    FileOperation,
    KeyAction,
    KeyboardEventData,
    MouseAction,
    MouseButton,
    MouseEventData,
    NavigationEventData,
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)
from src.recorder.base import BaseRecorder

logger = logging.getLogger(__name__)

SUPPORTED_WEB_BROWSERS = {"chromium", "firefox", "webkit"}

WEB_RECORDER_INIT_SCRIPT = r"""
(() => {
  if (window.__autoAgentRecorderInstalled) {
    return;
  }
  window.__autoAgentRecorderInstalled = true;
  window.__autoAgentLastInteraction = null;
  const pendingNavigationStorageKey = "__autoAgentPendingNavigation";
  const readStoredNavigation = () => {
    try {
      const raw = window.sessionStorage.getItem(pendingNavigationStorageKey);
      if (!raw) {
        return null;
      }
      window.sessionStorage.removeItem(pendingNavigationStorageKey);
      return JSON.parse(raw);
    } catch {
      return null;
    }
  };
  const storePendingNavigation = (payload) => {
    try {
      if (!payload) {
        window.sessionStorage.removeItem(pendingNavigationStorageKey);
        return;
      }
      window.sessionStorage.setItem(pendingNavigationStorageKey, JSON.stringify(payload));
    } catch {
      return;
    }
  };
  window.__autoAgentPendingNavigation = readStoredNavigation();
  window.__autoAgentLastNavigation = {
    url: window.location.href,
    title: document.title,
  };

  const inputState = new Map();

  const safeEscape = (value) => {
    if (!value) {
      return "";
    }
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(value);
    }
    return String(value).replace(/[^a-zA-Z0-9_-]/g, (char) => `\\${char}`);
  };

  const quoteValue = (value) => String(value).replace(/"/g, '\\"');

  const buildSelector = (element) => {
    if (!(element instanceof Element)) {
      return null;
    }
    const dataTestId = element.getAttribute("data-testid") || element.getAttribute("data-test");
    if (dataTestId) {
      return `[data-testid="${quoteValue(dataTestId)}"]`;
    }
    if (element.id) {
      return `#${safeEscape(element.id)}`;
    }

    const parts = [];
    let node = element;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      let selector = node.tagName.toLowerCase();
      const name = node.getAttribute("name");
      if (name) {
        selector += `[name="${quoteValue(name)}"]`;
      }
      const classes = Array.from(node.classList || []).slice(0, 2);
      if (classes.length) {
        selector += classes.map((className) => `.${safeEscape(className)}`).join("");
      }
      if (node.parentElement) {
        const siblings = Array.from(node.parentElement.children).filter((child) => child.tagName === node.tagName);
        if (siblings.length > 1) {
          selector += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(selector);
      if (node.id || node.getAttribute("role") || node.getAttribute("name")) {
        break;
      }
      node = node.parentElement;
    }
    return parts.join(" > ") || null;
  };

  const buildXPath = (element) => {
    if (!(element instanceof Element)) {
      return null;
    }
    if (element.id) {
      return `//*[@id="${quoteValue(element.id)}"]`;
    }
    const segments = [];
    let node = element;
    while (node && node.nodeType === Node.ELEMENT_NODE) {
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === node.tagName) {
          index += 1;
        }
        sibling = sibling.previousElementSibling;
      }
      segments.unshift(`${node.tagName.toLowerCase()}[${index}]`);
      node = node.parentElement;
    }
    return `/${segments.join("/")}`;
  };

  const getFramePath = () => {
    const parts = [];
    let currentWindow = window;
    while (currentWindow && currentWindow.frameElement) {
      const frameEl = currentWindow.frameElement;
      parts.unshift(
        frameEl.getAttribute("name") ||
        frameEl.id ||
        frameEl.getAttribute("src") ||
        frameEl.getAttribute("title") ||
        "iframe"
      );
      currentWindow = currentWindow.parent;
    }
    return parts.length ? parts.join(" > ") : null;
  };

  const serializeElement = (element) => {
    if (!(element instanceof Element)) {
      return null;
    }
    const rect = element.getBoundingClientRect();
    return {
      role: (element.getAttribute("role") || element.tagName || "unknown").toLowerCase(),
      title: (element.innerText || element.getAttribute("aria-label") || element.getAttribute("title") || "").trim() || null,
      value: typeof element.value === "string" ? element.value : null,
      identifier: element.getAttribute("data-testid") || element.id || element.getAttribute("name") || null,
      description: element.getAttribute("aria-label") || element.getAttribute("placeholder") || null,
      className: typeof element.className === "string" ? element.className : null,
      selector: buildSelector(element),
      xpath: buildXPath(element),
      url: window.location.href,
      frame: getFramePath(),
      inputType: element.getAttribute("type") || null,
      tagName: element.tagName ? element.tagName.toLowerCase() : null,
      bounds: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
    };
  };

  const emit = (payload) => {
    if (typeof window._autoAgentRecord !== "function") {
      return;
    }
    window._autoAgentRecord({
      ...payload,
      pageTitle: document.title,
      url: window.location.href,
      timestamp: Date.now(),
    }).catch(() => {});
  };

  const rememberInteraction = (type, element) => {
    window.__autoAgentLastInteraction = {
      type,
      element: serializeElement(element),
      timestamp: Date.now(),
    };
  };

  const recentInteractionElement = () => {
    const interaction = window.__autoAgentLastInteraction;
    if (!interaction) {
      return serializeElement(document.activeElement);
    }
    if (Date.now() - interaction.timestamp > 5000) {
      return serializeElement(document.activeElement);
    }
    return interaction.element || serializeElement(document.activeElement);
  };

  const rememberNavigation = (transition, overrides = {}) => {
    const previous = window.__autoAgentLastNavigation || {
      url: window.location.href,
      title: document.title,
    };
    const current = {
      url: window.location.href,
      title: document.title,
    };
    const fromUrl = overrides.fromUrl ?? previous.url ?? null;
    const fromTitle = overrides.fromTitle ?? previous.title ?? null;
    const sameDocument = Boolean(overrides.sameDocument);
    const changed = current.url !== fromUrl || current.title !== fromTitle;

    window.__autoAgentLastNavigation = current;
    if (!changed) {
      window.__autoAgentPendingNavigation = null;
      storePendingNavigation(null);
      return;
    }

    window.__autoAgentPendingNavigation = {
      transition,
      sameDocument,
      fromUrl,
      fromTitle,
      element: recentInteractionElement(),
      timestamp: Date.now(),
    };
  };

  window.addEventListener("beforeunload", () => {
    storePendingNavigation({
      transition: "navigation",
      sameDocument: false,
      fromUrl: window.location.href,
      fromTitle: document.title,
      element: recentInteractionElement(),
      timestamp: Date.now(),
    });
  });

  const wrapHistoryMethod = (methodName, transition) => {
    const original = window.history[methodName];
    if (typeof original !== "function") {
      return;
    }
    window.history[methodName] = function (...args) {
      const before = {
        url: window.location.href,
        title: document.title,
      };
      const result = original.apply(this, args);
      queueMicrotask(() => {
        rememberNavigation(transition, {
          fromUrl: before.url,
          fromTitle: before.title,
          sameDocument: true,
        });
      });
      return result;
    };
  };

  wrapHistoryMethod("pushState", "push_state");
  wrapHistoryMethod("replaceState", "replace_state");

  window.addEventListener("popstate", () => {
    const previous = window.__autoAgentLastNavigation || {
      url: window.location.href,
      title: document.title,
    };
    queueMicrotask(() => {
      rememberNavigation("popstate", {
        fromUrl: previous.url,
        fromTitle: previous.title,
        sameDocument: true,
      });
    });
  });

  window.addEventListener("hashchange", (event) => {
    const previous = window.__autoAgentLastNavigation || {
      url: window.location.href,
      title: document.title,
    };
    queueMicrotask(() => {
      rememberNavigation("hash_change", {
        fromUrl: event.oldURL || previous.url,
        fromTitle: previous.title,
        sameDocument: true,
      });
    });
  });

  window.addEventListener("click", (event) => {
    rememberInteraction("click", event.target);
    emit({
      type: "click",
      x: Math.round(event.clientX),
      y: Math.round(event.clientY),
      button: event.button,
      detail: event.detail || 1,
      element: serializeElement(event.target),
    });
  }, true);

  window.addEventListener("input", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.type === "file") {
      return;
    }
    const element = serializeElement(event.target);
    if (!element) {
      return;
    }
    const key = element.selector || element.xpath || element.identifier || element.title || element.tagName || "input";
    const currentValue = typeof event.target.value === "string" ? event.target.value : "";
    const previousValue = inputState.get(key) || "";
    let delta = null;
    if (currentValue.startsWith(previousValue)) {
      delta = currentValue.slice(previousValue.length);
    } else if (typeof event.data === "string" && event.data) {
      delta = event.data;
    } else {
      delta = currentValue;
    }
    inputState.set(key, currentValue);
    emit({
      type: "input",
      text: delta,
      value: currentValue,
      inputType: event.inputType || null,
      element,
    });
  }, true);

  window.addEventListener("change", (event) => {
    if (!(event.target instanceof HTMLInputElement) || event.target.type !== "file") {
      return;
    }
    const element = serializeElement(event.target);
    const files = Array.from(event.target.files || []).map((file) => ({
      name: file.name,
      size: file.size,
      type: file.type || null,
    }));
    rememberInteraction("file_upload", event.target);
    emit({
      type: "file_upload",
      value: typeof event.target.value === "string" ? event.target.value : "",
      fileNames: files.map((file) => file.name),
      files,
      multiple: event.target.multiple,
      element,
    });
  }, true);

  window.addEventListener("keydown", (event) => {
    const modifiers = [];
    if (event.metaKey) modifiers.push("Meta");
    if (event.ctrlKey) modifiers.push("Control");
    if (event.altKey) modifiers.push("Alt");
    if (event.shiftKey) modifiers.push("Shift");
    if (!modifiers.length && event.key !== "Tab") {
      return;
    }
    rememberInteraction("hotkey", document.activeElement);
    emit({
      type: "hotkey",
      key: event.key,
      modifiers,
      element: serializeElement(document.activeElement),
    });
  }, true);
})();
"""


class WebRecorder(BaseRecorder):
    def __init__(
        self,
        start_url: str | None = None,
        browser_name: str | None = None,
        headless: bool = False,
    ):
        super().__init__()
        self._session_id = ""
        self._seq = 0
        self._start_url = start_url or "about:blank"
        self._browser_name = (browser_name or settings.WEB_BROWSER).lower()
        self._headless = headless
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._tab_ids: dict[int, str] = {}
        self._last_window: WindowInfo | None = None
        self._last_navigation_fingerprints: dict[str, tuple[tuple[str, str, str, str], int]] = {}

    def start(self, session_id: str, callback) -> None:
        self._session_id = session_id
        self._callback = callback
        self._running = True
        self._seq = 0
        self._tab_ids = {}
        self._last_window = None
        self._last_navigation_fingerprints = {}
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.ensure_future(self._run())

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        try:
            from playwright.async_api import async_playwright

            if self._browser_name not in SUPPORTED_WEB_BROWSERS:
                raise RuntimeError(f"Unsupported web recorder browser: {self._browser_name}")

            self._playwright = await async_playwright().start()
            launcher = getattr(self._playwright, self._browser_name)
            self._browser = await launcher.launch(headless=self._headless)
            self._context = await self._browser.new_context(viewport=settings.web_viewport)
            self._context.set_default_timeout(settings.WEB_TIMEOUT_MS)
            await self._context.expose_binding("_autoAgentRecord", self._handle_binding)
            await self._context.add_init_script(WEB_RECORDER_INIT_SCRIPT)
            self._context.on("page", lambda page: self._schedule(self._register_page(page)))

            page = await self._context.new_page()
            await self._register_page(page)
            await page.goto(self._start_url, wait_until="domcontentloaded")

            while self._running:
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Web recorder failed: {exc}")
        finally:
            await self._close()

    async def _register_page(self, page) -> None:
        page_key = id(page)
        if page_key not in self._tab_ids:
            self._tab_ids[page_key] = uuid4().hex

        page.on("close", lambda: self._tab_ids.pop(page_key, None))
        page.on("framenavigated", lambda frame: self._schedule(self._on_frame_navigated(page, frame)))
        page.on("download", lambda download: self._schedule(self._on_download(page, download)))

    async def _on_frame_navigated(self, page, frame) -> None:
        if frame != page.main_frame:
            return

        interaction = await self._get_last_interaction(page)
        previous_window = self._last_window
        pending_navigation = await self._get_pending_navigation(page)
        payload = {
            "type": "navigation",
            "transition": pending_navigation.get("transition") if pending_navigation else "navigation",
            "sameDocument": bool(pending_navigation.get("sameDocument", False)) if pending_navigation else False,
            "pageTitle": await self._safe_page_title(page),
            "url": frame.url,
            "timestamp": int(time.time() * 1000),
            "fromUrl": (
                pending_navigation.get("fromUrl")
                if pending_navigation and pending_navigation.get("fromUrl")
                else previous_window.url
                if previous_window
                else None
            ),
            "fromTitle": (
                pending_navigation.get("fromTitle")
                if pending_navigation and pending_navigation.get("fromTitle")
                else previous_window.title
                if previous_window
                else None
            ),
            "element": (
                pending_navigation.get("element")
                if pending_navigation and pending_navigation.get("element")
                else interaction.get("element")
                if interaction
                else None
            ),
        }
        await self._handle_web_payload(payload, tab_id=self._tab_ids.get(id(page)))

    async def _on_download(self, page, download) -> None:
        interaction = await self._get_last_interaction(page)
        payload = {
            "type": "download",
            "pageTitle": await self._safe_page_title(page),
            "url": page.url,
            "timestamp": int(time.time() * 1000),
            "suggestedFilename": download.suggested_filename,
            "element": interaction.get("element") if interaction else None,
        }
        await self._handle_web_payload(payload, tab_id=self._tab_ids.get(id(page)))

    async def _get_last_interaction(self, page) -> dict | None:
        try:
            interaction = await page.evaluate("() => window.__autoAgentLastInteraction || null")
        except Exception:
            return None
        if not interaction:
            return None
        age_ms = int(time.time() * 1000) - int(interaction.get("timestamp", 0))
        if age_ms > 5000:
            return None
        return interaction

    async def _get_pending_navigation(self, page) -> dict | None:
        try:
            payload = await page.evaluate(
                """() => {
                    const pending = window.__autoAgentPendingNavigation || null;
                    window.__autoAgentPendingNavigation = null;
                    return pending;
                }"""
            )
        except Exception:
            return None
        if not payload:
            return None
        age_ms = int(time.time() * 1000) - int(payload.get("timestamp", 0))
        if age_ms > 5000:
            return None
        return payload

    async def _handle_binding(self, source, payload: dict) -> None:
        page = source.get("page")
        tab_id = self._tab_ids.get(id(page)) if page is not None else None
        await self._handle_web_payload(payload, tab_id=tab_id)

    async def _handle_web_payload(self, payload: dict, tab_id: str | None = None) -> None:
        if not self._running or not self._callback:
            return

        window = self._build_window_info(payload, tab_id=tab_id)
        previous_window = self._last_window
        self._emit_window_switch_if_needed(window, payload.get("type", "web"), payload.get("timestamp"))

        event_type = payload.get("type")
        if event_type == "click":
            self._emit_mouse_click(payload, window)
        elif event_type == "input":
            self._emit_text_input(payload, window)
        elif event_type == "hotkey":
            self._emit_hotkey(payload, window)
        elif event_type == "navigation":
            self._emit_navigation(payload, previous_window, window)
        elif event_type == "file_upload":
            self._emit_file_upload(payload, window)
        elif event_type == "download":
            self._emit_download(payload, window)

    def _emit_window_switch_if_needed(self, window: WindowInfo, method: str, timestamp: int | None) -> None:
        if self._last_window is not None:
            same_tab = self._last_window.tab_id == window.tab_id
            if same_tab:
                self._last_window = window
                return

        self._emit_operation(
            OperationType.WINDOW_SWITCH,
            WindowEventData(from_window=self._last_window, to_window=window, method=method),
            OperationContext(active_window=window, process_name=self._browser_name, process_pid=0, platform="web"),
            int(timestamp or time.time() * 1000),
        )
        self._last_window = window

    def _emit_navigation(self, payload: dict, previous_window: WindowInfo | None, window: WindowInfo) -> None:
        timestamp = int(payload.get("timestamp", time.time() * 1000))
        if previous_window is None and window.url == self._start_url:
            return
        if (
            previous_window is not None
            and previous_window.url == window.url
            and previous_window.title == window.title
            and payload.get("fromUrl") in (None, "")
            and payload.get("fromTitle") in (None, "")
        ):
            return

        from_url = payload.get("fromUrl") or (previous_window.url if previous_window else None)
        from_title = payload.get("fromTitle") or (previous_window.title if previous_window else None)
        navigation_key = window.tab_id or window.window_id or "default"
        fingerprint = (
            str(from_url or ""),
            str(from_title or ""),
            str(window.url or self._start_url),
            str(window.title or ""),
        )
        previous_fingerprint = self._last_navigation_fingerprints.get(navigation_key)
        if previous_fingerprint is not None:
            last_fingerprint, last_timestamp = previous_fingerprint
            if last_fingerprint == fingerprint and (timestamp - last_timestamp) < 1000:
                return
        self._last_navigation_fingerprints[navigation_key] = (fingerprint, timestamp)

        self._emit_operation(
            OperationType.NAVIGATION,
            NavigationEventData(
                url=window.url or self._start_url,
                title=window.title,
                transition=str(payload.get("transition") or "navigation"),
                from_url=str(from_url) if from_url else None,
                from_title=str(from_title) if from_title else None,
                same_document=bool(payload.get("sameDocument", False)),
            ),
            self._build_context(window, payload),
            timestamp,
        )

    def _emit_file_upload(self, payload: dict, window: WindowInfo) -> None:
        file_names = [str(name) for name in payload.get("fileNames", []) if name]
        if not file_names:
            raw_value = str(payload.get("value") or "")
            if raw_value:
                file_names = [raw_value.split("/")[-1].split("\\")[-1]]
        if not file_names:
            return

        file_entries = payload.get("files") or []
        first_file = file_entries[0] if file_entries else {}
        self._emit_operation(
            OperationType.FILE_OP,
            FileEventData(
                operation=FileOperation.UPLOAD,
                src_path=file_names[0],
                paths=file_names,
                file_type=first_file.get("type"),
                size=first_file.get("size"),
            ),
            self._build_context(window, payload),
            int(payload.get("timestamp", time.time() * 1000)),
        )

    def _emit_download(self, payload: dict, window: WindowInfo) -> None:
        suggested_filename = str(payload.get("suggestedFilename") or "download.bin")
        self._emit_operation(
            OperationType.FILE_OP,
            FileEventData(
                operation=FileOperation.DOWNLOAD,
                src_path=suggested_filename,
                dest_path=f"~/Downloads/{suggested_filename}",
            ),
            self._build_context(window, payload),
            int(payload.get("timestamp", time.time() * 1000)),
        )

    def _emit_mouse_click(self, payload: dict, window: WindowInfo) -> None:
        button_code = int(payload.get("button", 0))
        button = MouseButton.LEFT
        action = MouseAction.CLICK
        if button_code == 1:
            button = MouseButton.MIDDLE
        elif button_code == 2:
            button = MouseButton.RIGHT
            action = MouseAction.RIGHT_CLICK
        elif int(payload.get("detail", 1)) >= 2:
            action = MouseAction.DOUBLE_CLICK

        self._emit_operation(
            OperationType.MOUSE_CLICK,
            MouseEventData(
                x=int(payload.get("x", 0)),
                y=int(payload.get("y", 0)),
                button=button,
                action=action,
            ),
            self._build_context(window, payload),
            int(payload.get("timestamp", time.time() * 1000)),
        )

    def _emit_text_input(self, payload: dict, window: WindowInfo) -> None:
        text = payload.get("text")
        if text in (None, ""):
            text = payload.get("value", "")
        if text in (None, ""):
            return

        self._emit_operation(
            OperationType.KEY_INPUT,
            KeyboardEventData(key="text_input", action=KeyAction.INPUT, text=str(text)),
            self._build_context(window, payload),
            int(payload.get("timestamp", time.time() * 1000)),
        )

    def _emit_hotkey(self, payload: dict, window: WindowInfo) -> None:
        key = payload.get("key")
        if not key:
            return

        self._emit_operation(
            OperationType.KEY_PRESS,
            KeyboardEventData(
                key=str(key),
                action=KeyAction.HOTKEY,
                modifiers=[str(item) for item in payload.get("modifiers", [])],
            ),
            self._build_context(window, payload),
            int(payload.get("timestamp", time.time() * 1000)),
        )

    def _emit_operation(self, op_type: OperationType, data, context: OperationContext, timestamp: int) -> None:
        self._seq += 1
        self._callback(
            OperationEvent(
                id=uuid4().hex,
                session_id=self._session_id,
                seq_num=self._seq,
                timestamp=timestamp,
                type=op_type,
                data=data,
                context=context,
            )
        )

    def _build_context(self, window: WindowInfo, payload: dict) -> OperationContext:
        return OperationContext(
            active_window=window,
            focused_element=self._build_element(payload.get("element"), window),
            process_name=self._browser_name,
            process_pid=0,
            platform="web",
        )

    def _build_window_info(self, payload: dict, tab_id: str | None = None) -> WindowInfo:
        title = str(payload.get("pageTitle") or payload.get("title") or payload.get("url") or "Browser")
        url = str(payload.get("url") or self._start_url)
        tab_identifier = tab_id or str(payload.get("tabId") or uuid4().hex)
        viewport = settings.web_viewport
        return WindowInfo(
            window_id=tab_identifier,
            title=title,
            app_name=self._browser_name,
            pid=0,
            bounds=Rect(x=0, y=0, width=viewport["width"], height=viewport["height"]),
            is_active=True,
            is_visible=True,
            url=url,
            browser_type=self._browser_name,
            tab_id=tab_identifier,
        )

    def _build_element(self, element_payload: dict | None, window: WindowInfo) -> UIElement | None:
        if not element_payload:
            return None

        bounds = None
        bounds_payload = element_payload.get("bounds")
        if bounds_payload:
            bounds = Rect(
                x=int(bounds_payload.get("x", 0)),
                y=int(bounds_payload.get("y", 0)),
                width=int(bounds_payload.get("width", 0)),
                height=int(bounds_payload.get("height", 0)),
            )

        return UIElement(
            role=str(element_payload.get("role") or "unknown"),
            title=element_payload.get("title"),
            value=element_payload.get("value"),
            identifier=element_payload.get("identifier"),
            description=element_payload.get("description"),
            bounds=bounds,
            class_name=element_payload.get("className"),
            selector=element_payload.get("selector"),
            xpath=element_payload.get("xpath"),
            url=element_payload.get("url") or window.url,
            frame=element_payload.get("frame"),
            tab_id=window.tab_id,
            input_type=element_payload.get("inputType"),
            tag_name=element_payload.get("tagName"),
        )

    async def _safe_page_title(self, page) -> str:
        try:
            return await page.title()
        except Exception:
            return page.url

    def _schedule(self, coro) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._task = None
