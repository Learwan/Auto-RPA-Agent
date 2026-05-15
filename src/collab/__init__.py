from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from src.config import settings
from src.llm.local_engine import LocalLLMEngine

logger = logging.getLogger(__name__)


@dataclass
class Collaborator:
    user_id: str
    username: str
    websocket: WebSocket
    joined_at: float = field(default_factory=time.time)
    is_active: bool = True


@dataclass
class ChatMessage:
    id: str
    room_id: str
    user_id: str
    username: str
    content: str
    role: str = "user"
    timestamp: float = field(default_factory=time.time)


class CollaborationRoom:
    def __init__(self, room_id: str, session_id: str, max_users: int = 10):
        self.room_id = room_id
        self.session_id = session_id
        self.max_users = max_users
        self._users: dict[str, Collaborator] = {}
        self._messages: list[ChatMessage] = []
        self._lock = asyncio.Lock()
        self._engine: LocalLLMEngine | None = None
        self._ai_username = "AI 分析助手"

    async def initialize(self) -> None:
        if settings.LOCAL_LLM_ENABLED:
            try:
                self._engine = await LocalLLMEngine.get_instance()
            except Exception as e:
                logger.warning(f"Local LLM engine not available for collab: {e}")

    @property
    def user_count(self) -> int:
        return len(self._users)

    @property
    def usernames(self) -> list[str]:
        return [u.username for u in self._users.values()]

    async def join(self, user_id: str, username: str, websocket: WebSocket) -> bool:
        async with self._lock:
            if len(self._users) >= self.max_users:
                return False

            if user_id in self._users:
                old = self._users[user_id]
                with contextlib.suppress(Exception):
                    await old.websocket.close(code=4001, reason="duplicate_join")

            collaborator = Collaborator(user_id=user_id, username=username, websocket=websocket)
            self._users[user_id] = collaborator

            join_msg = ChatMessage(
                id=str(uuid.uuid4()),
                room_id=self.room_id,
                user_id="system",
                username="系统",
                content=f"{username} 加入了协作",
                role="system",
            )
            self._messages.append(join_msg)
            await self._broadcast(join_msg)

            await websocket.send_json(
                {
                    "type": "room_joined",
                    "room_id": self.room_id,
                    "session_id": self.session_id,
                    "user_id": user_id,
                    "username": username,
                    "users": self.usernames,
                    "history": self._format_messages(self._messages[-50:]),
                    "ai_enabled": self._engine is not None,
                }
            )

            await self._broadcast_users_update()
            return True

    async def leave(self, user_id: str) -> None:
        async with self._lock:
            if user_id not in self._users:
                return

            user = self._users.pop(user_id)
            leave_msg = ChatMessage(
                id=str(uuid.uuid4()),
                room_id=self.room_id,
                user_id="system",
                username="系统",
                content=f"{user.username} 离开了协作",
                role="system",
            )
            self._messages.append(leave_msg)
            await self._broadcast(leave_msg)
            await self._broadcast_users_update()

    async def handle_message(self, user_id: str, content: str, msg_type: str = "chat") -> None:
        async with self._lock:
            if user_id not in self._users:
                return
            user = self._users[user_id]

            chat_msg = ChatMessage(
                id=str(uuid.uuid4()),
                room_id=self.room_id,
                user_id=user_id,
                username=user.username,
                content=content,
                role="user",
            )

            if msg_type == "ai_query":
                chat_msg.role = "ai_query"
                asyncio.create_task(self._generate_ai_response(content))
            elif msg_type == "command":
                chat_msg.role = "command"
                await self._handle_command(content, user_id)

            self._messages.append(chat_msg)
            await self._broadcast(chat_msg)

    async def _generate_ai_response(self, query: str) -> None:
        if not self._engine:
            error_msg = ChatMessage(
                id=str(uuid.uuid4()),
                room_id=self.room_id,
                user_id="system",
                username=self._ai_username,
                content="AI 助手未启用，请先配置本地 LLM",
                role="ai",
            )
            self._messages.append(error_msg)
            await self._broadcast(error_msg)
            return

        context = self._build_ai_context()
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个录制协作AI助手，正在帮助多个用户分析他们的录制会话。\n"
                    f"当前录制上下文:\n{context}\n"
                    "请基于上下文提供有用的分析、建议或回答问题。使用中文，回答简洁专业。"
                ),
            },
            {"role": "user", "content": query},
        ]

        try:
            stream_msg_id = str(uuid.uuid4())
            response_parts: list[str] = []

            async for token in self._engine.generate_stream(messages, max_tokens=1024):
                response_parts.append(token)
                await self._broadcast(
                    {
                        "type": "ai_stream",
                        "message_id": stream_msg_id,
                        "username": self._ai_username,
                        "token": token,
                        "is_final": False,
                    }
                )

            full_response = "".join(response_parts)
            ai_msg = ChatMessage(
                id=stream_msg_id,
                room_id=self.room_id,
                user_id="ai",
                username=self._ai_username,
                content=full_response,
                role="ai",
            )
            self._messages.append(ai_msg)

            await self._broadcast(
                {
                    "type": "ai_stream",
                    "message_id": stream_msg_id,
                    "username": self._ai_username,
                    "token": "",
                    "is_final": True,
                    "full_response": full_response,
                }
            )

        except Exception as e:
            logger.error(f"AI response generation failed: {e}")
            error_msg = ChatMessage(
                id=str(uuid.uuid4()),
                room_id=self.room_id,
                user_id="system",
                username=self._ai_username,
                content=f"AI 响应生成失败: {str(e)}",
                role="ai",
            )
            self._messages.append(error_msg)
            await self._broadcast(error_msg)

    async def _handle_command(self, content: str, user_id: str) -> None:
        cmd = content.strip().lower()
        responses: dict[str, str] = {
            "/help": "可用命令:\n/help - 帮助\n/users - 查看用户\n/analyze - 触发AI分析\n/summary - 获取会话摘要",
            "/users": f"当前用户 ({len(self._users)}): {', '.join(self.usernames)}",
            "/analyze": "正在触发AI分析...",
            "/summary": "正在生成会话摘要...",
        }

        response_text = responses.get(cmd, f"未知命令: {cmd}，输入 /help 查看帮助")
        system_msg = ChatMessage(
            id=str(uuid.uuid4()),
            room_id=self.room_id,
            user_id="system",
            username="系统",
            content=response_text,
            role="system",
        )
        self._messages.append(system_msg)
        await self._broadcast(system_msg)

        if cmd == "/analyze" and self._engine:
            await self._generate_ai_response("请分析当前的录制会话，包括操作流程、效率评估和改进建议。")

    def _build_ai_context(self) -> str:
        user_list = ", ".join(self.usernames) if self.usernames else "无用户"
        msg_count = len(self._messages)
        return f"会话ID: {self.session_id}\n协作室ID: {self.room_id}\n在线用户: {user_list}\n消息数量: {msg_count}\n"

    async def _broadcast(self, payload: Any) -> None:
        if isinstance(payload, ChatMessage):
            payload = {
                "type": "chat_message",
                "id": payload.id,
                "room_id": payload.room_id,
                "user_id": payload.user_id,
                "username": payload.username,
                "content": payload.content,
                "role": payload.role,
                "timestamp": payload.timestamp,
            }

        dead_ids: list[str] = []
        for user_id, user in self._users.items():
            try:
                await user.websocket.send_json(payload)
            except Exception:
                dead_ids.append(user_id)

        for dead_id in dead_ids:
            await self.leave(dead_id)

    async def _broadcast_users_update(self) -> None:
        await self._broadcast(
            {
                "type": "users_update",
                "users": self.usernames,
                "count": len(self._users),
            }
        )

    @staticmethod
    def _format_messages(messages: list[ChatMessage]) -> list[dict]:
        return [
            {
                "id": m.id,
                "user_id": m.user_id,
                "username": m.username,
                "content": m.content,
                "role": m.role,
                "timestamp": m.timestamp,
            }
            for m in messages
        ]


