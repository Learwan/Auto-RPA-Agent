import math

from pydantic import BaseModel

from src.analyzer.preprocessor import NormalizedOperation
from src.models.automation import AutomationFlow, DetectedPattern, LocateStrategy, StepType

STABILITY_WEIGHT = 0.3
LOCATOR_WEIGHT = 0.3
CONTEXT_WEIGHT = 0.2
ANOMALY_WEIGHT = 0.2

LOCATOR_RELIABILITY = {
    LocateStrategy.ACCESSIBILITY_ID: 0.95,
    LocateStrategy.CSS_SELECTOR: 0.90,
    LocateStrategy.XPATH: 0.85,
    LocateStrategy.TEXT_MATCH: 0.80,
    LocateStrategy.IMAGE_ANCHOR: 0.75,
    LocateStrategy.IMAGE_MATCH: 0.65,
    LocateStrategy.OCR: 0.60,
    LocateStrategy.COORDINATE: 0.45,
    LocateStrategy.POSITION: 0.40,
}

POSITION_VARIANCE_THRESHOLD = 50.0
TIME_VARIANCE_THRESHOLD = 3000.0
MIN_FLOW_STEPS = 3
MAX_FLOW_STEPS = 15


class DimensionScore(BaseModel):
    name: str
    score: float
    details: str


class ImprovementSuggestion(BaseModel):
    step_index: int | None = None
    category: str
    message: str
    impact: str = "medium"


class ScoredFlow(BaseModel):
    flow: AutomationFlow
    overall_confidence: float
    dimensions: list[DimensionScore] = []
    suggestions: list[ImprovementSuggestion] = []
    is_reliable: bool = False


