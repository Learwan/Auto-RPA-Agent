from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SessionStatus(str, Enum):
    CREATED = "created"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"


class Session(BaseModel):
    """Recording session metadata persisted to the database."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    status: SessionStatus = SessionStatus.CREATED
    started_at: float | None = None
    stopped_at: float | None = None
    duration_ms: int = 0
    operation_count: int = 0
