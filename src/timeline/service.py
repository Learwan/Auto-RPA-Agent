from __future__ import annotations

import re
import time
from pathlib import Path

from src.collector.snapshot import DesktopSnapshot, get_snapshot_manager
from src.db.database import get_session_factory
from src.db.repository import Repository
from src.models.operation import (
    ClipboardEventData,
    FileEventData,
    KeyboardEventData,
    MouseEventData,
    NavigationEventData,
    OperationEvent,
    OperationType,
    WindowEventData,
)
from src.models.session import Session


def _clip_text(value: str | None, limit: int = 96) -> str:
    if not value:
        return ""
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


class TimelineMemoryService:
    def __init__(self):
        self._snapshot_manager = get_snapshot_manager()

    async def query_timeline(
        self,
        query: str = "",
        session_id: str | None = None,
        limit: int = 12,
        lookback_minutes: int = 120,
        include_operations: bool = True,
        include_snapshots: bool = True,
    ) -> list[dict]:
        cutoff_ms = int(time.time() * 1000) - max(1, lookback_minutes) * 60_000
        session_lookup = await self._load_sessions(session_id)
        items: list[dict] = []

        if include_snapshots:
            snapshots = self._snapshot_manager.list_snapshots(session_id=session_id, limit=max(limit * 4, 24))
            for snapshot in snapshots:
                if snapshot.timestamp < cutoff_ms:
                    continue
                items.append(self._snapshot_to_item(snapshot))

        if include_operations:
            session_ids = [session_id] if session_id else list(session_lookup.keys())
            operations_by_session = await self._load_operations(session_ids)
            for op_session_id, operations in operations_by_session.items():
                session = session_lookup.get(op_session_id)
                for event in reversed(operations[-120:]):
                    if event.timestamp < cutoff_ms:
                        continue
                    items.append(self._operation_to_item(event, session))

        query = query.strip()
        if query:
            filtered = []
            for item in items:
                score = self._score_item(query, item)
                if score <= 0:
                    continue
                item["score"] = round(score, 3)
                filtered.append(item)
            filtered.sort(key=lambda item: (item["score"], item["timestamp"]), reverse=True)
            return filtered[:limit]

        items.sort(key=lambda item: item["timestamp"], reverse=True)
        for item in items:
            item["score"] = 1.0
        return items[:limit]

    def get_snapshot_image(self, snapshot_id: str) -> bytes | None:
        return self._snapshot_manager.get_screenshot_bytes(snapshot_id)

    async def _load_sessions(self, session_id: str | None) -> dict[str, Session]:
        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            if session_id:
                session = await repo.get_session(session_id)
                return {session.id: session} if session else {}

            sessions = await repo.list_sessions(limit=6, offset=0)
            return {session.id: session for session in sessions}

    async def _load_operations(self, session_ids: list[str]) -> dict[str, list[OperationEvent]]:
        if not session_ids:
            return {}

        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            result: dict[str, list[OperationEvent]] = {}
            for session_id in session_ids:
                result[session_id] = await repo.get_operations_by_session(session_id)
            return result

    def _snapshot_to_item(self, snapshot: DesktopSnapshot) -> dict:
        state = snapshot.state
        active_window = state.active_window
        focused = state.focused_element
        app_name = active_window.app_name if active_window else None
        window_title = active_window.title if active_window else None
        url = active_window.url if active_window else getattr(focused, "url", None)
        focus_label = None
        if focused:
            focus_label = focused.title or focused.identifier or focused.description
        title = _clip_text(window_title or focus_label or "屏幕快照", 64)
        summary_parts = []
        if app_name:
            summary_parts.append(app_name)
        if window_title:
            summary_parts.append(window_title)
        if focus_label:
            summary_parts.append(f"聚焦元素: {focus_label}")

        searchable_text = " ".join(
            filter(
                None,
                [
                    app_name,
                    window_title,
                    url,
                    getattr(focused, "title", None),
                    getattr(focused, "description", None),
                    getattr(focused, "identifier", None),
                    getattr(focused, "value", None),
                    getattr(focused, "selector", None),
                    getattr(focused, "xpath", None),
                ],
            )
        )

        return {
            "id": snapshot.id,
            "kind": "snapshot",
            "timestamp": snapshot.timestamp,
            "session_id": snapshot.session_id,
            "session_name": None,
            "title": title,
            "summary": _clip_text(" · ".join(summary_parts) or "捕获到一帧桌面上下文", 160),
            "app_name": app_name,
            "window_title": window_title,
            "url": url,
            "operation_type": None,
            "snapshot_id": snapshot.id,
            "preview_url": f"/api/memory/snapshots/{snapshot.id}/image" if snapshot.screenshot_bytes else None,
            "searchable_text": searchable_text.lower(),
            "tags": [tag for tag in ["snapshot", app_name] if tag],
            "metadata": {
                "focused_role": focused.role if focused else None,
                "focused_label": focus_label,
                "focused_identifier": focused.identifier if focused else None,
                "focused_selector": focused.selector if focused else None,
                "focused_xpath": focused.xpath if focused else None,
                "focused_bounds": focused.bounds.model_dump() if focused and focused.bounds else None,
                "focused_input_type": focused.input_type if focused else None,
                "focused_tag_name": focused.tag_name if focused else None,
                "preview_available": snapshot.screenshot_bytes is not None,
            },
        }

    def _operation_to_item(self, event: OperationEvent, session: Session | None) -> dict:
        context = event.context
        data = event.data
        active_window = context.active_window if context else None
        focused = context.focused_element if context else None
        app_name = active_window.app_name if active_window else context.process_name if context else None
        window_title = active_window.title if active_window else None
        url = active_window.url if active_window else getattr(focused, "url", None)

        title, summary, searchable_parts = self._describe_operation(event)
        searchable_parts.extend(
            [
                app_name,
                window_title,
                url,
                getattr(focused, "title", None),
                getattr(focused, "identifier", None),
                getattr(focused, "description", None),
            ]
        )

        return {
            "id": event.id,
            "kind": "operation",
            "timestamp": event.timestamp,
            "session_id": event.session_id,
            "session_name": session.name if session else None,
            "title": _clip_text(title, 64),
            "summary": _clip_text(summary, 160),
            "app_name": app_name,
            "window_title": window_title,
            "url": url,
            "operation_type": event.type.value,
            "snapshot_id": None,
            "preview_url": None,
            "searchable_text": " ".join(filter(None, searchable_parts)).lower(),
            "tags": [tag for tag in [event.type.value, app_name] if tag],
            "metadata": {
                "seq_num": event.seq_num,
                "focused_role": focused.role if focused else None,
                "focused_label": focused.title or focused.identifier or focused.description if focused else None,
                "focused_identifier": focused.identifier if focused else None,
                "focused_selector": focused.selector if focused else None,
                "focused_xpath": focused.xpath if focused else None,
                "focused_bounds": focused.bounds.model_dump() if focused and focused.bounds else None,
                "input_text": data.text if isinstance(data, KeyboardEventData) and not data.is_sensitive else None,
                "input_key": data.key if isinstance(data, KeyboardEventData) else None,
                "mouse_x": data.x if isinstance(data, MouseEventData) else None,
                "mouse_y": data.y if isinstance(data, MouseEventData) else None,
                "mouse_action": data.action.value if isinstance(data, MouseEventData) else None,
                "navigation_url": data.url if isinstance(data, NavigationEventData) else None,
            },
        }

    def _describe_operation(self, event: OperationEvent) -> tuple[str, str, list[str]]:
        data = event.data
        if event.type in {OperationType.MOUSE_CLICK, OperationType.MOUSE_SCROLL, OperationType.MOUSE_DRAG}:
            mouse_data = data if isinstance(data, MouseEventData) else MouseEventData(x=0, y=0)
            action = mouse_data.action.value.replace("_", " ")
            title = f"鼠标{action}"
            summary = f"在坐标 ({mouse_data.x}, {mouse_data.y}) 发生 {action}"
            return title, summary, [action]

        if event.type in {OperationType.KEY_PRESS, OperationType.KEY_INPUT}:
            key_data = data if isinstance(data, KeyboardEventData) else KeyboardEventData(key="unknown")
            if key_data.is_sensitive:
                text = "[敏感输入已隐藏]"
                searchable = []
            else:
                text = key_data.text or key_data.key
                searchable = [key_data.text or "", key_data.key]
            title = "文本输入" if event.type == OperationType.KEY_INPUT else "按键操作"
            summary = f"{title}: {_clip_text(text, 48)}"
            return title, summary, searchable

        if event.type == OperationType.WINDOW_SWITCH:
            window_data = data if isinstance(data, WindowEventData) else None
            to_window = window_data.to_window if window_data else None
            title = "窗口切换"
            summary = f"切换到 {to_window.app_name if to_window else ''} {to_window.title if to_window else ''}".strip()
            return title, summary, [to_window.title if to_window else "", to_window.app_name if to_window else ""]

        if event.type == OperationType.NAVIGATION:
            nav_data = data if isinstance(data, NavigationEventData) else None
            title = "页面导航"
            nav_target = ""
            if nav_data:
                nav_target = nav_data.title or nav_data.url
            summary = f"打开 {_clip_text(nav_target, 72)}"
            return title, summary, [nav_data.title if nav_data else "", nav_data.url if nav_data else ""]

        if event.type == OperationType.FILE_OP:
            file_data = data if isinstance(data, FileEventData) else None
            action = file_data.operation.value if file_data else "file_op"
            src_name = Path(file_data.src_path).name if file_data and file_data.src_path else "文件"
            title = "文件操作"
            summary = f"{action}: {src_name}"
            return title, summary, [action, file_data.src_path if file_data else "", file_data.dest_path if file_data else ""]

        if event.type == OperationType.CLIPBOARD:
            clip_data = data if isinstance(data, ClipboardEventData) else None
            preview = "[敏感内容已隐藏]" if clip_data and clip_data.is_sensitive else clip_data.content_preview if clip_data else ""
            title = "剪贴板变化"
            summary = f"{title}: {_clip_text(preview, 72)}"
            return title, summary, [preview if preview and not preview.startswith("[") else ""]

        fallback = event.type.value.replace("_", " ")
        return fallback, fallback, [fallback]

    def _score_item(self, query: str, item: dict) -> float:
        haystack = item.get("searchable_text", "")
        if not haystack:
            return 0.0

        query = query.lower().strip()
        terms = [term for term in re.split(r"\s+", query) if term]
        if query not in terms:
            terms.insert(0, query)

        score = 0.0
        matched = 0
        for term in terms:
            if term and term in haystack:
                matched += 1
                score += 1.0 + min(haystack.count(term) * 0.15, 0.6)
            elif term and self._fuzzy_in_order(term, haystack):
                matched += 1
                score += 0.55

        if matched == 0:
            return 0.0

        if item.get("kind") == "snapshot":
            score += 0.15
        if item.get("preview_url"):
            score += 0.1

        return score / len(terms)

    def _fuzzy_in_order(self, needle: str, haystack: str) -> bool:
        if len(needle) < 2:
            return False
        pattern = ".*?".join(re.escape(ch) for ch in needle)
        return re.search(pattern, haystack) is not None