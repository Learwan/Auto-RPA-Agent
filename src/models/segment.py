from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SegmentBoundary(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class SegmentFeature(BaseModel):
    dominant_op_type: str = ""
    type_uniformity: float = 0.0
    window_titles: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class SemanticSegmentModel(BaseModel):
    segment_id: str = ""
    session_id: str = ""
    start_idx: int = 0
    end_idx: int = 0
    label: str = ""
    confidence: float = 0.0
    boundary_type: SegmentBoundary = SegmentBoundary.HARD
    features: SegmentFeature = Field(default_factory=SegmentFeature)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    @property
    def length(self) -> int:
        return self.end_idx - self.start_idx + 1

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
        }


class SegmentResult(BaseModel):
    session_id: str = ""
    segments: list[SemanticSegmentModel] = Field(default_factory=list)
    total_operations: int = 0
    avg_confidence: float = 0.0
    boundary_count: int = 0

    model_config = {"extra": "allow"}

    def compute_stats(self) -> None:
        if not self.segments:
            self.avg_confidence = 0.0
            self.boundary_count = 0
            return
        self.avg_confidence = sum(s.confidence for s in self.segments) / len(self.segments)
        self.boundary_count = max(0, len(self.segments) - 1)
