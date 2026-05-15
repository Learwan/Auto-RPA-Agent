from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from src.analyzer.confidence_scorer import ConfidenceScorer
from src.analyzer.pattern_detector import PatternDetector
from src.analyzer.preprocessor import NormalizedOperation
from src.analyzer.script_generator import ScriptGenerator
from src.analyzer.semantic_segmenter import SemanticSegmenter

logger = logging.getLogger(__name__)

WINDOW_SIZE_SECONDS = 30
MIN_OPERATIONS_FOR_ANALYSIS = 3
INCREMENTAL_DELAY_MS = 300


class AnalysisState(StrEnum):
    IDLE = "idle"
    ANALYZING = "analyzing"
    READY = "ready"


@dataclass
class RealtimeAnalysisResult:
    session_id: str
    timestamp: float
    segments_count: int = 0
    patterns_count: int = 0
    candidate_flows: list[dict] = field(default_factory=list)
    state: AnalysisState = AnalysisState.IDLE
    latency_ms: float = 0.0


class RealtimeAnalyzer:
    def __init__(self, window_size_seconds: int = WINDOW_SIZE_SECONDS):
        self._window_size_seconds = window_size_seconds
        self._segmenter = SemanticSegmenter()
        self._pattern_detector = PatternDetector()
        self._scorer = ConfidenceScorer()
        self._generator = ScriptGenerator()
        self._operation_buffer: list[NormalizedOperation] = []
        self._last_analysis_time: float = 0.0
        self._state = AnalysisState.IDLE
        self._session_id: str = ""

    def add_operation(self, operation: NormalizedOperation) -> None:
        self._operation_buffer.append(operation)
        cutoff_time = operation.timestamp - self._window_size_seconds * 1000
        self._operation_buffer = [op for op in self._operation_buffer if op.timestamp >= cutoff_time]

    async def analyze_incremental(self, session_id: str) -> RealtimeAnalysisResult | None:
        if len(self._operation_buffer) < MIN_OPERATIONS_FOR_ANALYSIS:
            return None

        now = time.time()
        if now - self._last_analysis_time < INCREMENTAL_DELAY_MS / 1000.0:
            return None

        self._state = AnalysisState.ANALYZING
        self._session_id = session_id
        t0 = time.perf_counter()

        try:
            segments = self._segmenter.segment(self._operation_buffer)
            patterns = self._pattern_detector.detect_patterns(self._operation_buffer)

            candidate_flows = []
            if patterns:
                flows = self._generator.generate(patterns, self._operation_buffer)
                for flow in flows[:3]:
                    candidate_flows.append(
                        {
                            "id": flow.id,
                            "name": flow.name,
                            "steps_count": len(flow.steps),
                            "confidence": flow.confidence,
                        }
                    )

            latency_ms = (time.perf_counter() - t0) * 1000
            self._last_analysis_time = now
            self._state = AnalysisState.READY

            return RealtimeAnalysisResult(
                session_id=session_id,
                timestamp=now,
                segments_count=len(segments),
                patterns_count=len(patterns),
                candidate_flows=candidate_flows,
                state=AnalysisState.READY,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.error("Realtime analysis failed: %s", e)
            self._state = AnalysisState.IDLE
            return None

    def reset(self) -> None:
        self._operation_buffer.clear()
        self._last_analysis_time = 0.0
        self._state = AnalysisState.IDLE

    @property
    def state(self) -> AnalysisState:
        return self._state

    @property
    def buffer_size(self) -> int:
        return len(self._operation_buffer)
