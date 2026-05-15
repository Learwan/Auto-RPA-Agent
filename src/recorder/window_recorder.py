import asyncio
import inspect
import logging
import time
from uuid import uuid4

from src.models.desktop import UIElement, WindowInfo
from src.models.operation import (
    OperationContext,
    OperationEvent,
    OperationType,
    WindowEventData,
)
from src.platform.factory import create_platform_adapter
from src.recorder.base import BaseRecorder
from src.recorder.context_inference import enrich_operation_context

logger = logging.getLogger(__name__)

WINDOW_POLL_INTERVAL = 1.0
SAME_APP_TITLE_DEBOUNCE_MS = 2000


class WindowRecorder(BaseRecorder):
    def __init__(self, poll_interval: float = WINDOW_POLL_INTERVAL):
        super().__init__()
        self._task: asyncio.Task | None = None
        self._session_id = ""
        self._seq = 0
        self._adapter = None
        self._last_window: WindowInfo | None = None
        self._last_switch_time = 0.0
        self._poll_interval = poll_interval

    def start(self, session_id: str, callback) -> None:
        self._session_id = session_id
        self._callback = callback
        self._running = True
        self._seq = 0
        self._last_window = None
        self._last_switch_time = 0.0
        try:
            self._adapter = create_platform_adapter()
        except Exception as e:
            logger.warning(f"Failed to create platform adapter: {e}")
            self._adapter = None
        self._task = asyncio.ensure_future(self._poll_loop())

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._adapter = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._check_active_window()
            except Exception as e:
                logger.debug(f"Window poll error: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _check_active_window(self) -> None:
        if not self._adapter:
            return
        try:
            window = await self._adapter.get_active_window()
        except Exception:
            return
        if not window:
            return

        if self._is_same_window(window):
            return
        now = time.time()
        if self._is_transient_same_app_update(window, now):
            self._last_window = window
            return
        prev_window = self._last_window
        self._last_window = window
        self._last_switch_time = now
        self._seq += 1

        focused_element = None
        try:
            focused_candidate = self._adapter.get_focused_element()
            focused_candidate = await self._resolve_adapter_value(focused_candidate)
            if isinstance(focused_candidate, UIElement):
                focused_element = focused_candidate
            elif isinstance(focused_candidate, dict):
                focused_element = UIElement.model_validate(focused_candidate)
        except Exception:
            focused_element = None

        platform_name = None
        try:
            platform_candidate = self._adapter.get_platform_name()
            platform_candidate = await self._resolve_adapter_value(platform_candidate)
            if isinstance(platform_candidate, str) and platform_candidate.strip():
                platform_name = platform_candidate
        except Exception:
            platform_name = None

        context = enrich_operation_context(
            OperationContext(
                active_window=window,
                focused_element=focused_element,
                process_name=window.app_name,
                process_pid=window.pid,
                platform=platform_name,
            )
        )
        self._emit(
            WindowEventData(
                from_window=prev_window,
                to_window=window,
                method="focus",
            ),
            OperationType.WINDOW_SWITCH,
            context,
        )

    def _is_same_window(self, window: WindowInfo) -> bool:
        if not self._last_window:
            return False
        if self._last_window.app_name != window.app_name:
            return False
        return self._last_window.title == window.title and (self._last_window.url or "") == (window.url or "")

    def _is_transient_same_app_update(self, window: WindowInfo, now: float) -> bool:
        if not self._last_window:
            return False
        if self._last_window.app_name != window.app_name:
            return False
        if (now - self._last_switch_time) >= SAME_APP_TITLE_DEBOUNCE_MS / 1000:
            return False

        previous_title = (self._last_window.title or "").strip()
        current_title = (window.title or "").strip()
        previous_url = (self._last_window.url or "").strip()
        current_url = (window.url or "").strip()

        return not previous_url and not current_url and (not previous_title or not current_title)

    async def _resolve_adapter_value(self, value):
        if inspect.isawaitable(value):
            return await value
        return value

    def _emit(self, data: WindowEventData, op_type: OperationType, context: OperationContext) -> None:
        if not self._running or not self._callback:
            return
        event = OperationEvent(
            id=uuid4().hex,
            session_id=self._session_id,
            seq_num=self._seq,
            timestamp=int(time.time() * 1000),
            type=op_type,
            data=data,
            context=context,
        )
        self._callback(event)
