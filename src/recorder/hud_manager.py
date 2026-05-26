from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.config import settings
from src.llm.service import LLMService
from src.models.hotkey import HotkeyBinding
from src.models.desktop import UIElement, WindowInfo
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
from src.settings_store import SettingsStore

logger = logging.getLogger(__name__)

HUD_LOCAL_AI_TIMEOUT_S = 4.0
HUD_REMOTE_AI_TIMEOUT_S = 8.0

_SENSITIVE_ELEMENT_KEYWORDS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "otp",
    "pin",
    "验证码",
    "密码",
    "口令",
    "安全码",
}


def _stringify_text(value: Any | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _truncate_text(value: str, limit: int = 48) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _element_is_sensitive(
    *,
    role: Any | None,
    title: Any | None,
    identifier: Any | None,
    description: Any | None,
    functional_label: Any | None,
    class_name: Any | None,
) -> bool:
    haystack = " ".join(
        part.casefold()
        for part in (
            _stringify_text(role),
            _stringify_text(title),
            _stringify_text(identifier),
            _stringify_text(description),
            _stringify_text(functional_label),
            _stringify_text(class_name),
        )
        if part
    )
    return any(keyword in haystack for keyword in _SENSITIVE_ELEMENT_KEYWORDS)


def _element_text_preview(element: UIElement | None, *, allow_redacted: bool = True) -> str | None:
    if not element:
        return None
    preview = _stringify_text(element.value)
    if not preview:
        return None
    if _element_is_sensitive(
        role=element.role,
        title=element.title,
        identifier=element.identifier,
        description=element.description,
        functional_label=element.functional_label,
        class_name=element.class_name,
    ):
        return "文本已脱敏" if allow_redacted else None
    return _truncate_text(preview)


def _element_text_preview_from_payload(element: dict[str, Any] | None, *, allow_redacted: bool = True) -> str | None:
    if not element:
        return None
    preview = _stringify_text(element.get("value"))
    if not preview:
        return None
    if _element_is_sensitive(
        role=element.get("role"),
        title=element.get("title"),
        identifier=element.get("identifier"),
        description=element.get("description"),
        functional_label=element.get("functional_label"),
        class_name=element.get("class_name"),
    ):
        return "文本已脱敏" if allow_redacted else None
    return _truncate_text(preview)


def _window_label(window: WindowInfo | None) -> str:
    if not window:
        return "未识别窗口"
    title = (window.title or "").strip()
    app_name = (window.app_name or "").strip()
    if title and app_name:
        return f"{app_name} · {title}"
    return title or app_name or "未识别窗口"


def _element_label(element: UIElement | None) -> str:
    if not element:
        return "未识别元素"

    candidates = [
        element.functional_label,
        element.title,
        element.description,
        _element_text_preview(element),
        element.identifier,
    ]
    for item in candidates:
        if item:
            return str(item).strip()

    role = (element.role or "element").strip()
    return role or "未识别元素"


def _window_label_from_payload(window: dict[str, Any] | None) -> str:
    if not window:
        return "未识别窗口"
    title = str(window.get("title") or "").strip()
    app_name = str(window.get("app_name") or "").strip()
    if title and app_name:
        return f"{app_name} · {title}"
    return title or app_name or "未识别窗口"


def _element_label_from_payload(element: dict[str, Any] | None) -> str:
    if not element:
        return "未识别元素"
    for value in (
        element.get("functional_label"),
        element.get("title"),
        element.get("description"),
        _element_text_preview_from_payload(element),
        element.get("identifier"),
    ):
        if value:
            return str(value).strip()
    return str(element.get("role") or "未识别元素").strip() or "未识别元素"


def summarize_operation_event(event: OperationEvent) -> dict[str, str]:
    window = _window_label(event.context.active_window if event.context else None)
    focused = _element_label(event.context.focused_element if event.context else None)
    action = event.type.value
    detail = ""

    if isinstance(event.data, MouseEventData):
        action = {
            OperationType.MOUSE_CLICK.value: "鼠标点击",
            OperationType.MOUSE_SCROLL.value: "鼠标滚动",
            OperationType.MOUSE_DRAG.value: "鼠标拖拽",
        }.get(event.type.value, action)
        detail = f"{event.data.action.value} @ ({event.data.x}, {event.data.y})"
    elif isinstance(event.data, KeyboardEventData):
        action = "快捷键" if event.data.action.value == "hotkey" else "键盘输入"
        if event.data.is_sensitive:
            detail = "敏感输入已脱敏"
        elif event.data.text:
            detail = f"输入: {event.data.text[:40]}"
        elif event.data.modifiers:
            detail = "+".join([*event.data.modifiers, event.data.key])
        else:
            detail = event.data.key
    elif isinstance(event.data, WindowEventData):
        action = "窗口切换"
        detail = f"切到 {_window_label(event.data.to_window)}"
    elif isinstance(event.data, NavigationEventData):
        action = "页面导航"
        detail = event.data.url
    elif isinstance(event.data, ClipboardEventData):
        action = "剪贴板"
        detail = "敏感内容已脱敏" if event.data.is_sensitive else event.data.content_preview[:40]
    elif isinstance(event.data, FileEventData):
        action = "文件操作"
        detail = f"{event.data.operation.value}: {Path(event.data.src_path).name}"

    return {
        "action": action,
        "detail": detail,
        "window": window,
        "focused": focused,
    }


def build_recording_prompt_context(
    session_name: str,
    status: str,
    operation_count: int,
    recent_events: Iterable[dict[str, str]],
    latest_window: str,
    latest_focused: str,
) -> str:
    recent_lines = [
        f"- {item['action']}: {item['detail']} | 窗口: {item['window']} | 焦点: {item['focused']}"
        for item in recent_events
    ]
    if not recent_lines:
        recent_lines = ["- 暂无捕获事件"]

    return "\n".join(
        [
            f"会话: {session_name}",
            f"录制状态: {status}",
            f"已捕获操作数: {operation_count}",
            f"当前窗口: {latest_window}",
            f"当前焦点元素: {latest_focused}",
            "最近捕获的操作:",
            *recent_lines,
        ]
    )


class RecordingHUDManager:
    def __init__(self):
        self._process: subprocess.Popen[str] | None = None
        self._stdin_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active_session_id: str | None = None
        self._session_name = ""
        self._session_status = "idle"
        self._operation_count = 0
        self._interactive = False
        self._hud_ai_assist = False
        self._recent_events: deque[dict[str, str]] = deque(maxlen=6)
        self._latest_window = "未识别窗口"
        self._latest_focused = "未识别元素"
        self._llm = LLMService()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._focus_context_request_handler = None
        self._settings_store = SettingsStore.get_instance()

    def set_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def set_focus_context_request_handler(self, handler) -> None:
        self._focus_context_request_handler = handler

    def start_session(self, session_id: str, session_name: str, hud_ai_assist: bool = False) -> None:
        if sys.platform != "darwin":
            return

        if self._active_session_id != session_id:
            self.stop_session(self._active_session_id)

        self._ensure_process()
        self._active_session_id = session_id
        self._session_name = session_name
        self._session_status = "recording"
        self._operation_count = 0
        self._interactive = False
        self._hud_ai_assist = hud_ai_assist
        self._recent_events.clear()
        self._latest_window = "等待捕获窗口"
        self._latest_focused = "等待捕获元素"
        self._send_command({"type": "clear_messages"})
        self._send_command(
            {
                "type": "append_message",
                "role": "assistant",
                "content": (
                    "HUD 已连接。默认处于穿透显示模式，不会抢占前台焦点。"
                    f"按 {self._hotkey_display(self._current_hotkeys().get('toggle_hud_interaction'))} 可切换到 LLM 交互态，"
                    f"按 {self._hotkey_display(self._current_hotkeys().get('capture_focus_context'))} 可手动抓取焦点上下文。"
                ),
            }
        )
        self._push_state()
        self._send_command({"type": "mode", "interactive": False})

    def update_session_status(self, session_id: str, status: str, operation_count: int = 0) -> None:
        if session_id != self._active_session_id:
            return
        self._session_status = status
        self._operation_count = operation_count
        self._push_state()

    def stop_session(self, session_id: str | None, operation_count: int | None = None) -> None:
        if session_id is not None and session_id != self._active_session_id:
            return
        if operation_count is not None:
            self._operation_count = operation_count
        self._session_status = "stopped"
        self._push_state()
        self._send_command({"type": "close"})
        self._terminate_process()
        self._active_session_id = None

    def toggle_interaction(self, session_id: str | None = None) -> None:
        self.set_interaction(not self._interactive, session_id=session_id)

    def set_interaction(self, interactive: bool, session_id: str | None = None) -> None:
        if session_id and session_id != self._active_session_id:
            return
        if self._process is None or self._process.poll() is not None:
            return
        if self._interactive == interactive:
            return
        self._interactive = interactive
        self._send_command({"type": "mode", "interactive": self._interactive})
        if self._interactive:
            self._send_command(
                {
                    "type": "append_message",
                    "role": "assistant",
                    "content": "已进入交互态。描述你当前要录什么，我会结合刚捕获的窗口、元素和最近操作给出下一步建议。",
                }
            )

    def handle_operation(self, event: OperationEvent, operation_count: int) -> None:
        if event.session_id != self._active_session_id:
            return

        summary = summarize_operation_event(event)
        with self._state_lock:
            self._recent_events.appendleft(summary)
            self._latest_window = summary["window"]
            self._latest_focused = summary["focused"]
            self._operation_count = operation_count
        self._push_state()

    def present_focus_context(self, capture: dict[str, Any], *, enter_interaction: bool = True) -> None:
        if self._active_session_id is None:
            return

        state = capture.get("state") or {}
        self._latest_window = _window_label_from_payload(state.get("active_window"))
        self._latest_focused = _element_label_from_payload(state.get("focused_element"))
        self._push_state()
        if not enter_interaction:
            return

        self.set_interaction(True, session_id=self._active_session_id)
        self._send_command(
            {
                "type": "append_message",
                "role": "assistant",
                "content": self._build_focus_context_message(capture),
            }
        )

    def _ensure_process(self) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                return
            self._terminate_process()

        root_dir = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        self._process = subprocess.Popen(
            [sys.executable, "-u", "-m", "src.recorder.hud_overlay"],
            cwd=str(root_dir),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stdout_thread = threading.Thread(target=self._read_stdout_loop, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr_loop, daemon=True)
        self._stderr_thread.start()

    def _terminate_process(self) -> None:
        process = self._process
        if process is None:
            return

        try:
            if process.stdin is not None:
                with contextlib.suppress(Exception):
                    process.stdin.close()

            if process.poll() is None:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.25)

            if process.poll() is None:
                with contextlib.suppress(Exception):
                    process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=1.5)

            if process.poll() is None:
                with contextlib.suppress(Exception):
                    process.kill()
                with contextlib.suppress(Exception):
                    process.wait(timeout=1.0)
            else:
                with contextlib.suppress(Exception):
                    process.wait(timeout=0.1)
        finally:
            self._process = None
            self._stdout_thread = None
            self._stderr_thread = None

    def _push_state(self) -> None:
        if self._active_session_id is None:
            return
        hotkeys = self._current_hotkeys()
        with self._state_lock:
            recent_events = list(self._recent_events)
            payload = {
                "session_id": self._active_session_id,
                "session_name": self._session_name,
                "status": self._session_status,
                "operation_count": self._operation_count,
                "latest_window": self._latest_window,
                "latest_focused": self._latest_focused,
                "recent_events": recent_events,
                "hud_ai_assist": self._hud_ai_assist,
                "hotkeys": {
                    "start_recording": self._hotkey_display(hotkeys.get("start_recording")),
                    "pause_resume_recording": self._hotkey_display(hotkeys.get("pause_resume_recording")),
                    "stop_recording": self._hotkey_display(hotkeys.get("stop_recording")),
                    "toggle_hud_interaction": self._hotkey_display(hotkeys.get("toggle_hud_interaction")),
                    "capture_focus_context": self._hotkey_display(hotkeys.get("capture_focus_context")),
                },
            }
        self._send_command({"type": "state", "payload": payload})

    def _current_hotkeys(self) -> dict[str, HotkeyBinding | None]:
        settings = self._settings_store.get_settings()
        hotkeys = getattr(settings, "hotkeys", None)
        if hotkeys is None:
            return {}
        return {
            "start_recording": getattr(hotkeys, "start_recording", None),
            "pause_resume_recording": getattr(hotkeys, "pause_resume_recording", None),
            "stop_recording": getattr(hotkeys, "stop_recording", None),
            "toggle_hud_interaction": getattr(hotkeys, "toggle_hud_interaction", None),
            "capture_focus_context": getattr(hotkeys, "capture_focus_context", None),
        }

    @staticmethod
    def _hotkey_display(binding: HotkeyBinding | None) -> str:
        if binding is None:
            return "未配置"
        if not binding.enabled:
            return "已禁用"
        return binding.display_string

    def _send_command(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            return
        try:
            with self._stdin_lock:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
        except Exception as exc:
            logger.debug("HUD command send failed: %s", exc)

    def _read_stdout_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("HUD stdout: %s", line)
                continue

            self._handle_overlay_message(message)

    def _read_stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            line = line.strip()
            if line:
                logger.debug("HUD stderr: %s", line)

    def _schedule_prompt(self, prompt: str) -> None:
        self._send_command({"type": "append_message", "role": "user", "content": prompt})
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._handle_prompt(prompt), self._loop)
            future.add_done_callback(lambda f: f.exception() if not f.cancelled() and f.exception() else None)
        else:
            self._send_command(
                {
                    "type": "append_message",
                    "role": "assistant",
                    "content": "HUD 当前没有可用事件循环，暂时无法向 LLM 提问。",
                }
            )

    def _handle_overlay_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "").strip()
        if message_type == "prompt":
            prompt = str(message.get("prompt") or "").strip()
            if prompt:
                self._schedule_prompt(prompt)
            return

        if message_type in {"toggle_interaction", "set_interaction"}:
            requested = message.get("interactive")
            interactive = (not self._interactive) if requested is None else bool(requested)
            self.set_interaction(interactive, session_id=self._active_session_id)
            return

        if message_type == "capture_focus_context":
            if callable(self._focus_context_request_handler):
                self._send_command(
                    {
                        "type": "append_message",
                        "role": "assistant",
                        "content": "正在抓取当前焦点上下文，请稍候...",
                    }
                )
                try:
                    self._focus_context_request_handler()
                except Exception as exc:
                    logger.warning("HUD focus context request failed: %s", exc)
                    self._send_command(
                        {
                            "type": "append_message",
                            "role": "assistant",
                            "content": f"抓取焦点上下文失败: {exc}",
                        }
                    )

    def _build_focus_context_message(self, capture: dict[str, Any]) -> str:
        lines = ["已抓取当前焦点上下文。"]

        summary = str(capture.get("focus_summary") or "").strip()
        if summary:
            lines.append(summary)

        node_suggestion = capture.get("node_suggestion") or {}
        target = node_suggestion.get("target") or {}
        if node_suggestion:
            lines.append(
                "结构化节点建议: "
                f"{node_suggestion.get('step_type', 'unknown')}"
                f" | 定位 {target.get('strategy', 'unknown')}"
                f" | 说明 {node_suggestion.get('description', '暂无')}"
            )

        guidance = str(capture.get("recording_guidance") or "").strip()
        if guidance:
            lines.append(f"录制建议:\n{guidance}")

        return "\n".join(lines)

    async def _chat_with_local_llm(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        from src.llm.local_engine import LocalLLMEngine

        engine = await LocalLLMEngine.get_instance()
        result = await engine.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return str(result.text).strip()

    async def _chat_with_remote_llm(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> str:
        response = await self._llm.chat(
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=False,
        )
        return str(response).strip()

    async def _handle_prompt(self, prompt: str) -> None:
        context_text = build_recording_prompt_context(
            session_name=self._session_name or "未命名会话",
            status=self._session_status,
            operation_count=self._operation_count,
            recent_events=list(self._recent_events),
            latest_window=self._latest_window,
            latest_focused=self._latest_focused,
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是桌面录制引导助手。用户正在录制自动化流程。"
                    "请只依据给定录制上下文给出简短、可执行的下一步指导，优先提醒窗口、元素、变量输入、检查点和分支风险。"
                ),
            },
            {
                "role": "user",
                "content": f"录制上下文:\n{context_text}\n\n用户问题: {prompt}",
            },
        ]

        local_enabled = settings.LOCAL_LLM_ENABLED
        remote_enabled = self._llm.is_configured

        if not local_enabled and not remote_enabled:
            self._send_command(
                {
                    "type": "append_message",
                    "role": "assistant",
                    "content": "远端文本 LLM 未配置，本地 LLM 也未启用，当前只能显示录制上下文，无法提供实时引导。",
                }
            )
            return

        self._send_command({"type": "thinking", "visible": True})
        local_error: Exception | None = None
        remote_error: Exception | None = None
        try:
            response = ""
            if local_enabled:
                try:
                    response = await asyncio.wait_for(
                        self._chat_with_local_llm(
                            messages,
                            temperature=0.3,
                            max_tokens=800,
                        ),
                        timeout=HUD_LOCAL_AI_TIMEOUT_S,
                    )
                except TimeoutError:
                    local_error = TimeoutError(f"本地 LLM 响应超时（{HUD_LOCAL_AI_TIMEOUT_S:.1f}s）")
                    logger.info("HUD local prompt timed out after %.1fs", HUD_LOCAL_AI_TIMEOUT_S)
                except Exception as exc:
                    local_error = exc
                    logger.warning("HUD local prompt failed, falling back to remote LLM: %s", exc, exc_info=True)

            if not response and remote_enabled:
                try:
                    response = await asyncio.wait_for(
                        self._chat_with_remote_llm(
                            messages,
                            temperature=0.3,
                            top_p=0.9,
                            max_tokens=800,
                        ),
                        timeout=HUD_REMOTE_AI_TIMEOUT_S,
                    )
                except TimeoutError:
                    remote_error = TimeoutError(f"远端文本 LLM 响应超时（{HUD_REMOTE_AI_TIMEOUT_S:.1f}s）")
                    logger.info("HUD remote prompt timed out after %.1fs", HUD_REMOTE_AI_TIMEOUT_S)
                except Exception as exc:
                    remote_error = exc
                    logger.warning("HUD remote prompt failed: %s", exc, exc_info=True)

            if not response:
                detail_parts: list[str] = []
                if local_error is not None:
                    detail_parts.append(
                        f"本地 LLM 当前不可用（{_truncate_text(str(local_error), limit=120)}）"
                    )
                if remote_enabled:
                    if remote_error is not None:
                        detail_parts.append(
                            f"远端文本 LLM 当前不可用（{_truncate_text(str(remote_error), limit=120)}）"
                        )
                else:
                    detail_parts.append("远端文本 LLM 未配置")

                detail = "；".join(detail_parts)
                self._send_command(
                    {
                        "type": "append_message",
                        "role": "assistant",
                        "content": f"{detail}，当前只能显示录制上下文，无法提供实时引导。",
                    }
                )
                return

            self._send_command(
                {
                    "type": "append_message",
                    "role": "assistant",
                    "content": response,
                }
            )
        except Exception as exc:
            logger.error("HUD prompt failed: %s", exc, exc_info=True)
            self._send_command(
                {
                    "type": "append_message",
                    "role": "assistant",
                    "content": f"LLM 请求失败: {exc}",
                }
            )
        finally:
            self._send_command({"type": "thinking", "visible": False})