class ConfidenceScorer:
    def score_flow(
        self,
        flow: AutomationFlow,
        operations: list[NormalizedOperation],
        pattern: DetectedPattern,
    ) -> ScoredFlow:
        stability = self._score_stability(pattern, operations)
        locator = self._score_locator_reliability(flow)
        context = self._score_context_dependency(flow)
        anomaly = self._score_anomaly(flow)

        overall = (
            stability.score * STABILITY_WEIGHT
            + locator.score * LOCATOR_WEIGHT
            + context.score * CONTEXT_WEIGHT
            + anomaly.score * ANOMALY_WEIGHT
        )
        overall = min(max(overall, 0.0), 1.0)

        suggestions = self._generate_suggestions(flow, stability, locator, context, anomaly)

        flow.confidence = overall

        return ScoredFlow(
            flow=flow,
            overall_confidence=overall,
            dimensions=[stability, locator, context, anomaly],
            suggestions=suggestions,
            is_reliable=overall >= 0.7,
        )

    def _score_stability(self, pattern: DetectedPattern, operations: list[NormalizedOperation]) -> DimensionScore:
        if len(pattern.instances) < 2:
            return DimensionScore(name="stability", score=0.5, details="Only one instance, cannot measure stability")

        positions_by_step: dict[int, list[tuple[float, float]]] = {}
        times_by_step: dict[int, list[int]] = {}

        for instance in pattern.instances:
            start_idx = instance["start_idx"]
            end_idx = instance["end_idx"]
            for step_offset, op_idx in enumerate(range(start_idx, min(end_idx + 1, len(operations)))):
                op = operations[op_idx]
                x = op.data.get("x")
                y = op.data.get("y")
                if x is not None and y is not None:
                    positions_by_step.setdefault(step_offset, []).append((float(x), float(y)))
                times_by_step.setdefault(step_offset, []).append(op.timestamp)

        position_scores: list[float] = []
        for _step_idx, positions in positions_by_step.items():
            if len(positions) < 2:
                continue
            avg_x = sum(p[0] for p in positions) / len(positions)
            avg_y = sum(p[1] for p in positions) / len(positions)
            variance = sum((p[0] - avg_x) ** 2 + (p[1] - avg_y) ** 2 for p in positions) / len(positions)
            std_dev = math.sqrt(variance)
            score = max(0.0, 1.0 - std_dev / POSITION_VARIANCE_THRESHOLD)
            position_scores.append(score)

        time_scores: list[float] = []
        for _step_idx, timestamps in times_by_step.items():
            if len(timestamps) < 2:
                continue
            intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
            if not intervals:
                continue
            avg_interval = sum(intervals) / len(intervals)
            variance = sum((iv - avg_interval) ** 2 for iv in intervals) / len(intervals)
            std_dev = math.sqrt(variance)
            score = max(0.0, 1.0 - std_dev / TIME_VARIANCE_THRESHOLD)
            time_scores.append(score)

        pos_avg = sum(position_scores) / len(position_scores) if position_scores else 0.7
        time_avg = sum(time_scores) / len(time_scores) if time_scores else 0.7
        combined = pos_avg * 0.6 + time_avg * 0.4

        details = f"Position stability: {pos_avg:.2f}, Time stability: {time_avg:.2f}"
        return DimensionScore(name="stability", score=combined, details=details)

    def _score_locator_reliability(self, flow: AutomationFlow) -> DimensionScore:
        if not flow.steps:
            return DimensionScore(name="locator_reliability", score=0.0, details="No steps in flow")

        step_scores: list[float] = []
        for step in flow.steps:
            base_score = LOCATOR_RELIABILITY.get(step.target.strategy, 0.3)
            bonus = 0.0
            if step.target.window_title:
                bonus += 0.05
            if step.target.role:
                bonus += 0.05
            if step.target.class_name:
                bonus += 0.03
            if step.target.strategy == LocateStrategy.POSITION and step.target.position:
                bonus -= 0.1
            step_scores.append(min(base_score + bonus, 1.0))

        avg_score = sum(step_scores) / len(step_scores)
        position_only_count = sum(1 for s in flow.steps if s.target.strategy == LocateStrategy.POSITION)
        position_ratio = position_only_count / len(flow.steps) if flow.steps else 0
        if position_ratio > 0.8:
            avg_score *= 0.7

        details = f"Average locator score: {avg_score:.2f}, Position-only ratio: {position_ratio:.1%}"
        return DimensionScore(name="locator_reliability", score=avg_score, details=details)

    def _score_context_dependency(self, flow: AutomationFlow) -> DimensionScore:
        score = 0.5
        details_parts: list[str] = []

        app_names: set[str] = set()
        for step in flow.steps:
            if step.target.window_title:
                app_names.add(step.target.window_title)

        if len(app_names) <= 1:
            score += 0.2
            details_parts.append("Single app context (+0.2)")
        elif len(app_names) <= 3:
            score += 0.1
            details_parts.append("Limited app switching (+0.1)")
        else:
            score -= 0.1
            details_parts.append("Many app switches (-0.1)")

        has_window_switch = any(s.type == StepType.SWITCH_WINDOW for s in flow.steps)
        if has_window_switch:
            score += 0.1
            details_parts.append("Has window switch verification (+0.1)")

        has_condition = any(s.condition is not None for s in flow.steps)
        if has_condition:
            score += 0.1
            details_parts.append("Has condition checks (+0.1)")

        has_wait = any(s.type == StepType.WAIT for s in flow.steps)
        if has_wait:
            score += 0.05
            details_parts.append("Has wait steps (+0.05)")

        score = min(max(score, 0.0), 1.0)
        details = "; ".join(details_parts) if details_parts else "Basic context"
        return DimensionScore(name="context_dependency", score=score, details=details)

    def _score_anomaly(self, flow: AutomationFlow) -> DimensionScore:
        score = 1.0
        details_parts: list[str] = []
        step_count = len(flow.steps)

        if step_count < MIN_FLOW_STEPS:
            score -= 0.3
            details_parts.append(f"Too few steps ({step_count} < {MIN_FLOW_STEPS}, -0.3)")
        elif step_count > MAX_FLOW_STEPS:
            score -= 0.1
            details_parts.append(f"Many steps ({step_count} > {MAX_FLOW_STEPS}, -0.1)")

        has_wait = any(s.type == StepType.WAIT for s in flow.steps)
        if not has_wait and step_count > 5:
            score -= 0.15
            details_parts.append("No wait steps in long flow (-0.15)")

        all_position = all(s.target.strategy == LocateStrategy.POSITION for s in flow.steps)
        if all_position and step_count > 3:
            score -= 0.2
            details_parts.append("All steps use position-only targeting (-0.2)")

        type_steps = [s for s in flow.steps if s.type == StepType.TYPE]
        for step in type_steps:
            text = step.action.get("text", "")
            if text and len(text) > 50:
                score -= 0.05
                details_parts.append("Very long text input at step (-0.05)")
                break

        score = min(max(score, 0.0), 1.0)
        details = "; ".join(details_parts) if details_parts else "No anomalies detected"
        return DimensionScore(name="anomaly_detection", score=score, details=details)

    def _generate_suggestions(
        self,
        flow: AutomationFlow,
        stability: DimensionScore,
        locator: DimensionScore,
        context: DimensionScore,
        anomaly: DimensionScore,
    ) -> list[ImprovementSuggestion]:
        suggestions: list[ImprovementSuggestion] = []

        if stability.score < 0.6:
            suggestions.append(
                ImprovementSuggestion(
                    category="stability",
                    message=(
                        "Operation positions vary significantly across instances. "
                        "Consider adding accessibility identifiers for more reliable targeting."
                    ),
                    impact="high",
                )
            )

        if locator.score < 0.6:
            position_steps = [
                i for i, s in enumerate(flow.steps)
                if s.target.strategy == LocateStrategy.POSITION
            ]
            if position_steps:
                suggestions.append(
                    ImprovementSuggestion(
                        step_index=position_steps[0],
                        category="locator",
                        message=(
                            f"{len(position_steps)} step(s) use position-only targeting "
                            "which is resolution-dependent. Add text or accessibility identifiers."
                        ),
                        impact="high",
                    )
                )

        if context.score < 0.5:
            suggestions.append(
                ImprovementSuggestion(
                    category="context",
                    message="Flow depends on multiple applications. Add window verification steps before interactions.",
                    impact="medium",
                )
            )

        if anomaly.score < 0.7:
            if len(flow.steps) < MIN_FLOW_STEPS:
                suggestions.append(
                    ImprovementSuggestion(
                        category="anomaly",
                        message="Flow is very short and may not represent a meaningful automation pattern.",
                        impact="low",
                    )
                )
            if not any(s.type == StepType.WAIT for s in flow.steps) and len(flow.steps) > 5:
                suggestions.append(
                    ImprovementSuggestion(
                        category="anomaly",
                        message="Add wait steps between operations to handle varying response times.",
                        impact="medium",
                    )
                )

        return suggestions
