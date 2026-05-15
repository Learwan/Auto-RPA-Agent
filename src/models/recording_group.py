from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RecordingStatus(str, Enum):
    PENDING = "pending"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"


class RecordingSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    name: str | None = None
    status: RecordingStatus = RecordingStatus.PENDING
    operation_count: int = 0
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class FusedBranchModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    branch_id: str = Field(default_factory=lambda: uuid4().hex)
    condition: str = ""
    trace_ids: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoopPatternModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pattern_id: str = Field(default_factory=lambda: uuid4().hex)
    sequence: str = ""
    occurrences: int = 0
    positions: list[int] = Field(default_factory=list)
    confidence: float = 0.0


class RecordingGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    group_id: str
    name: str = ""
    description: str = ""
    sessions: list[RecordingSession] = Field(default_factory=list)
    min_recordings: int = 2
    status: str = "collecting"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.sessions if s.status == RecordingStatus.COMPLETED)

    @property
    def is_ready_for_fusion(self) -> bool:
        return self.completed_count >= self.min_recordings

    def add_session(self, session: RecordingSession) -> None:
        if any(existing.session_id == session.session_id for existing in self.sessions):
            return
        self.sessions.append(session)

    def remove_session(self, session_id: str) -> None:
        self.sessions = [s for s in self.sessions if s.session_id != session_id]


class FusionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result_id: str
    group_id: str
    common_steps: list[dict[str, Any]] = Field(default_factory=list)
    branches: list[FusedBranchModel] = Field(default_factory=list)
    loop_patterns: list[LoopPatternModel] = Field(default_factory=list)
    confidence: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def total_divergences(self) -> int:
        return len(self.branches)

    @property
    def common_step_ratio(self) -> float:
        branch_steps = sum(len(branch.steps) for branch in self.branches)
        total = len(self.common_steps) + branch_steps
        if total == 0:
            return 0.0
        return len(self.common_steps) / total
