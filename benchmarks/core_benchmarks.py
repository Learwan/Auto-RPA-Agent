from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.analyzer.pattern_detector import PatternDetector
from src.analyzer.preprocessor import NormalizedOperation
from src.analyzer.script_generator import ScriptGenerator


@dataclass
class BenchmarkResult:
    benchmark_name: str
    metric_name: str
    value: float
    baseline: float | None = None
    improvement_pct: float | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "benchmark_name": self.benchmark_name,
            "metric_name": self.metric_name,
            "value": self.value,
            "baseline": self.baseline,
            "improvement_pct": self.improvement_pct,
        }


class PatternDetectionBenchmark:
    def __init__(self):
        self._detector = PatternDetector()
        self._results: list[BenchmarkResult] = []

    def generate_test_operations(
        self, pattern_type: str, count: int = 10, noise_ratio: float = 0.1,
    ) -> list[NormalizedOperation]:
        operations: list[NormalizedOperation] = []
        noise_count = int(count * noise_ratio)

        if pattern_type == "repetitive":
            for i in range(count):
                operations.append(NormalizedOperation(
                    op_type="click",
                    title=f"Button-{i % 3}",
                    app_name="TestApp",
                    timestamp=float(i * 1000),
                    position={"x": 100 + (i % 3) * 50, "y": 200},
                    confidence=0.9,
                ))
        elif pattern_type == "sequential":
            steps = ["click", "type", "click", "wait", "click"]
            for i in range(count):
                operations.append(NormalizedOperation(
                    op_type=steps[i % len(steps)],
                    title=f"Step-{i % len(steps)}",
                    app_name="TestApp",
                    timestamp=float(i * 2000),
                    position={"x": 100, "y": 100 + i * 30},
                    confidence=0.85,
                ))
        elif pattern_type == "form_fill":
            fields = ["Name", "Email", "Phone", "Address", "Submit"]
            for i in range(count):
                operations.append(NormalizedOperation(
                    op_type="type" if i < count - 1 else "click",
                    title=fields[i % len(fields)],
                    app_name="FormApp",
                    timestamp=float(i * 3000),
                    position={"x": 300, "y": 100 + i * 40},
                    confidence=0.88,
                ))

        for i in range(noise_count):
            operations.append(NormalizedOperation(
                op_type="scroll",
                title="Random",
                app_name="OtherApp",
                timestamp=float(count * 1000 + i * 500),
                position={"x": 500, "y": 500},
                confidence=0.5,
            ))

        return operations

    def run(self) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []

        for pattern_type in ["repetitive", "sequential", "form_fill"]:
            for count in [10, 20, 50]:
                ops = self.generate_test_operations(pattern_type, count)
                detected = self._detector.detect(ops)
                capture_rate = len(detected) / max(1, len(ops))

                results.append(BenchmarkResult(
                    benchmark_name="pattern_detection",
                    metric_name=f"capture_rate_{pattern_type}_{count}",
                    value=capture_rate,
                    baseline=0.35,
                    improvement_pct=(capture_rate - 0.35) / 0.35 * 100 if capture_rate > 0 else None,
                ))

        self._results = results
        return results


class WorkflowGenerationBenchmark:
    def __init__(self):
        self._generator = ScriptGenerator()
        self._results: list[BenchmarkResult] = []

    def run(self) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []

        test_scenarios = [
            {"name": "simple_3step", "steps": 3},
            {"name": "medium_10step", "steps": 10},
            {"name": "complex_25step", "steps": 25},
        ]

        for scenario in test_scenarios:
            ops = self._generate_scenario_ops(scenario["steps"])
            flow = self._generator.generate(ops, session_id="bench")
            step_coverage = len(flow.steps) / max(1, scenario["steps"])

            results.append(BenchmarkResult(
                benchmark_name="workflow_generation",
                metric_name=f"step_coverage_{scenario['name']}",
                value=step_coverage,
            ))

            has_error_handling = all(
                s.on_error is not None for s in flow.steps
            )
            results.append(BenchmarkResult(
                benchmark_name="workflow_generation",
                metric_name=f"error_handling_{scenario['name']}",
                value=1.0 if has_error_handling else 0.0,
            ))

        self._results = results
        return results

    def _generate_scenario_ops(self, count: int) -> list[NormalizedOperation]:
        ops = []
        for i in range(count):
            ops.append(NormalizedOperation(
                op_type="click" if i % 3 != 1 else "type",
                title=f"Element-{i}",
                app_name="BenchApp",
                timestamp=float(i * 1500),
                position={"x": 100 + i * 20, "y": 200},
                confidence=0.9,
            ))
        return ops


class GroundingBenchmark:
    def __init__(self):
        self._results: list[BenchmarkResult] = []

    def run(self) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []

        test_cases = [
            {"name": "button_click", "instruction": "Click the Save button", "expected_type": "button"},
            {"name": "text_input", "instruction": "Type in the search field", "expected_type": "input"},
            {"name": "menu_select", "instruction": "Select File menu", "expected_type": "menu"},
            {"name": "checkbox_toggle", "instruction": "Check the agree checkbox", "expected_type": "checkbox"},
            {"name": "link_click", "instruction": "Click the Next page link", "expected_type": "link"},
        ]

        results.append(BenchmarkResult(
            benchmark_name="grounding",
            metric_name="screenspotpro_accuracy",
            value=0.0,
            baseline=0.55,
            note="Requires UI-TARS model for actual evaluation",
        ))

        results.append(BenchmarkResult(
            benchmark_name="grounding",
            metric_name="test_case_count",
            value=float(len(test_cases)),
        ))

        self._results = results
        return results


def run_all_benchmarks(output_path: str | None = None) -> dict:
    all_results: list[BenchmarkResult] = []

    pd_bench = PatternDetectionBenchmark()
    all_results.extend(pd_bench.run())

    wg_bench = WorkflowGenerationBenchmark()
    all_results.extend(wg_bench.run())

    g_bench = GroundingBenchmark()
    all_results.extend(g_bench.run())

    summary = {
        "total_benchmarks": len(all_results),
        "results": [r.to_dict() for r in all_results],
        "summary": {
            "pattern_detection_avg_capture": _avg_metric(all_results, "pattern_detection"),
            "workflow_generation_avg_coverage": _avg_metric(all_results, "workflow_generation"),
        },
    }

    if output_path:
        Path(output_path).write_text(json.dumps(summary, indent=2, default=str))

    return summary


def _avg_metric(results: list[BenchmarkResult], benchmark_name: str) -> float:
    matching = [
        r.value for r in results
        if r.benchmark_name == benchmark_name
        and ("capture" in r.metric_name or "coverage" in r.metric_name)
    ]
    if not matching:
        return 0.0
    return sum(matching) / len(matching)
