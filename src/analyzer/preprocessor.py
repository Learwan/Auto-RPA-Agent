from src.models.operation import (
    ClipboardEventData,
    FileEventData,
    FileOperation,
    KeyboardEventData,
    MouseAction,
    MouseEventData,
    NavigationEventData,
    OperationEvent,
    OperationType,
    WindowEventData,
)

CLICK_TIME_THRESHOLD_MS = 100
KEY_INPUT_MERGE_GAP_MS = 2000
SCROLL_MERGE_GAP_MS = 500
SAME_CLICK_DEBOUNCE_MS = 300
NEIGHBOR_ANCHOR_MAX_GAP_STEPS = 2
NEIGHBOR_ANCHOR_MAX_GAP_MS = 4000
NEIGHBOR_ANCHOR_TOLERANCE_PX = 80
SCROLL_ANCHOR_TOLERANCE_PX = 180


class NormalizedOperation:
    def __init__(self, op_type: str, data: dict, timestamp: int, seq_num: int, context: dict | None = None):
        self.op_type = op_type
        self.data = data
        self.timestamp = timestamp
        self.seq_num = seq_num
        self.context = context or {}

    def to_dict(self) -> dict:
        return {
            "op_type": self.op_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "seq_num": self.seq_num,
            "context": self.context,
        }


