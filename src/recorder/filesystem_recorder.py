import asyncio
import os
import time
import uuid
from pathlib import Path

from watchdog.events import (
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from src.config import settings
from src.models.operation import FileEventData, FileOperation, OperationContext, OperationEvent, OperationType
from src.recorder.base import BaseRecorder

IGNORED_PREFIXES = (".", "~", "/tmp/", "/var/", "/Library/", "/System/", "/.Trash", "/proc/", "/dev/")
IGNORED_SUFFIXES = ("-journal", "-wal", "-shm")
IGNORED_EXTENSIONS = (
    ".tmp",
    ".log",
    ".lock",
    ".swp",
    ".swo",
    ".DS_Store",
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
)
IGNORED_DIRS = {
    ".git",
    ".svn",
    "__pycache__",
    "node_modules",
    ".idea",
    ".vscode",
    ".cache",
    ".venv",
    ".trae",
    "venv",
    "env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
DEBOUNCE_MS = 1000


def _normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _sqlite_database_path(database_url: str | None) -> str | None:
    if not database_url or not database_url.startswith("sqlite"):
        return None
    if "///" not in database_url:
        return None
    raw_path = database_url.split("///", 1)[1].strip()
    if not raw_path:
        return None
    return _normalize_path(raw_path)


class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, loop, callback, ignored_paths: tuple[str, ...] = ()): 
        self._loop = loop
        self._callback = callback
        self._last_emit: dict[str, tuple[str, float]] = {}
        self._ignored_paths = ignored_paths

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event, FileOperation.CREATE)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event, FileOperation.MODIFY)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event, FileOperation.DELETE)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(
                event, FileOperation.MOVE, dest_path=event.dest_path if isinstance(event, FileMovedEvent) else None
            )

    def _emit(self, event: FileSystemEvent, op: FileOperation, dest_path: str | None = None) -> None:
        if self._should_filter(event.src_path):
            return
        now = time.time()
        last_op, last_time = self._last_emit.get(event.src_path, ("", 0))
        if op == FileOperation.MODIFY and last_op == FileOperation.MODIFY and (now - last_time) < DEBOUNCE_MS / 1000:
            return
        self._last_emit[event.src_path] = (op.value, now)
        if len(self._last_emit) > 1000:
            oldest = sorted(self._last_emit.items(), key=lambda x: x[1][1])[:500]
            self._last_emit = dict(oldest)
        data = FileEventData(
            operation=op,
            src_path=event.src_path,
            dest_path=dest_path,
            file_type=Path(event.src_path).suffix or None,
        )
        if self._loop and self._callback:
            asyncio.run_coroutine_threadsafe(self._callback(data, op), self._loop)

    def _should_filter(self, path: str) -> bool:
        normalized_path = _normalize_path(path)
        name = os.path.basename(normalized_path)
        if any(name.startswith(p) for p in (".", "~")):
            return True
        if any(normalized_path.startswith(p) for p in IGNORED_PREFIXES):
            return True
        if any(name.endswith(suffix) for suffix in IGNORED_SUFFIXES):
            return True
        if any(name.endswith(ext) for ext in IGNORED_EXTENSIONS):
            return True
        if any(
            normalized_path == ignored or normalized_path.startswith(f"{ignored}{os.sep}")
            for ignored in self._ignored_paths
        ):
            return True
        parts = Path(normalized_path).parts
        return bool(any(part in IGNORED_DIRS for part in parts))


class FilesystemRecorder(BaseRecorder):
    def __init__(self, watch_paths: list[str] | None = None):
        super().__init__()
        self._session_id = ""
        self._seq_num = 0
        self._observer: Observer | None = None
        self._handler: _WatchdogHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watch_paths = watch_paths or self._default_watch_paths()
        self._ignored_paths = tuple(self._default_ignored_paths())

    @staticmethod
    def _default_watch_paths() -> list[str]:
        cwd = os.getcwd()
        return [cwd] if os.path.isdir(cwd) else []

    @staticmethod
    def _default_ignored_paths() -> list[str]:
        candidates = {
            settings.DATA_DIR,
            settings.SCREENSHOT_DIR,
            settings.SCRIPT_DIR,
        }
        database_path = _sqlite_database_path(settings.DATABASE_URL)
        if database_path:
            candidates.update(
                {
                    database_path,
                    f"{database_path}-journal",
                    f"{database_path}-wal",
                    f"{database_path}-shm",
                }
            )
        return [_normalize_path(path) for path in candidates if path]

    def start(self, session_id: str, callback) -> None:
        self._session_id = session_id
        self._callback = callback
        self._seq_num = 0
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._handler = _WatchdogHandler(self._loop, self._on_file_event, ignored_paths=self._ignored_paths)
        self._observer = Observer()
        for path in self._watch_paths:
            if os.path.isdir(path):
                self._observer.schedule(self._handler, path, recursive=True)
        if self._observer.emitters:
            self._observer.start()
        else:
            self._observer = None

    def stop(self) -> None:
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        self._observer = None
        self._handler = None

    async def _on_file_event(self, data: FileEventData, op: FileOperation) -> None:
        if not self._callback or not self._running:
            return
        try:
            stat = os.stat(data.src_path) if os.path.exists(data.src_path) else None
            if stat:
                data.size = stat.st_size
        except OSError:
            pass
        context = OperationContext(
            process_name=None,
        )
        event = OperationEvent(
            id=str(uuid.uuid4()),
            session_id=self._session_id,
            seq_num=self._seq_num,
            timestamp=int(time.time() * 1000),
            type=OperationType.FILE_OP,
            data=data,
            context=context,
        )
        self._seq_num += 1
        self._callback(event)
