from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.analyzer.confidence_scorer import ConfidenceScorer
from src.analyzer.pattern_detector import PatternDetector
from src.analyzer.preprocessor import NormalizedOperation as NormalizedOperation
from src.analyzer.preprocessor import OperationPreprocessor
from src.analyzer.script_generator import ScriptGenerator
from src.analyzer.semantic_segmenter import SemanticSegmenter

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    name: str
    timestamp: str = ""
    metrics: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "timestamp": self.timestamp, "metrics": self.metrics, "details": self.details}


class PatternDetectionBenchmark:
    def __init__(self):
        self._preprocessor = OperationPreprocessor()
        self._detector = PatternDetector()
        self._segmenter = SemanticSegmenter()

    def run(self, test_cases: list[dict]) -> BenchmarkResult:
        import datetime
        result = BenchmarkResult(
            name="pattern_detection",
            timestamp=datetime.datetime.utcnow().isoformat(),
        )

        total_cases = len(test_cases)
        detected_count = 0
        segment_count = 0
        total_latency_ms = 0.0

        for case in test_cases:
            operations = case.get("operations", [])
            expected_patterns = case.get("expected_patterns", 0)

            norm_ops = self._preprocessor.preprocess(operations)

            t0 = time.perf_counter()
            patterns = self._detector.detect_patterns(norm_ops)
            segments = self._segmenter.segment(norm_ops)
            latency_ms = (time.perf_counter() - t0) * 1000

            if len(patterns) >= expected_patterns:
                detected_count += 1

            segment_count += len(segments)
            total_latency_ms += latency_ms

        result.metrics = {
            "total_cases": total_cases,
            "detected_count": detected_count,
            "detection_rate": detected_count / max(total_cases, 1),
            "avg_latency_ms": total_latency_ms / max(total_cases, 1),
            "avg_segments_per_case": segment_count / max(total_cases, 1),
        }

        return result


class WorkflowGenerationBenchmark:
    def __init__(self):
        self._preprocessor = OperationPreprocessor()
        self._detector = PatternDetector()
        self._generator = ScriptGenerator()
        self._scorer = ConfidenceScorer()

    def run(self, test_cases: list[dict]) -> BenchmarkResult:
        import datetime
        result = BenchmarkResult(
            name="workflow_generation",
            timestamp=datetime.datetime.utcnow().isoformat(),
        )

        total_cases = len(test_cases)
        valid_flows = 0
        avg_confidence = 0.0
        total_latency_ms = 0.0

        for case in test_cases:
            operations = case.get("operations", [])
            expected_steps = case.get("expected_steps", 0)

            norm_ops = self._preprocessor.preprocess(operations)
            patterns = self._detector.detect_patterns(norm_ops)

            t0 = time.perf_counter()
            flows = self._generator.generate(patterns, norm_ops)
            latency_ms = (time.perf_counter() - t0) * 1000

            if flows:
                flow = flows[0]
                if len(flow.steps) >= expected_steps * 0.8:
                    valid_flows += 1
                avg_confidence += flow.confidence

            total_latency_ms += latency_ms

        result.metrics = {
            "total_cases": total_cases,
            "valid_flows": valid_flows,
            "validity_rate": valid_flows / max(total_cases, 1),
            "avg_confidence": avg_confidence / max(total_cases, 1),
            "avg_latency_ms": total_latency_ms / max(total_cases, 1),
        }

        return result


class GroundingBenchmark:
    def __init__(self):
        pass

    async def run(self, test_cases: list[dict]) -> BenchmarkResult:
        import datetime
        result = BenchmarkResult(
            name="grounding",
            timestamp=datetime.datetime.utcnow().isoformat(),
        )

        from src.llm.gui_grounding import GUIGroundingEngine
        engine = GUIGroundingEngine()

        total_cases = len(test_cases)
        correct_count = 0
        total_confidence = 0.0
        total_latency_ms = 0.0

        for case in test_cases:
            screenshot = case.get("screenshot_base64", "")
            action_desc = case.get("action_description", "")
            action_type = case.get("action_type", "click")
            expected_bbox = case.get("expected_bbox")

            t0 = time.perf_counter()
            grounding_result = await engine.ground_action(
                screenshot_base64=screenshot,
                action_description=action_desc,
                action_type=action_type,
            )
            latency_ms = (time.perf_counter() - t0) * 1000

            if grounding_result.found:
                total_confidence += grounding_result.confidence
                if expected_bbox and grounding_result.bbox_pixel:
                    correct_count += 1

            total_latency_ms += latency_ms

        result.metrics = {
            "total_cases": total_cases,
            "correct_count": correct_count,
            "accuracy": correct_count / max(total_cases, 1),
            "avg_confidence": total_confidence / max(total_cases, 1),
            "avg_latency_ms": total_latency_ms / max(total_cases, 1),
        }

        return result


def save_benchmark_report(results: list[BenchmarkResult], output_path: str) -> None:
    report = {
        "benchmarks": [r.to_dict() for r in results],
        "summary": {},
    }
    for r in results:
        report["summary"][r.name] = r.metrics

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("Benchmark report saved to: %s", output_path)
