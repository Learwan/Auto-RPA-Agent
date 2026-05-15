from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    CREATED = "created"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"
    ANALYZING = "analyzing"


class Session(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    status: SessionStatus = SessionStatus.CREATED
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    operation_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
