from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RecordingStatus(str, Enum):
    PENDING = "pending"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecordingSession(BaseModel):
    session_id: str = ""
    name: str = ""
    status: RecordingStatus = RecordingStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class RecordingGroup(BaseModel):
    group_id: str = ""
    name: str = ""
    sessions: list[RecordingSession] = Field(default_factory=list)
    min_recordings: int = 2
    status: str = "collecting"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.sessions if s.status == RecordingStatus.COMPLETED)

    @property
    def is_ready_for_fusion(self) -> bool:
        return self.completed_count >= self.min_recordings

    def add_session(self, session: RecordingSession) -> None:
        if not any(s.session_id == session.session_id for s in self.sessions):
            self.sessions.append(session)

    def remove_session(self, session_id: str) -> None:
        self.sessions = [s for s in self.sessions if s.session_id != session_id]


class FusedBranchModel(BaseModel):
    branch_id: str = ""
    condition: str = ""
    trace_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    steps: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class LoopPatternModel(BaseModel):
    pattern_id: str = ""
    sequence: str = ""
    occurrences: int = 0
    positions: list[int] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class FusionResult(BaseModel):
    result_id: str = ""
    group_id: str = ""
    common_steps: list[dict[str, Any]] = Field(default_factory=list)
    branches: list[FusedBranchModel] = Field(default_factory=list)
    loop_patterns: list[LoopPatternModel] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    @property
    def total_divergences(self) -> int:
        return len(self.branches)

    @property
    def common_step_ratio(self) -> float:
        branch_steps = sum(len(b.steps) for b in self.branches)
        total = len(self.common_steps) + branch_steps
        if total == 0:
            return 0.0
        return len(self.common_steps) / total
