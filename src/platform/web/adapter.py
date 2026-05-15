from __future__ import annotations

import contextlib
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from PIL import Image

from src.config import settings
from src.models.automation import StepTarget
from src.models.desktop import ElementCriteria, Rect, UIElement, WindowInfo
from src.platform.base import BasePlatformAdapter
from src.platform.screenshot import pil_image_to_png_bytes

PLAYWRIGHT_KEY_MAP = {
    "cmd": "Meta",
    "command": "Meta",
    "ctrl": "Control",
    "control": "Control",
    "option": "Alt",
    "alt": "Alt",
    "esc": "Escape",
    "enter": "Enter",
    "return": "Enter",
    "space": "Space",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
}


class WebAdapter(BasePlatformAdapter):
    def __init__(self):
        self._browser_name = settings.WEB_BROWSER
        self._headless = settings.WEB_HEADLESS
        self._timeout_ms = settings.WEB_TIMEOUT_MS
        self._viewport = settings.web_viewport
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._current_page: Any = None

    async def _start_playwright(self):
        if self._playwright is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Web automation requires Playwright. Install with `pip install '.[web]'` and run `playwright install chromium`."
                ) from exc

            self._playwright = await async_playwright().start()
        return self._playwright

    async def _ensure_browser(self):
        if self._browser is not None:
            return

        playwright = await self._start_playwright()
        launcher = getattr(playwright, self._browser_name, None)
        if launcher is None:
            raise RuntimeError(f"Unsupported Playwright browser: {self._browser_name}")

        self._browser = await launcher.launch(headless=self._headless)
        self._context = await self._browser.new_context(viewport=self._viewport)
        self._context.set_default_timeout(self._timeout_ms)

    async def _ensure_page(self, url: str | None = None):
        await self._ensure_browser()
        if self._current_page is None:
            self._current_page = await self._context.new_page()

        if url and self._current_page.url != url:
            await self._current_page.goto(url, wait_until="domcontentloaded")
        return self._current_page

    async def _get_pages(self) -> list[Any]:
        if self._context is None:
            return []
        return list(self._context.pages)

    @staticmethod
    def _criteria_from_target(target: StepTarget | None) -> ElementCriteria:
        if target is None:
            return ElementCriteria()
        return ElementCriteria(
            accessibility_id=target.accessibility_id,
            title=target.title,
            role=target.role,
            class_name=target.class_name,
            selector=target.selector,
            xpath=target.xpath,
            url=target.url,
            frame=target.frame,
            text_contains=target.text_contains,
            position=target.position,
        )

    async def _resolve_scope(self, page, frame_name: str | None):
        if not frame_name:
            return page

        frame = page.frame(name=frame_name)
        if frame is not None:
            return frame

        for item in page.frames:
            if frame_name in item.url:
                return item

        raise RuntimeError(f"Frame not found: {frame_name}")

    async def _resolve_locator(self, criteria: ElementCriteria):
        page = await self._ensure_page(criteria.url)
        scope = await self._resolve_scope(page, criteria.frame)

        locator = None
        if criteria.selector:
            locator = scope.locator(criteria.selector).first
        elif criteria.xpath:
            locator = scope.locator(f"xpath={criteria.xpath}").first
        elif criteria.accessibility_id:
            value = criteria.accessibility_id.replace('"', '\\"')
            locator = scope.locator(
                f'[data-testid="{value}"], [data-test="{value}"], [id="{value}"], [name="{value}"]'
            ).first
        elif criteria.text_contains:
            locator = scope.get_by_text(criteria.text_contains).first
        elif criteria.title:
            locator = scope.get_by_text(criteria.title, exact=True).first
        elif criteria.role:
            role_name = criteria.title or criteria.text_contains
            try:
                if role_name:
                    locator = scope.get_by_role(criteria.role, name=role_name).first
                else:
                    locator = scope.get_by_role(criteria.role).first
            except Exception:
                locator = scope.locator(f'[role="{criteria.role}"]').first
        elif criteria.class_name:
            selector = "." + ".".join(part for part in criteria.class_name.split() if part)
            locator = scope.locator(selector).first

        if locator is None:
            return page, None

        if await locator.count() == 0:
            return page, None
        return page, locator

    @staticmethod
    async def _locator_to_element(locator) -> UIElement:
        payload = await locator.evaluate(
            """
            (element) => ({
                role: (element.getAttribute('role') || element.tagName || 'unknown').toLowerCase(),
                title: (element.innerText || element.getAttribute('aria-label') || element.getAttribute('title') || '').trim() || null,
                value: 'value' in element ? String(element.value ?? '') : null,
                identifier: element.getAttribute('data-testid') || element.id || element.getAttribute('name') || null,
                description: element.getAttribute('aria-label') || element.getAttribute('placeholder') || null,
                className: typeof element.className === 'string' ? element.className : null,
                isEnabled: !element.hasAttribute('disabled') && element.getAttribute('aria-disabled') !== 'true',
                isVisible: !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length),
                isFocusable: typeof element.focus === 'function',
                isFocused: document.activeElement === element,
            })
            """
        )
        box = await locator.bounding_box()
        if box:
            payload["bounds"] = {
                "x": int(box["x"]),
                "y": int(box["y"]),
                "width": int(box["width"]),
                "height": int(box["height"]),
            }
        return WebAdapter._payload_to_element(payload)

    @staticmethod
    def _payload_to_element(payload: dict[str, Any]) -> UIElement:
        bounds = None
        box = payload.get("bounds")
        if box:
            bounds = Rect(
                x=int(box["x"]),
                y=int(box["y"]),
                width=int(box["width"]),
                height=int(box["height"]),
            )
        return UIElement(
            role=payload["role"],
            title=payload["title"],
            value=payload["value"],
            identifier=payload["identifier"],
            description=payload["description"],
            bounds=bounds,
            class_name=payload["className"],
            is_enabled=payload["isEnabled"],
            is_visible=payload["isVisible"],
            is_focusable=payload["isFocusable"],
            is_focused=payload["isFocused"],
        )

    def _window_info_from_page(self, page, page_index: int, title: str) -> WindowInfo:
        parsed = urlparse(page.url or "")
        app_name = parsed.netloc or "browser"
        viewport = page.viewport_size or settings.web_viewport
        return WindowInfo(
            window_id=id(page),
            title=title or page.url or f"tab-{page_index}",
            app_name=app_name,
            pid=0,
            bounds=Rect(x=0, y=0, width=viewport["width"], height=viewport["height"]),
            is_active=False,
            is_visible=not page.is_closed(),
            url=page.url or None,
            browser_type=self._browser_name,
            tab_id=str(id(page)),
        )

    async def get_windows(self) -> list[WindowInfo]:
        windows: list[WindowInfo] = []
        for index, page in enumerate(await self._get_pages()):
            try:
                title = await page.title()
            except Exception:
                title = page.url
            info = self._window_info_from_page(page, index, title)
            info.is_active = page == self._current_page
            windows.append(info)
        return windows

    async def get_active_window(self) -> WindowInfo | None:
        if self._current_page is None:
            return None
        try:
            title = await self._current_page.title()
        except Exception:
            title = self._current_page.url
        info = self._window_info_from_page(self._current_page, 0, title)
        info.is_active = True
        return info

    async def get_processes(self) -> list:
        return []

    async def get_focused_element(self) -> UIElement | None:
        page = await self._ensure_page()
        handle = await page.evaluate_handle("() => document.activeElement")
        element = handle.as_element()
        if element is None:
            return None
        locator = page.locator(":focus")
        if await locator.count() == 0:
            return None
        return await self._locator_to_element(locator.first)

    async def capture_screen(self, region: Rect | None = None) -> bytes:
        page = await self._ensure_page()
        png_bytes = await page.screenshot(type="png")
        image = Image.open(BytesIO(png_bytes)).convert("RGB")
        if region is not None:
            image = image.crop((region.x, region.y, region.x + region.width, region.y + region.height))
        return pil_image_to_png_bytes(image)

    async def get_element_at_position(self, x: int, y: int) -> UIElement | None:
        page = await self._ensure_page()
        payload = await page.evaluate(
            """
            ([px, py]) => {
                const element = document.elementFromPoint(px, py);
                if (!element) {
                    return null;
                }
                const rect = element.getBoundingClientRect();
                return {
                    role: (element.getAttribute('role') || element.tagName || 'unknown').toLowerCase(),
                    title: (element.innerText || element.getAttribute('aria-label') || element.getAttribute('title') || '').trim() || null,
                    value: 'value' in element ? String(element.value ?? '') : null,
                    identifier: element.getAttribute('data-testid') || element.id || element.getAttribute('name') || null,
                    description: element.getAttribute('aria-label') || element.getAttribute('placeholder') || null,
                    className: typeof element.className === 'string' ? element.className : null,
                    isEnabled: !element.hasAttribute('disabled') && element.getAttribute('aria-disabled') !== 'true',
                    isVisible: !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length),
                    isFocusable: typeof element.focus === 'function',
                    isFocused: document.activeElement === element,
                    bounds: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                    },
                };
            }
            """,
            [x, y],
        )
        if payload is None:
            return None
        return self._payload_to_element(payload)

    async def find_element(self, criteria: ElementCriteria) -> UIElement | None:
        if criteria.position is not None:
            return await self.get_element_at_position(criteria.position.x, criteria.position.y)

        page, locator = await self._resolve_locator(criteria)
        if locator is None:
            return None
        return await self._locator_to_element(locator)

    def check_permissions(self) -> dict[str, bool]:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return {"playwright": False}
        return {"playwright": True}

    def get_platform_name(self) -> str:
        return "web"

    async def get_screen_size(self) -> tuple[int, int]:
        if self._current_page is not None and self._current_page.viewport_size:
            size = self._current_page.viewport_size
            return size["width"], size["height"]
        return self._viewport["width"], self._viewport["height"]

    async def activate_window(self, target_window: WindowInfo) -> bool:
        for page in await self._get_pages():
            if id(page) == target_window.window_id:
                await page.bring_to_front()
                self._current_page = page
                return True

            try:
                title = await page.title()
            except Exception:
                title = page.url

            if (
                title == target_window.title
                or page.url == target_window.title
                or (target_window.url and page.url == target_window.url)
                or (target_window.tab_id and target_window.tab_id == str(id(page)))
            ):
                await page.bring_to_front()
                self._current_page = page
                return True
        return False

    async def navigate(self, target: StepTarget | None, action: dict) -> None:
        await self._ensure_browser()
        url = str(action.get("url") or (target.url if target else "")).strip()
        if not url:
            raise RuntimeError("No URL specified for navigate")

        if bool(action.get("new_tab", False)) or self._current_page is None:
            self._current_page = await self._context.new_page()

        page = self._current_page
        wait_until = str(action.get("wait_until", "domcontentloaded"))
        navigated_in_place = False
        if bool(action.get("same_document", False)):
            navigated_in_place = await self._navigate_same_document(page, url, action)

        if not navigated_in_place:
            await page.goto(url, wait_until=wait_until)

        wait_for = action.get("wait_for")
        if wait_for:
            await page.locator(str(wait_for)).first.wait_for(
                state=str(action.get("wait_state", "visible")),
                timeout=int(action.get("timeout_ms", self._timeout_ms)),
            )

    @staticmethod
    def _normalize_url(url: str | None) -> str:
        return str(url or "").strip()

    @classmethod
    def _urls_match(cls, left: str | None, right: str | None) -> bool:
        return cls._normalize_url(left) == cls._normalize_url(right)

    @staticmethod
    def _is_same_origin(left: str, right: str) -> bool:
        left_parsed = urlparse(left)
        right_parsed = urlparse(right)
        return bool(
            left_parsed.scheme
            and left_parsed.netloc
            and left_parsed.scheme == right_parsed.scheme
            and left_parsed.netloc == right_parsed.netloc
        )

    async def _navigate_same_document(self, page, url: str, action: dict) -> bool:
        current_url = self._normalize_url(getattr(page, "url", ""))
        if not current_url:
            return False

        resolved_url = urljoin(current_url, url)
        if not self._is_same_origin(current_url, resolved_url):
            return False

        from_url = self._normalize_url(action.get("from_url"))
        if from_url and not self._urls_match(current_url, urljoin(current_url, from_url)):
            return False

        if self._urls_match(current_url, resolved_url):
            return True

        transition = str(action.get("transition") or "push_state")
        await page.evaluate(
            """({ url, transition }) => {
                const next = new URL(url, window.location.href);

                if (transition === "replace_state") {
                    window.history.replaceState(window.history.state, "", next.href);
                    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
                    return window.location.href;
                }

                if (transition === "hash_change") {
                    if (
                        next.pathname !== window.location.pathname
                        || next.search !== window.location.search
                    ) {
                        window.history.pushState(window.history.state, "", next.href);
                    } else {
                        window.location.hash = next.hash;
                    }
                    return window.location.href;
                }

                if (transition === "popstate") {
                    window.history.replaceState(window.history.state, "", next.href);
                    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
                    return window.location.href;
                }

                window.history.pushState(window.history.state, "", next.href);
                window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
                return window.location.href;
            }""",
            {"url": resolved_url, "transition": transition},
        )
        return True

    async def upload_files(self, target: StepTarget | None, action: dict) -> None:
        file_paths = action.get("file_paths") or action.get("paths") or action.get("path")
        if isinstance(file_paths, str):
            file_paths = [file_paths]

        normalized_paths = [str(Path(path).expanduser()) for path in file_paths or [] if path]
        if not normalized_paths:
            raise RuntimeError("No file paths specified for upload_file")

        criteria = self._criteria_from_target(target)
        _, locator = await self._resolve_locator(criteria)
        if locator is None:
            raise RuntimeError(f"Web target not found for upload_file: {target}")

        upload_payload: str | list[str] = normalized_paths[0] if len(normalized_paths) == 1 else normalized_paths
        await locator.set_input_files(upload_payload)

    async def download_target(self, target: StepTarget | None, action: dict) -> None:
        criteria = self._criteria_from_target(target)
        page, locator = await self._resolve_locator(criteria)
        if locator is None:
            raise RuntimeError(f"Web target not found for download_file: {target}")

        async with page.expect_download(timeout=int(action.get("timeout_ms", self._timeout_ms))) as download_info:
            await locator.click(
                button=action.get("button", "left"),
                click_count=int(action.get("clicks", 1)),
                force=bool(action.get("force", False)),
            )

        download = await download_info.value
        save_as = action.get("save_as") or action.get("path")
        download_dir = action.get("download_dir")

        if save_as:
            save_path = Path(str(save_as)).expanduser()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            await download.save_as(str(save_path))
        elif download_dir:
            target_dir = Path(str(download_dir)).expanduser()
            target_dir.mkdir(parents=True, exist_ok=True)
            await download.save_as(str(target_dir / download.suggested_filename))

    async def click_target(self, target: StepTarget | None, action: dict) -> None:
        criteria = self._criteria_from_target(target)
        _, locator = await self._resolve_locator(criteria)
        if locator is None:
            raise RuntimeError(f"Web target not found for click: {target}")

        await locator.click(
            button=action.get("button", "left"),
            click_count=int(action.get("clicks", 1)),
            force=bool(action.get("force", False)),
        )

    async def type_text_target(self, target: StepTarget | None, action: dict) -> None:
        text = str(action.get("text", ""))
        if not text:
            return

        criteria = self._criteria_from_target(target)
        page, locator = await self._resolve_locator(criteria)
        delay = int(float(action.get("interval", 0.02)) * 1000)

        if locator is None:
            await page.keyboard.type(text, delay=delay)
            return

        try:
            if action.get("append", False):
                await locator.type(text, delay=delay)
            else:
                await locator.fill(text)
        except Exception:
            await locator.click()
            await page.keyboard.type(text, delay=delay)

    async def press_hotkey(self, action: dict) -> None:
        page = await self._ensure_page(action.get("url"))
        keys = list(action.get("keys", []))
        if not keys:
            modifiers = list(action.get("modifiers", []))
            key = action.get("key")
            keys = [*modifiers, key] if key else modifiers
        if not keys:
            return

        sequence = "+".join(self._normalize_key(key) for key in keys if key)
        await page.keyboard.press(sequence)

    async def scroll_target(self, target: StepTarget | None, action: dict) -> None:
        criteria = self._criteria_from_target(target)
        page, locator = await self._resolve_locator(criteria)
        if locator is not None:
            with contextlib.suppress(Exception):
                await locator.scroll_into_view_if_needed()
        delta = int(action.get("delta", 3))
        await page.mouse.wheel(0, -delta * 120)

    async def drag_target(self, target: StepTarget | None, action: dict) -> None:
        page = await self._ensure_page(target.url if target else action.get("url"))
        start_x = int(action.get("start_x", 0))
        start_y = int(action.get("start_y", 0))
        end_x = int(action.get("end_x", 0))
        end_y = int(action.get("end_y", 0))

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(end_x, end_y)
        await page.mouse.up()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._current_page = None

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = key.strip()
        return PLAYWRIGHT_KEY_MAP.get(normalized.lower(), normalized)
