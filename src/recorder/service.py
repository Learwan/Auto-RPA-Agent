import asyncio
from collections.abc import AsyncGenerator

from src.models.operation import OperationEvent
from src.models.session import Session
from src.recorder.session_manager import SessionManager


class RecordingService:
    _instance = None

    @classmethod
    def get_instance(cls) -> "RecordingService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._manager = SessionManager()

    async def create_session(self, name: str, description: str = "", tags: list[str] | None = None) -> Session:
        return await self._manager.create_session(name, description, tags)

    async def start_session(
        self,
        session_id: str,
        mode: str = "desktop",
        options: dict | None = None,
    ) -> Session:
        if mode == "desktop" and not options:
            return await self._manager.start_session(session_id)
        return await self._manager.start_session(session_id, mode, options)

    async def pause_session(self, session_id: str) -> Session:
        return await self._manager.pause_session(session_id)

    async def resume_session(self, session_id: str) -> Session:
        return await self._manager.resume_session(session_id)

    async def stop_session(self, session_id: str) -> Session:
        return await self._manager.stop_session(session_id)

    async def toggle_hud_interaction(self, session_id: str) -> bool:
        return await self._manager.toggle_hud_interaction(session_id)

    async def capture_focus_context(
        self,
        session_id: str,
        *,
        include_vision: bool = True,
        include_ocr: bool = True,
        include_llm: bool = True,
    ) -> dict:
        return await self._manager.capture_focus_context(
            session_id,
            include_vision=include_vision,
            include_ocr=include_ocr,
            include_llm=include_llm,
        )

    async def get_session(self, session_id: str) -> Session | None:
        return await self._manager.get_session(session_id)

    async def list_sessions(self, limit: int = 50, offset: int = 0) -> list[Session]:
        return await self._manager.list_sessions(limit, offset)

    async def delete_session(self, session_id: str) -> None:
        await self._manager.delete_session(session_id)

    async def get_operations(self, session_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        from src.db.database import get_session_factory
        from src.db.repository import Repository

        factory = get_session_factory()
        async with factory() as db_session:
            repo = Repository(db_session)
            operations = await repo.get_operations_by_session(session_id)
            sliced = operations[offset : offset + limit]
            return [event.model_dump(mode="json") for event in sliced]

    async def subscribe_events(self, session_id: str) -> AsyncGenerator[OperationEvent, None]:
        queue = self._manager.subscribe_events(session_id)
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield event
        except TimeoutError:
            yield None
        finally:
            self._manager.unsubscribe_events(session_id, queue)