class OperationPreprocessor:
    def preprocess(self, operations: list[OperationEvent]) -> list[NormalizedOperation]:
        if not operations:
            return []
        sorted_ops = sorted(operations, key=lambda o: o.timestamp)
        filtered = self._filter_meaningful(sorted_ops)
        merged = self._merge_adjacent(filtered)
        normalized = self._normalize(merged)
        return self._backfill_context(normalized)

    def _filter_meaningful(self, operations: list[OperationEvent]) -> list[OperationEvent]:
        result = []
        last_click_time = 0
        last_click_pos = (0, 0)
        last_click_element_sig = ""
        last_click_action = ""

        for op in operations:
            if op.type == OperationType.MOUSE_CLICK:
                data = op.data
                if not isinstance(data, MouseEventData):
                    continue
                if data.action.value in ("press", "release", "move"):
                    continue

                dx = abs(data.x - last_click_pos[0])
                dy = abs(data.y - last_click_pos[1])
                element = None
                context = op.context.model_dump() if op.context else {}
                if context.get("focused_element"):
                    fe = context["focused_element"]
                    element = f"{fe.get('role', '')}|{fe.get('identifier', '')}|{fe.get('title', '')}"

                if (
                    data.action == MouseAction.CLICK
                    and last_click_action == MouseAction.CLICK.value
                    and dx < 10
                    and dy < 10
                    and element == last_click_element_sig
                    and op.timestamp - last_click_time < SAME_CLICK_DEBOUNCE_MS
                ):
                    continue

                last_click_time = op.timestamp
                last_click_pos = (data.x, data.y)
                last_click_element_sig = element
                last_click_action = data.action.value
                result.append(op)

            elif op.type == OperationType.MOUSE_SCROLL or op.type == OperationType.MOUSE_DRAG:
                result.append(op)

            elif op.type in (OperationType.KEY_PRESS, OperationType.KEY_INPUT):
                data = op.data
                if isinstance(data, KeyboardEventData) and data.action.value == "release":
                    continue
                result.append(op)

            elif (
                op.type == OperationType.WINDOW_SWITCH
                or op.type == OperationType.NAVIGATION
                or op.type == OperationType.CLIPBOARD
                or op.type == OperationType.FILE_OP
            ):
                result.append(op)

        return result

    def _merge_adjacent(self, operations: list[OperationEvent]) -> list[OperationEvent]:
        if not operations:
            return []
        result = [operations[0]]
        for op in operations[1:]:
            last = result[-1]
            if self._should_merge(last, op):
                result[-1] = self._merge_ops(last, op)
            else:
                result.append(op)
        return result

    def _should_merge(self, a: OperationEvent, b: OperationEvent) -> bool:
        if a.type in (OperationType.KEY_INPUT, OperationType.KEY_PRESS) and b.type in (
            OperationType.KEY_INPUT,
            OperationType.KEY_PRESS,
        ):
            da = a.data
            db = b.data
            if (
                isinstance(da, KeyboardEventData)
                and isinstance(db, KeyboardEventData)
                and da.action.value in ("input", "press")
                and db.action.value in ("input", "press")
                and not da.modifiers
                and not db.modifiers
                and b.timestamp - a.timestamp < KEY_INPUT_MERGE_GAP_MS
            ):
                return True
        if a.type == OperationType.MOUSE_SCROLL and b.type == OperationType.MOUSE_SCROLL:
            da = a.data
            db = b.data
            if (
                isinstance(da, MouseEventData)
                and isinstance(db, MouseEventData)
                and b.timestamp - a.timestamp < SCROLL_MERGE_GAP_MS
                and abs(da.x - db.x) < 50
                and abs(da.y - db.y) < 50
            ):
                return True
        return False

    def _merge_ops(self, a: OperationEvent, b: OperationEvent) -> OperationEvent:
        da = a.data
        db = b.data
        if isinstance(da, KeyboardEventData) and isinstance(db, KeyboardEventData):
            text_a = da.text or da.key or ""
            text_b = db.text or db.key or ""
            merged_text = text_a + text_b
            merged_data = KeyboardEventData(
                key="text_input",
                action=da.action,
                text=merged_text,
                modifiers=[],
                is_sensitive=da.is_sensitive or db.is_sensitive,
            )
            return OperationEvent(
                id=a.id,
                session_id=a.session_id,
                seq_num=a.seq_num,
                timestamp=a.timestamp,
                type=OperationType.KEY_INPUT,
                data=merged_data,
                context=b.context,
            )
        if isinstance(da, MouseEventData) and isinstance(db, MouseEventData):
            merged_data = MouseEventData(
                x=db.x,
                y=db.y,
                button=db.button,
                action=db.action,
                scroll_dx=da.scroll_dx + db.scroll_dx,
                scroll_dy=da.scroll_dy + db.scroll_dy,
            )
            return OperationEvent(
                id=a.id,
                session_id=a.session_id,
                seq_num=a.seq_num,
                timestamp=a.timestamp,
                type=OperationType.MOUSE_SCROLL,
                data=merged_data,
                context=b.context,
            )
        return b

    def _normalize(self, operations: list[OperationEvent]) -> list[NormalizedOperation]:
        result = []
        for i, op in enumerate(operations):
            norm = self._normalize_single(op, i)
            if norm:
                result.append(norm)
        return result

    def _backfill_context(self, operations: list[NormalizedOperation]) -> list[NormalizedOperation]:
        last_active_window: dict | None = None
        last_process_name: str | None = None
        last_process_pid: int | None = None
        last_platform: str | None = None
        last_anchor_by_window: dict[tuple, dict] = {}
        last_scroll_anchor_by_window: dict[tuple, dict] = {}

        for op in operations:
            context = dict(op.context or {})
            active_window = self._resolve_active_window(context, op)

            if active_window is None and last_active_window is not None and op.op_type != "switch_window":
                active_window = dict(last_active_window)
                context["active_window"] = active_window
                context["active_window_inferred"] = True
            elif active_window is not None:
                context["active_window"] = active_window

            if not context.get("process_name"):
                process_name = (context.get("active_window") or {}).get("app_name") or last_process_name
                if process_name:
                    context["process_name"] = process_name

            if context.get("process_pid") is None:
                process_pid = (context.get("active_window") or {}).get("pid")
                if process_pid is None:
                    process_pid = last_process_pid
                if process_pid is not None:
                    context["process_pid"] = process_pid

            if not context.get("platform") and last_platform:
                context["platform"] = last_platform

            window_key = self._window_context_key(context)
            focused_element = self._compact_anchor_element(context.get("focused_element"))

            if focused_element and self._is_meaningful_anchor_element(focused_element):
                anchor_state = {
                    "element": focused_element,
                    "seq_num": op.seq_num,
                    "timestamp": op.timestamp,
                }
                if window_key:
                    last_anchor_by_window[window_key] = anchor_state
                    if self._is_scrollable_anchor_element(focused_element):
                        last_scroll_anchor_by_window[window_key] = anchor_state
            elif window_key:
                if op.op_type == "mouse_scroll":
                    scroll_anchor = self._select_scroll_anchor(last_scroll_anchor_by_window.get(window_key), op)
                    if scroll_anchor:
                        context["scroll_anchor"] = dict(scroll_anchor["element"])
                        context["scroll_anchor_inferred"] = True
                elif op.op_type in {"mouse_click", "mouse_double_click", "mouse_right_click"}:
                    neighbor_anchor = self._select_neighbor_anchor(last_anchor_by_window.get(window_key), op)
                    if neighbor_anchor:
                        context["neighbor_anchor"] = dict(neighbor_anchor["element"])
                        context["neighbor_anchor_inferred"] = True

            op.context = context

            if context.get("platform"):
                last_platform = context["platform"]
            if isinstance(context.get("active_window"), dict):
                last_active_window = dict(context["active_window"])
            if context.get("process_name"):
                last_process_name = context["process_name"]
            if context.get("process_pid") is not None:
                last_process_pid = context["process_pid"]

        return operations

    @staticmethod
    def _window_context_key(context: dict) -> tuple | None:
        active_window = context.get("active_window") or {}
        parts = (
            active_window.get("title") or "",
            active_window.get("app_name") or "",
            active_window.get("url") or "",
        )
        return parts if any(parts) else None

    @staticmethod
    def _compact_anchor_element(element: dict | None) -> dict | None:
        if not isinstance(element, dict):
            return None
        compact = {
            key: element.get(key)
            for key in (
                "role",
                "title",
                "identifier",
                "value",
                "description",
                "functional_label",
                "class_name",
                "selector",
                "xpath",
                "url",
                "frame",
                "tag_name",
                "input_type",
                "bounds",
            )
            if element.get(key) not in (None, "", {}, [])
        }
        return compact or None

    @staticmethod
    def _is_meaningful_anchor_element(element: dict | None) -> bool:
        if not isinstance(element, dict):
            return False
        return any(
            element.get(key) not in (None, "", {}, [])
            for key in (
                "identifier",
                "selector",
                "xpath",
                "title",
                "value",
                "description",
                "functional_label",
                "role",
                "class_name",
                "bounds",
            )
        )

    @staticmethod
    def _is_scrollable_anchor_element(element: dict | None) -> bool:
        if not isinstance(element, dict):
            return False
        haystack = " ".join(
            str(value).casefold()
            for value in (
                element.get("role"),
                element.get("title"),
                element.get("description"),
                element.get("functional_label"),
                element.get("class_name"),
            )
            if value
        )
        return any(token in haystack for token in ("scroll", "滚动", "list", "table", "tree", "outline"))

    @staticmethod
    def _operation_position(op: NormalizedOperation) -> tuple[int, int] | None:
        x = op.data.get("x")
        y = op.data.get("y")
        if isinstance(x, int) and isinstance(y, int):
            return x, y
        return None

    @classmethod
    def _position_near_bounds(cls, op: NormalizedOperation, bounds: dict | None, tolerance: int) -> bool:
        if not isinstance(bounds, dict):
            return False
        position = cls._operation_position(op)
        if position is None:
            return False
        x, y = position
        bx = bounds.get("x")
        by = bounds.get("y")
        bw = bounds.get("width")
        bh = bounds.get("height")
        if not all(isinstance(value, int) for value in (bx, by, bw, bh)):
            return False
        return (
            bx - tolerance <= x <= bx + bw + tolerance
            and by - tolerance <= y <= by + bh + tolerance
        )

    @classmethod
    def _select_neighbor_anchor(cls, anchor_state: dict | None, op: NormalizedOperation) -> dict | None:
        if not anchor_state:
            return None
        if (op.seq_num - anchor_state.get("seq_num", -999)) > NEIGHBOR_ANCHOR_MAX_GAP_STEPS:
            return None
        if (op.timestamp - anchor_state.get("timestamp", 0)) > NEIGHBOR_ANCHOR_MAX_GAP_MS:
            return None
        element = anchor_state.get("element") or {}
        if not cls._position_near_bounds(op, element.get("bounds"), NEIGHBOR_ANCHOR_TOLERANCE_PX):
            return None
        return anchor_state

    @classmethod
    def _select_scroll_anchor(cls, anchor_state: dict | None, op: NormalizedOperation) -> dict | None:
        if not anchor_state:
            return None
        element = anchor_state.get("element") or {}
        bounds = element.get("bounds")
        if bounds and not cls._position_near_bounds(op, bounds, SCROLL_ANCHOR_TOLERANCE_PX):
            return None
        return anchor_state

    @staticmethod
    def _resolve_active_window(context: dict, op: NormalizedOperation) -> dict | None:
        active_window = context.get("active_window")
        if isinstance(active_window, dict) and any(
            active_window.get(key) not in (None, "")
            for key in ("window_id", "title", "app_name", "url")
        ):
            return dict(active_window)

        if op.op_type == "switch_window":
            inferred = {
                "title": op.data.get("title"),
                "app_name": op.data.get("app_name"),
                "pid": op.data.get("pid"),
                "url": op.data.get("url"),
            }
            compact = {key: value for key, value in inferred.items() if value not in (None, "")}
            return compact or None

        if op.op_type == "navigation":
            inferred = {
                "title": op.data.get("title"),
                "url": op.data.get("url"),
            }
            compact = {key: value for key, value in inferred.items() if value not in (None, "")}
            return compact or None

        return None

    def _normalize_single(self, op: OperationEvent, idx: int) -> NormalizedOperation | None:
        data = op.data
        context = op.context.model_dump() if op.context else {}

        if op.type == OperationType.MOUSE_CLICK and isinstance(data, MouseEventData):
            return NormalizedOperation(
                op_type=f"mouse_{data.action.value}",
                data={
                    "x": data.x,
                    "y": data.y,
                    "button": data.button.value,
                    "scroll_dx": data.scroll_dx,
                    "scroll_dy": data.scroll_dy,
                    "drag_start": {"x": data.drag_start_x, "y": data.drag_start_y} if data.drag_start_x else None,
                },
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        elif op.type == OperationType.MOUSE_SCROLL and isinstance(data, MouseEventData):
            return NormalizedOperation(
                op_type="mouse_scroll",
                data={"x": data.x, "y": data.y, "scroll_dx": data.scroll_dx, "scroll_dy": data.scroll_dy},
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        elif op.type == OperationType.MOUSE_DRAG and isinstance(data, MouseEventData):
            return NormalizedOperation(
                op_type="mouse_drag",
                data={
                    "x": data.x,
                    "y": data.y,
                    "button": data.button.value,
                    "drag_start": {"x": data.drag_start_x, "y": data.drag_start_y} if data.drag_start_x else None,
                },
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        elif op.type in (OperationType.KEY_PRESS, OperationType.KEY_INPUT) and isinstance(data, KeyboardEventData):
            if data.modifiers:
                return NormalizedOperation(
                    op_type="hotkey",
                    data={"key": data.key, "modifiers": data.modifiers},
                    timestamp=op.timestamp,
                    seq_num=idx,
                    context=context,
                )
            text = data.text or data.key
            return NormalizedOperation(
                op_type="type_text",
                data={"text": text, "is_sensitive": data.is_sensitive},
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        elif op.type == OperationType.WINDOW_SWITCH and isinstance(data, WindowEventData):
            to_win = data.to_window
            return NormalizedOperation(
                op_type="switch_window",
                data={"app_name": to_win.app_name, "title": to_win.title, "pid": to_win.pid, "url": to_win.url},
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        elif op.type == OperationType.NAVIGATION and isinstance(data, NavigationEventData):
            return NormalizedOperation(
                op_type="navigation",
                data={
                    "title": data.title,
                    "url": data.url,
                    "transition": data.transition,
                    "from_url": data.from_url,
                    "from_title": data.from_title,
                    "same_document": data.same_document,
                },
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        elif op.type == OperationType.FILE_OP and isinstance(data, FileEventData):
            if data.operation == FileOperation.UPLOAD:
                paths = data.paths or ([data.src_path] if data.src_path else [])
                return NormalizedOperation(
                    op_type="upload_file",
                    data={
                        "src_path": data.src_path,
                        "dest_path": data.dest_path,
                        "paths": paths,
                        "file_type": data.file_type,
                    },
                    timestamp=op.timestamp,
                    seq_num=idx,
                    context=context,
                )
            if data.operation == FileOperation.DOWNLOAD:
                return NormalizedOperation(
                    op_type="download_file",
                    data={"src_path": data.src_path, "dest_path": data.dest_path, "file_type": data.file_type},
                    timestamp=op.timestamp,
                    seq_num=idx,
                    context=context,
                )
            return NormalizedOperation(
                op_type="file_op",
                data={
                    "operation": data.operation.value,
                    "src_path": data.src_path,
                    "dest_path": data.dest_path,
                    "paths": data.paths,
                },
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        elif op.type == OperationType.CLIPBOARD and isinstance(data, ClipboardEventData):
            return NormalizedOperation(
                op_type="clipboard",
                data={"content_type": data.content_type.value, "preview": data.content_preview},
                timestamp=op.timestamp,
                seq_num=idx,
                context=context,
            )
        return None
