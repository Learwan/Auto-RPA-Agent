from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SegmentBoundary(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class SegmentFeature(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dominant_op_type: str = ""
    type_uniformity: float = 0.0
    window_titles: list[str] = Field(default_factory=list)
    process_names: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    op_count: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


class SemanticSegmentModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    segment_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str | None = None
    start_idx: int = 0
    end_idx: int = 0
    label: str | None = None
    confidence: float = 0.0
    boundary_type: SegmentBoundary = SegmentBoundary.HARD
    features: SegmentFeature = Field(default_factory=SegmentFeature)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def length(self) -> int:
        return max(0, self.end_idx - self.start_idx + 1)

    def to_segment_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "session_id": self.session_id,
            "start_idx": self.start_idx,
            "end_idx": self.end_idx,
            "label": self.label,
            "confidence": self.confidence,
            "boundary_type": self.boundary_type.value,
            "length": self.length,
            "features": self.features.model_dump(),
        }


class SegmentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    segments: list[SemanticSegmentModel] = Field(default_factory=list)
    total_operations: int = 0
    avg_confidence: float = 0.0
    boundary_count: int = 0

    def compute_stats(self) -> "SegmentResult":
        if not self.segments:
            self.avg_confidence = 0.0
            self.boundary_count = 0
            return self
        self.avg_confidence = sum(s.confidence for s in self.segments) / len(self.segments)
        self.boundary_count = sum(1 for s in self.segments if s.boundary_type == SegmentBoundary.HARD)
        return self