class CollaborationService:
    _instance: CollaborationService | None = None

    @classmethod
    def get_instance(cls) -> CollaborationService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._rooms: dict[str, CollaborationRoom] = {}
        self._room_lock = asyncio.Lock()

    async def get_or_create_room(self, room_id: str, session_id: str) -> CollaborationRoom:
        async with self._room_lock:
            if room_id not in self._rooms:
                room = CollaborationRoom(
                    room_id=room_id,
                    session_id=session_id,
                    max_users=settings.COLLAB_MAX_USERS_PER_ROOM,
                )
                await room.initialize()
                self._rooms[room_id] = room
            return self._rooms[room_id]

    async def remove_room(self, room_id: str) -> None:
        async with self._room_lock:
            self._rooms.pop(room_id, None)

    async def get_room_status(self, room_id: str) -> dict | None:
        room = self._rooms.get(room_id)
        if not room:
            return None
        return {
            "room_id": room.room_id,
            "session_id": room.session_id,
            "user_count": room.user_count,
            "users": room.usernames,
        }

    async def list_rooms(self) -> list[dict]:
        return [
            {
                "room_id": r.room_id,
                "session_id": r.session_id,
                "user_count": r.user_count,
                "users": r.usernames,
            }
            for r in self._rooms.values()
        ]
