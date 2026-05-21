import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.collector.visual_targeting import build_click_node_suggestion
from src.config import settings
from src.db.database import get_session_factory
from src.db.repository import Repository
from src.models.desktop import UIElement, WindowInfo
from src.models.hotkey import HotkeyAction
from src.models.operation import KeyAction, KeyboardEventData, MouseEventData, OperationEvent, OperationType
from src.models.session import Session, SessionStatus
from src.recorder.base import BaseRecorder
from src.recorder.context_inference import normalize_recording_mode
from src.recorder.event_bus import EventBus
from src.recorder.web_recorder import WebRecorder
from src.settings_store import SettingsStore

logger = logging.getLogger(__name__)

MAX_OPERATIONS_PER_SESSION = 5000
FOCUS_CAPTURE_REUSE_WINDOW_MS = 3000


class SessionManager:
    def __init__(self, recorders_factory: Callable[[], list[BaseRecorder]] | None = None):
        self._sessions: dict[str, Session] = {}
        self._recorders: dict[str, list[BaseRecorder]] = {}
        self._event_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._operation_counts: dict[str, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recorders_factory = recorders_factory
        self._hotkey_manager: Any | None = None
        self._hud_manager: Any | None = None
        self._audio: Any | None = None
        self._notifier: Any | None = None
        self._focus_context_service: Any | None = None
        self._snapshot_manager: Any | None = None
        self._settings_store = SettingsStore.get_instance()
        self._hotkeys_started = False
        self._last_hotkey_session_id: str | None = None
        self._session_options: dict[str, dict[str, Any]] = {}
        self._last_auto_focus_capture_at: dict[str, float] = {}
        self._auto_focus_capture_inflight: set[str] = set()
        self._latest_focus_capture: dict[str, dict[str, Any]] = {}

    async def create_session(self, name: str, description: str = "", tags: list[str] | None = None) -> Session:
        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            session = await repo.create_session(name, description, tags)
        self._sessions[session.id] = session
        self._operation_counts[session.id] = 0
        self._event_subscribers[session.id] = []
        return session

    async def start_session(
        self,
        session_id: str,
        mode: str = "desktop",
        options: dict | None = None,
    ) -> Session:
        session = self._sessions.get(session_id)
        if not session:
            session = await self._get_session_from_db(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status == SessionStatus.RECORDING:
            return session

        self._loop = asyncio.get_running_loop()
        normalized_mode = normalize_recording_mode(mode)
        normalized_options = dict(options or {})
        normalized_options["recording_mode"] = normalized_mode
        self._session_options[session_id] = normalized_options
        recorders = self._create_recorders(mode=normalized_mode, options=normalized_options)
        self._recorders[session_id] = recorders
        uses_desktop_helpers = self._uses_desktop_helpers(session_id)
        if uses_desktop_helpers:
            self._ensure_desktop_helpers()

        session.status = SessionStatus.RECORDING
        session.started_at = time.time()
        self._sessions[session_id] = session

        if uses_desktop_helpers:
            self._start_periodic_capture_if_needed(session_id)
            # On macOS, forking the HUD subprocess after pynput event-tap threads are
            # live can trigger an Objective-C abort in the server process.
            self._start_hud_if_needed(session, normalized_options)
            self._start_hotkeys_if_needed(session_id)
            if self._audio is not None:
                self._audio.play_start()
            if self._notifier is not None:
                self._notifier.notify_recording_started(session.name)

        for recorder in recorders:
            recorder.start(session_id, self._on_operation)

        recorder_health = self._check_recorder_health(recorders)
        if recorder_health["active_count"] == 0 and recorder_health["total_count"] > 0:
            logger.warning(
                "Recording session %s started but NO recorders are active. "
                "No operations will be captured. Check display server, "
                "system tools (wmctrl/xdotool/xclip), and environment variables.",
                session_id,
            )
        elif recorder_health["inactive"]:
            logger.info(
                "Recording session %s: %d/%d recorders active. Inactive: %s",
                session_id,
                recorder_health["active_count"],
                recorder_health["total_count"],
                ", ".join(recorder_health["inactive"]),
            )

        session.metadata = session.metadata or {}
        session.metadata["recorder_health"] = recorder_health

        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            await repo.update_session_status(
                session_id,
                SessionStatus.RECORDING.value,
                started_at=datetime.fromtimestamp(session.started_at, tz=UTC),
            )
        return session

    async def pause_session(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if not session:
            session = await self._get_session_from_db(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status != SessionStatus.RECORDING:
            return session
        uses_desktop_helpers = self._uses_desktop_helpers(session_id)
        hud_enabled = self._is_hud_enabled(session_id)
        if uses_desktop_helpers:
            self._ensure_desktop_helpers()
            self._stop_periodic_capture_if_needed(session_id)

        for recorder in self._recorders.get(session_id, []):
            if hasattr(recorder, "pause") and callable(recorder.pause):
                recorder.pause()

        session.status = SessionStatus.PAUSED
        self._sessions[session_id] = session
        if uses_desktop_helpers:
            if hud_enabled and self._hud_manager is not None:
                self._hud_manager.update_session_status(session_id, SessionStatus.PAUSED.value, session.operation_count)
            if self._audio is not None:
                self._audio.play_pause()
            if self._notifier is not None:
                self._notifier.notify_recording_paused(session.name)

        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            await repo.update_session_status(session_id, SessionStatus.PAUSED.value)
        return session

    async def resume_session(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if not session:
            session = await self._get_session_from_db(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status != SessionStatus.PAUSED:
            return session
        uses_desktop_helpers = self._uses_desktop_helpers(session_id)
        hud_enabled = self._is_hud_enabled(session_id)
        if uses_desktop_helpers:
            self._ensure_desktop_helpers()
            self._start_periodic_capture_if_needed(session_id)

        for recorder in self._recorders.get(session_id, []):
            if hasattr(recorder, "resume") and callable(recorder.resume):
                recorder.resume()

        session.status = SessionStatus.RECORDING
        self._sessions[session_id] = session
        if uses_desktop_helpers:
            if hud_enabled and self._hud_manager is not None:
                self._hud_manager.update_session_status(
                    session_id,
                    SessionStatus.RECORDING.value,
                    session.operation_count,
                )
            if self._audio is not None:
                self._audio.play_resume()
            if self._notifier is not None:
                self._notifier.notify_recording_resumed(session.name)

        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            await repo.update_session_status(session_id, SessionStatus.RECORDING.value)
        return session

    async def stop_session(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if not session:
            session = await self._get_session_from_db(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status == SessionStatus.STOPPED:
            return session
        uses_desktop_helpers = self._uses_desktop_helpers(session_id)
        hud_enabled = self._is_hud_enabled(session_id)
        if uses_desktop_helpers:
            self._ensure_desktop_helpers()
            self._stop_periodic_capture_if_needed(session_id)

        for recorder in self._recorders.get(session_id, []):
            recorder.stop()
        self._recorders.pop(session_id, None)

        now = time.time()
        session.status = SessionStatus.STOPPED
        session.stopped_at = now
        if session.started_at:
            session.duration_ms = int((now - session.started_at) * 1000)
        session.operation_count = self._operation_counts.get(session_id, 0)

        self._sessions[session_id] = session
        if uses_desktop_helpers:
            if hud_enabled and self._hud_manager is not None:
                self._hud_manager.stop_session(session_id, session.operation_count)
            if self._audio is not None:
                self._audio.play_stop()
            duration_text = f"{session.duration_ms // 60000}分{(session.duration_ms // 1000) % 60}秒"
            if self._notifier is not None:
                self._notifier.notify_recording_stopped(session.name, duration_text)

        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            await repo.update_session_status(
                session_id,
                SessionStatus.STOPPED.value,
                stopped_at=datetime.fromtimestamp(now, tz=UTC),
                duration_ms=session.duration_ms,
                operation_count=session.operation_count,
            )
        self._session_options.pop(session_id, None)
        self._latest_focus_capture.pop(session_id, None)
        return session

    async def get_session(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session:
            return session
        return await self._get_session_from_db(session_id)

    async def list_sessions(self, limit: int = 50, offset: int = 0) -> list[Session]:
        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            return await repo.list_sessions(limit, offset)

    async def delete_session(self, session_id: str) -> None:
        if session_id in self._recorders:
            for recorder in self._recorders[session_id]:
                recorder.stop()
            self._recorders.pop(session_id, None)
        self._sessions.pop(session_id, None)
        self._operation_counts.pop(session_id, None)
        self._event_subscribers.pop(session_id, None)
        self._session_options.pop(session_id, None)
        self._stop_periodic_capture_if_needed(session_id)
        self._last_auto_focus_capture_at.pop(session_id, None)
        self._auto_focus_capture_inflight.discard(session_id)
        self._latest_focus_capture.pop(session_id, None)

        if self._hud_manager is not None:
            self._hud_manager.stop_session(session_id)

        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            await repo.delete_session(session_id)

    async def toggle_hud_interaction(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            session = await self._get_session_from_db(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if not self._is_hud_enabled(session_id) or self._hud_manager is None:
            return False
        self._hud_manager.toggle_interaction(session_id=session_id)
        return True

    async def capture_focus_context(
        self,
        session_id: str,
        *,
        include_vision: bool = True,
        include_ocr: bool = True,
        include_llm: bool = True,
        present_interactively: bool = True,
    ) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session:
            session = await self._get_session_from_db(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        self._ensure_desktop_helpers()
        if self._focus_context_service is None:
            raise ValueError("Focus context service unavailable")

        capture = await self._focus_context_service.capture(
            session_id=session_id,
            include_vision=include_vision,
            include_ocr=include_ocr,
            include_llm=include_llm,
        )
        self._latest_focus_capture[session_id] = capture
        if self._is_hud_enabled(session_id) and self._hud_manager is not None:
            self._hud_manager.present_focus_context(capture, enter_interaction=present_interactively)
        try:
            bus = EventBus.get_instance()
            bus.publish_sync(
                "focus_context_capture",
                {
                    "type": "focus_context_capture",
                    "session_id": session_id,
                    "capture": capture,
                },
            )
        except Exception as exc:
            logger.debug("Failed to publish focus context capture: %s", exc)
        return capture

    def subscribe_events(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        if session_id not in self._event_subscribers:
            self._event_subscribers[session_id] = []
        self._event_subscribers[session_id].append(queue)
        return queue

    def unsubscribe_events(self, session_id: str, queue: asyncio.Queue) -> None:
        if session_id in self._event_subscribers:
            with contextlib.suppress(ValueError):
                self._event_subscribers[session_id].remove(queue)

    def _create_recorders(self, mode: str = "desktop", options: dict | None = None) -> list[BaseRecorder]:
        if self._recorders_factory is not None:
            return self._recorders_factory()

        normalized_mode = normalize_recording_mode(mode)

        if normalized_mode == "web":
            options = options or {}
            return [
                WebRecorder(
                    start_url=options.get("url"),
                    browser_name=options.get("browser"),
                    headless=bool(options.get("headless", False)),
                )
            ]

        if normalized_mode not in {"desktop", "auto"}:
            raise ValueError(f"Unsupported recording mode: {mode}")

        recorders: list[BaseRecorder] = []
        if settings.RECORD_ENABLE_MOUSE:
            try:
                from src.recorder.mouse_recorder import MouseRecorder

                recorders.append(MouseRecorder())
            except ImportError as e:
                logger.warning(
                    "MouseRecorder unavailable: %s. "
                    "On Linux, ensure a display server (X11/Wayland) is running and DISPLAY is set. "
                    "Set RECORD_ENABLE_MOUSE=false to suppress this warning.",
                    e,
                )
        if settings.RECORD_ENABLE_KEYBOARD:
            try:
                from src.recorder.keyboard_recorder import KeyboardRecorder

                recorders.append(KeyboardRecorder())
            except ImportError as e:
                logger.warning(
                    "KeyboardRecorder unavailable: %s. "
                    "On Linux, ensure a display server (X11/Wayland) is running and DISPLAY is set. "
                    "Set RECORD_ENABLE_KEYBOARD=false to suppress this warning.",
                    e,
                )
        if settings.RECORD_ENABLE_WINDOW:
            try:
                from src.recorder.window_recorder import WindowRecorder

                window_interval = max(settings.RECORD_WINDOW_CHECK_INTERVAL_MS, 10) / 1000
                recorders.append(WindowRecorder(poll_interval=window_interval))
            except ImportError as e:
                logger.warning("WindowRecorder unavailable: %s", e)
        if settings.RECORD_ENABLE_CLIPBOARD:
            try:
                from src.recorder.clipboard_recorder import ClipboardRecorder

                clipboard_interval = max(settings.RECORD_CLIPBOARD_INTERVAL_MS, 10) / 1000
                recorders.append(ClipboardRecorder(poll_interval=clipboard_interval))
            except ImportError as e:
                logger.warning("ClipboardRecorder unavailable: %s", e)
        if settings.RECORD_ENABLE_FILESYSTEM:
            try:
                from src.recorder.filesystem_recorder import FilesystemRecorder

                watch_paths = settings.filesystem_watch_paths or None
                recorders.append(FilesystemRecorder(watch_paths=watch_paths))
            except ImportError as e:
                logger.warning("FilesystemRecorder unavailable: %s", e)
        return recorders

    @staticmethod
    def _check_recorder_health(recorders: list) -> dict[str, Any]:
        active = []
        inactive = []
        for recorder in recorders:
            name = type(recorder).__name__
            healthy = getattr(recorder, "_listener_healthy", None)
            if healthy is not None:
                if healthy:
                    active.append(name)
                else:
                    inactive.append(name)
            elif getattr(recorder, "is_running", False):
                active.append(name)
            else:
                inactive.append(name)
        return {
            "total_count": len(recorders),
            "active_count": len(active),
            "active": active,
            "inactive": inactive,
        }

    def _on_operation(self, event: OperationEvent) -> None:
        session_id = event.session_id
        count = self._operation_counts.get(session_id, 0) + 1
        self._operation_counts[session_id] = count
        event.seq_num = count

        if session_id in self._sessions:
            self._sessions[session_id].operation_count = count

        if count <= MAX_OPERATIONS_PER_SESSION:
            self._schedule_coro(self._persist_event(event))
            self._notify_subscribers(session_id, event)
            self._publish_to_event_bus(session_id, event)
            self._maybe_schedule_auto_focus_context(event)
            if self._matches_hud_toggle_hotkey(event):
                self._hud_manager.toggle_interaction(session_id=session_id)
            if self._is_hud_enabled(session_id) and self._hud_manager is not None:
                self._hud_manager.handle_operation(event, count)

        if count == MAX_OPERATIONS_PER_SESSION:
            logger.warning(f"Session {session_id} reached max operations ({MAX_OPERATIONS_PER_SESSION}), auto-stopping")
            self._schedule_coro(self._auto_stop_session(session_id))

    def _schedule_coro(self, coro) -> None:
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            future.add_done_callback(lambda f: f.exception() if not f.cancelled() and f.exception() else None)
        else:
            try:
                asyncio.get_running_loop()
                task = asyncio.ensure_future(coro)
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.exception() else None)
            except RuntimeError:
                logger.warning("No event loop available to schedule coroutine")

    def _maybe_schedule_auto_focus_context(self, event: OperationEvent) -> None:
        session_id = event.session_id
        if not self._should_capture_auto_focus_context(event):
            return

        self._last_auto_focus_capture_at[session_id] = time.monotonic()
        self._auto_focus_capture_inflight.add(session_id)
        self._schedule_coro(self._capture_focus_context_background(session_id))

    def _should_capture_auto_focus_context(self, event: OperationEvent) -> bool:
        session_id = event.session_id
        if not settings.RECORD_AUTO_FOCUS_CONTEXT_ENABLED:
            return False
        if session_id in self._auto_focus_capture_inflight:
            return False

        session = self._sessions.get(session_id)
        if session is None or session.status != SessionStatus.RECORDING:
            return False
        if not self._uses_desktop_helpers(session_id):
            return False

        # Lightweight focus refresh should stay responsive even when screenshots run slower.
        minimum_interval_ms = settings.RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS
        if minimum_interval_ms > 0:
            last_capture = self._last_auto_focus_capture_at.get(session_id, 0.0)
            if (time.monotonic() - last_capture) < (minimum_interval_ms / 1000):
                return False

        if event.type == OperationType.WINDOW_SWITCH:
            return True
        if event.type not in {OperationType.MOUSE_CLICK, OperationType.KEY_INPUT}:
            return False
        if (
            event.type == OperationType.KEY_INPUT
            and isinstance(event.data, KeyboardEventData)
            and event.data.is_sensitive
        ):
            return False
        return True

    async def _capture_focus_context_background(self, session_id: str) -> None:
        try:
            await self.capture_focus_context(
                session_id,
                include_vision=False,
                include_ocr=False,
                include_llm=False,
                present_interactively=False,
            )
        except Exception as exc:
            logger.debug("Auto focus context capture failed for %s: %s", session_id, exc)
        finally:
            self._auto_focus_capture_inflight.discard(session_id)

    async def _auto_stop_session(self, session_id: str) -> None:
        try:
            await self.stop_session(session_id)
            logger.info(f"Session {session_id} auto-stopped due to operation limit")
        except Exception as e:
            logger.error(f"Failed to auto-stop session {session_id}: {e}")

    async def _persist_event(self, event: OperationEvent) -> None:
        try:
            event = await self._enrich_operation_event_for_recording(event)
            factory = get_session_factory()
            async with factory() as db_session:
                repo = Repository(db_session)
                await repo.add_operation(event)
        except Exception as e:
            logger.error(f"Failed to persist operation event: {e}")

    async def _enrich_operation_event_for_recording(self, event: OperationEvent) -> OperationEvent:
        if not self._should_enrich_operation_event(event):
            return event

        include_vision = self._event_needs_vision_enrichment(event)
        capture = self._get_recent_focus_capture(event.session_id, event.timestamp)
        if capture is not None and not self._capture_satisfies_enrichment(capture, include_vision, event):
            capture = None
        if capture is None:
            try:
                capture = await self.capture_focus_context(
                    event.session_id,
                    include_vision=include_vision,
                    include_ocr=include_vision,
                    include_llm=False,
                )
            except Exception as exc:
                logger.debug("Failed to enrich operation %s with focus capture: %s", event.id, exc)
                return event

        return self._merge_focus_capture_into_event(event, capture)

    def _should_enrich_operation_event(self, event: OperationEvent) -> bool:
        if not self._uses_desktop_helpers(event.session_id):
            return False
        if event.type not in {OperationType.MOUSE_CLICK, OperationType.KEY_INPUT, OperationType.WINDOW_SWITCH}:
            return False
        if (
            event.type == OperationType.KEY_INPUT
            and isinstance(event.data, KeyboardEventData)
            and event.data.is_sensitive
        ):
            return False
        return True

    def _context_needs_recording_enrichment(self, context) -> bool:
        if context is None:
            return True
        if context.active_window is None:
            return True
        return self._focused_element_needs_enrichment(context.focused_element, context)

    @staticmethod
    def _focused_element_needs_enrichment(
        element: UIElement | None,
        context: Any | None = None,
    ) -> bool:
        if element is None:
            return True

        has_identity = bool(
            element.identifier
            or element.selector
            or element.xpath
            or element.title
            or element.functional_label
            or element.description
        )
        if not has_identity:
            return True

        is_web_context = bool(
            getattr(context, "platform", None) == "web"
            or getattr(getattr(context, "active_window", None), "browser_type", None)
            or getattr(getattr(context, "active_window", None), "url", None)
            or element.url
            or element.frame
        )
        if is_web_context and not (element.selector or element.xpath or element.identifier):
            return True
        return False

    def _event_needs_vision_enrichment(self, event: OperationEvent) -> bool:
        context = event.context
        if context is None or context.focused_element is None:
            return True
        return self._focused_element_needs_enrichment(context.focused_element, context)

    def _get_recent_focus_capture(self, session_id: str, event_timestamp: int) -> dict[str, Any] | None:
        capture = self._latest_focus_capture.get(session_id)
        if not capture:
            return None

        capture_timestamp = capture.get("timestamp")
        if capture_timestamp in (None, ""):
            return capture
        try:
            if abs(int(event_timestamp) - int(capture_timestamp)) <= FOCUS_CAPTURE_REUSE_WINDOW_MS:
                return capture
        except Exception:
            return capture
        return None

    def _capture_satisfies_enrichment(
        self,
        capture: dict[str, Any],
        include_vision: bool,
        event: OperationEvent | None = None,
    ) -> bool:
        if not include_vision:
            return True
        node_suggestion = self._build_node_suggestion_payload(capture, event)
        element = self._build_focus_element_from_capture(capture, node_suggestion=node_suggestion)
        if element is None:
            return False
        return not self._focused_element_needs_enrichment(element)

    def _merge_focus_capture_into_event(self, event: OperationEvent, capture: dict[str, Any]) -> OperationEvent:
        context = event.context
        if context is None:
            from src.models.operation import OperationContext

            context = OperationContext()

        state = capture.get("state") or {}
        capture_window = self._parse_window_info(state.get("active_window"))
        node_suggestion = self._build_node_suggestion_payload(capture, event)
        capture_element = self._build_focus_element_from_capture(capture, node_suggestion=node_suggestion)

        merged_window = self._merge_window_info(context.active_window, capture_window)
        merged_element = self._merge_ui_element(context.focused_element, capture_element)

        updates: dict[str, Any] = {
            "active_window": merged_window,
            "focused_element": merged_element,
        }
        if not context.process_name and merged_window is not None and merged_window.app_name:
            updates["process_name"] = merged_window.app_name
        if not context.process_pid and merged_window is not None and merged_window.pid:
            updates["process_pid"] = merged_window.pid
        if not context.platform and capture.get("state", {}).get("platform"):
            updates["platform"] = capture["state"]["platform"]
        if not context.screenshot_path and capture.get("snapshot_id"):
            updates["screenshot_path"] = f"snapshot://{capture['snapshot_id']}"
        if node_suggestion is not None:
            updates["node_suggestion"] = node_suggestion

        merged_context = context.model_copy(update=updates)
        return event.model_copy(update={"context": merged_context})

    @staticmethod
    def _parse_window_info(payload: Any) -> WindowInfo | None:
        if not isinstance(payload, dict):
            return None
        try:
            return WindowInfo.model_validate(payload)
        except Exception:
            return None

    @staticmethod
    def _build_focus_element_from_capture(
        capture: dict[str, Any],
        node_suggestion: dict[str, Any] | None = None,
    ) -> UIElement | None:
        state = capture.get("state") or {}
        details = capture.get("focused_details") or {}
        target = ((node_suggestion or capture.get("node_suggestion") or {}).get("target") or {})
        vision_element = target.get("vision_element") or {}
        payload = state.get("focused_element")
        base_element = None
        if isinstance(payload, dict):
            with contextlib.suppress(Exception):
                base_element = UIElement.model_validate(payload)

        vision_label = str(vision_element.get("label") or "").strip() or None
        role = (
            target.get("role")
            or details.get("role")
            or vision_element.get("element_type")
            or (base_element.role if base_element else None)
            or "generic"
        )
        title = (
            target.get("title")
            or target.get("text_contains")
            or details.get("title")
            or details.get("label")
            or (base_element.title if base_element else None)
            or vision_label
        )
        value = (
            target.get("text_preview")
            or details.get("value")
            or (base_element.value if base_element else None)
            or vision_label
        )
        identifier = target.get("accessibility_id") or details.get("identifier")
        selector = target.get("selector")
        xpath = target.get("xpath")
        class_name = target.get("class_name") or details.get("class_name")
        description = target.get("description") or details.get("description")
        functional_label = details.get("functional_label") or details.get("label") or vision_label
        bounds = target.get("bounds")
        url = target.get("url")
        frame = target.get("frame")

        if not any([identifier, selector, xpath, title, value, functional_label, description]):
            return base_element

        with contextlib.suppress(Exception):
            enriched_element = UIElement.model_validate(
                {
                    "role": role,
                    "title": title,
                    "value": value,
                    "identifier": identifier,
                    "description": description,
                    "functional_label": functional_label,
                    "class_name": class_name,
                    "selector": selector,
                    "xpath": xpath,
                    "url": url,
                    "frame": frame,
                    "bounds": bounds,
                    "is_enabled": details.get("is_enabled", True),
                    "is_focused": details.get("is_focused", True),
                }
            )
            return SessionManager._merge_ui_element(base_element, enriched_element)
        return base_element

    def _build_node_suggestion_payload(
        self,
        capture: dict[str, Any],
        event: OperationEvent | None = None,
    ) -> dict[str, Any] | None:
        node_suggestion = capture.get("node_suggestion")
        if (not isinstance(node_suggestion, dict) or not node_suggestion) and event is not None:
            node_suggestion = self._build_click_node_suggestion_from_vision(capture, event)
        if not isinstance(node_suggestion, dict) or not node_suggestion:
            return None

        payload = dict(node_suggestion)
        snapshot_id = capture.get("snapshot_id")
        if snapshot_id not in (None, ""):
            payload["snapshot_id"] = snapshot_id
        return payload

    def _build_click_node_suggestion_from_vision(
        self,
        capture: dict[str, Any],
        event: OperationEvent,
    ) -> dict[str, Any] | None:
        if event.type != OperationType.MOUSE_CLICK or not isinstance(event.data, MouseEventData):
            return None

        return build_click_node_suggestion(
            click_x=event.data.x,
            click_y=event.data.y,
            active_window=(capture.get("state") or {}).get("active_window"),
            vision_payload=capture.get("vision") or {},
            ocr_payload=capture.get("ocr") or {},
            snapshot_id=capture.get("snapshot_id"),
        )

    @staticmethod
    def _merge_window_info(current: WindowInfo | None, enriched: WindowInfo | None) -> WindowInfo | None:
        if enriched is None:
            return current
        if current is None:
            return enriched

        updates: dict[str, Any] = {}
        for field in ("title", "app_name", "pid", "url", "browser_type", "tab_id", "bounds"):
            current_value = getattr(current, field, None)
            enriched_value = getattr(enriched, field, None)
            if current_value in (None, "", 0) and enriched_value not in (None, "", 0):
                updates[field] = enriched_value
        return current.model_copy(update=updates) if updates else current

    @staticmethod
    def _merge_ui_element(current: UIElement | None, enriched: UIElement | None) -> UIElement | None:
        if enriched is None:
            return current
        if current is None:
            return enriched

        updates: dict[str, Any] = {}
        for field in (
            "title",
            "value",
            "identifier",
            "description",
            "functional_label",
            "class_name",
            "selector",
            "xpath",
            "url",
            "frame",
            "bounds",
            "input_type",
            "tag_name",
        ):
            current_value = getattr(current, field, None)
            enriched_value = getattr(enriched, field, None)
            if current_value in (None, "", 0) and enriched_value not in (None, "", 0):
                updates[field] = enriched_value

        if not current.is_focused and enriched.is_focused:
            updates["is_focused"] = True
        if not current.is_enabled and enriched.is_enabled:
            updates["is_enabled"] = True

        return current.model_copy(update=updates) if updates else current

    def _notify_subscribers(self, session_id: str, event: OperationEvent) -> None:
        for queue in self._event_subscribers.get(session_id, []):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def _publish_to_event_bus(self, session_id: str, event: OperationEvent) -> None:
        try:
            bus = EventBus.get_instance()
            bus.publish_sync(
                "operation_event",
                {
                    "type": "operation_event",
                    "session_id": session_id,
                    "event": event.model_dump(mode="json"),
                },
            )
        except Exception as e:
            logger.debug(f"Failed to publish event to event bus: {e}")

    def _matches_hud_toggle_hotkey(self, event: OperationEvent) -> bool:
        if self._hud_manager is None or not self._is_hud_enabled(event.session_id):
            return False
        if event.type != OperationType.KEY_PRESS or not isinstance(event.data, KeyboardEventData):
            return False
        if event.data.action != KeyAction.HOTKEY:
            return False

        settings_obj = self._settings_store.get_settings()
        binding = settings_obj.hotkeys.find_by_action(HotkeyAction.TOGGLE_HUD_INTERACTION.value)
        if binding is None or not binding.enabled:
            return False

        normalized_modifiers = {self._normalize_hotkey_modifier(item) for item in event.data.modifiers}
        normalized_modifiers.discard("")
        expected_modifiers = set(binding.modifiers)
        if event.data.key.lower() != binding.key.lower():
            return False

        if normalized_modifiers == expected_modifiers:
            return True

        # On macOS, injected/system-level shortcuts may surface cmd where users
        # configured ctrl (and vice versa). Accept that equivalent set for HUD toggle.
        if (event.context and event.context.platform == "macos"):
            swapped = {
                "ctrl" if item == "cmd" else "cmd" if item == "ctrl" else item
                for item in normalized_modifiers
            }
            return swapped == expected_modifiers
        return False

    def _normalize_hotkey_modifier(self, value: str) -> str:
        lowered = value.lower().strip()
        modifier_map = {
            "key.ctrl": "ctrl",
            "key.ctrl_l": "ctrl",
            "key.ctrl_r": "ctrl",
            "key.shift": "shift",
            "key.shift_l": "shift",
            "key.shift_r": "shift",
            "key.alt": "alt",
            "key.alt_l": "alt",
            "key.alt_r": "alt",
            "key.cmd": "cmd",
            "key.cmd_l": "cmd",
            "key.cmd_r": "cmd",
            "ctrl": "ctrl",
            "shift": "shift",
            "alt": "alt",
            "cmd": "cmd",
            "super": "cmd",
        }
        return modifier_map.get(lowered, "")

    def _uses_desktop_helpers(self, session_id: str) -> bool:
        return any(not isinstance(recorder, WebRecorder) for recorder in self._recorders.get(session_id, []))

    def _ensure_desktop_helpers(self) -> None:
        if self._audio is None:
            from src.recorder.audio_feedback import AudioFeedback

            self._audio = AudioFeedback.get_instance()

        if self._notifier is None:
            from src.recorder.notification import SystemNotifier

            self._notifier = SystemNotifier.get_instance()

        if settings.RECORD_HOTKEY_ENABLED and self._hotkey_manager is None:
            try:
                from src.recorder.hotkey_manager import HotkeyManager

                self._hotkey_manager = HotkeyManager.get_instance()
            except Exception as exc:
                logger.warning("Hotkey manager unavailable, continuing without hotkeys: %s", exc)

        if self._hud_manager is None:
            try:
                from src.recorder.hud_manager import RecordingHUDManager

                self._hud_manager = RecordingHUDManager()
            except Exception as exc:
                logger.warning("HUD manager unavailable, continuing without HUD: %s", exc)

        if self._focus_context_service is None:
            try:
                from src.collector.focus_context import FocusContextService

                self._focus_context_service = FocusContextService()
            except Exception as exc:
                logger.warning("Focus context service unavailable, continuing without capture: %s", exc)

        if self._snapshot_manager is None:
            try:
                from src.collector.snapshot import get_snapshot_manager

                self._snapshot_manager = get_snapshot_manager()
            except Exception as exc:
                logger.warning("Snapshot manager unavailable, continuing without periodic capture: %s", exc)

    def _start_periodic_capture_if_needed(self, session_id: str) -> None:
        if not settings.RECORD_SCREENSHOT_ENABLED:
            return
        interval_seconds = self._capture_interval_seconds()
        if interval_seconds <= 0:
            return
        if self._snapshot_manager is None:
            return
        try:
            self._snapshot_manager.start_periodic_capture(interval_seconds, session_id=session_id)
        except Exception as exc:
            logger.warning("Failed to start periodic capture for %s: %s", session_id, exc)

    def _stop_periodic_capture_if_needed(self, session_id: str) -> None:
        if self._snapshot_manager is None:
            return
        try:
            self._snapshot_manager.stop_periodic_capture(session_id=session_id)
        except Exception as exc:
            logger.warning("Failed to stop periodic capture for %s: %s", session_id, exc)

    def _capture_interval_seconds(self) -> float:
        interval_ms = settings.RECORD_CAPTURE_INTERVAL_MS
        if interval_ms <= 0:
            return 0.0
        return interval_ms / 1000

    async def _get_session_from_db(self, session_id: str) -> Session | None:
        factory = get_session_factory()
        try:
            async with factory() as db_session:
                repo = Repository(db_session)
                return await repo.get_session(session_id)
        except SQLAlchemyError as exc:
            logger.debug(f"Failed to load session {session_id} from database: {exc}")
            return None

    def _start_hotkeys_if_needed(self, session_id: str) -> None:
        user_settings = self._settings_store.get_settings()
        self._last_hotkey_session_id = session_id
        if not settings.RECORD_HOTKEY_ENABLED or self._hotkeys_started:
            return
        self._ensure_desktop_helpers()
        if self._hotkey_manager is None:
            return

        def _on_start():
            self._schedule_coro(self._handle_hotkey_start())

        def _on_pause_resume():
            self._schedule_coro(self._handle_hotkey_pause_resume())

        def _on_stop():
            self._schedule_coro(self._handle_hotkey_stop())

        def _on_toggle_hud():
            sid = self._last_hotkey_session_id
            if sid and self._hud_manager is not None:
                self._hud_manager.toggle_interaction(session_id=sid)

        def _on_capture_focus():
            self._schedule_coro(self._handle_hotkey_capture_focus())

        self._hotkey_manager.start(
            user_settings,
            on_start=_on_start,
            on_pause_resume=_on_pause_resume,
            on_stop=_on_stop,
            on_toggle_hud=_on_toggle_hud,
            on_capture_focus=_on_capture_focus,
        )
        self._hotkeys_started = True

    async def _handle_hotkey_start(self) -> None:
        sid = self._last_hotkey_session_id
        if not sid or sid not in self._sessions:
            return
        session = self._sessions[sid]
        if session.status in (SessionStatus.RECORDING, SessionStatus.PAUSED):
            return
        try:
            await self.resume_session(sid)
        except Exception as e:
            logger.error("Hotkey start failed: %s", e)

    async def _handle_hotkey_pause_resume(self) -> None:
        sid = self._last_hotkey_session_id
        if not sid or sid not in self._sessions:
            return
        session = self._sessions[sid]
        if session.status == SessionStatus.RECORDING:
            try:
                await self.pause_session(sid)
            except Exception as e:
                logger.error("Hotkey pause failed: %s", e)
        elif session.status == SessionStatus.PAUSED:
            try:
                await self.resume_session(sid)
            except Exception as e:
                logger.error("Hotkey resume failed: %s", e)

    async def _handle_hotkey_stop(self) -> None:
        sid = self._last_hotkey_session_id
        if not sid or sid not in self._sessions:
            return
        session = self._sessions[sid]
        if session.status == SessionStatus.STOPPED:
            return
        try:
            await self.stop_session(sid)
        except Exception as e:
            logger.error("Hotkey stop failed: %s", e)

    async def _handle_hotkey_capture_focus(self) -> None:
        sid = self._last_hotkey_session_id
        if not sid or sid not in self._sessions:
            return
        session = self._sessions[sid]
        if session.status == SessionStatus.STOPPED:
            return
        try:
            await self.capture_focus_context(sid)
        except Exception as e:
            logger.error("Hotkey capture focus failed: %s", e)

    def _is_hud_enabled(self, session_id: str) -> bool:
        if not self._uses_desktop_helpers(session_id):
            return False
        return bool(self._session_options.get(session_id, {}).get("hud", True))

    def _start_hud_if_needed(self, session: Session, options: dict[str, Any]) -> None:
        if not self._is_hud_enabled(session.id) or self._hud_manager is None:
            return
        self._hud_manager.set_loop(self._loop)
        set_handler = getattr(self._hud_manager, "set_focus_context_request_handler", None)
        if callable(set_handler):
            set_handler(lambda: self._schedule_coro(self.capture_focus_context(session.id)))
        self._hud_manager.start_session(
            session_id=session.id,
            session_name=session.name,
            hud_ai_assist=bool(options.get("hud_ai_assist", False)),
        )
        self._hud_manager.update_session_status(session.id, SessionStatus.RECORDING.value, session.operation_count)
