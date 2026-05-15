import asyncio
import hashlib
import time
import uuid

from src.models.operation import ClipboardEventData, ClipboardType, OperationContext, OperationEvent, OperationType
from src.recorder.base import BaseRecorder

SENSITIVE_KEYWORDS = ("password", "passwd", "secret", "token", "api_key", "apikey", "credential", "private_key")
MAX_PREVIEW_LENGTH = 200


class ClipboardRecorder(BaseRecorder):
    def __init__(self, poll_interval: float = 1.0):
        super().__init__()
        self._poll_interval = poll_interval
        self._session_id = ""
        self._seq_num = 0
        self._last_content_hash: str = ""
        self._poll_task: asyncio.Task | None = None

    def start(self, session_id: str, callback) -> None:
        self._session_id = session_id
        self._callback = callback
        self._seq_num = 0
        self._last_content_hash = ""
        self._running = True
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    def stop(self) -> None:
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None
        self._last_content_hash = ""

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._poll_interval)
                if not self._running:
                    break
                content = await self._read_clipboard()
                if content is None:
                    continue
                content_hash = self._hash_content(content)
                if content_hash == self._last_content_hash:
                    continue
                self._last_content_hash = content_hash
                self._emit_clipboard_event(content, content_hash)
            except asyncio.CancelledError:
                break
            except Exception:
                continue

    async def _read_clipboard(self) -> str | None:
        try:
            import subprocess

            result = subprocess.run(
                ["pbcopy", "-help"] if False else ["osascript", "-e", "the clipboard as text"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        try:
            import pyperclip

            content = pyperclip.paste()
            return content if content else None
        except Exception:
            pass
        return None

    def _hash_content(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]

    def _is_sensitive(self, content: str) -> bool:
        lower = content.lower()
        return any(kw in lower for kw in SENSITIVE_KEYWORDS)

    def _emit_clipboard_event(self, content: str, content_hash: str) -> None:
        if not self._callback:
            return
        is_sensitive = self._is_sensitive(content)
        preview = content[:MAX_PREVIEW_LENGTH] if not is_sensitive else "***REDACTED***"
        data = ClipboardEventData(
            content_type=ClipboardType.TEXT,
            content_preview=preview,
            content_hash=content_hash,
            is_sensitive=is_sensitive,
        )
        context = OperationContext()
        event = OperationEvent(
            id=str(uuid.uuid4()),
            session_id=self._session_id,
            seq_num=self._seq_num,
            timestamp=int(time.time() * 1000),
            type=OperationType.CLIPBOARD,
            data=data,
            context=context,
        )
        self._seq_num += 1
        self._callback(event)
