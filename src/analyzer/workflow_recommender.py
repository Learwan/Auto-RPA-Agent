from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from src.models.automation import AutomationFlow, ErrorAction, LocateStrategy, StepType

logger = logging.getLogger(__name__)


class RecommendationType(StrEnum):
    ADD_WAIT = "add_wait"
    CHANGE_STRATEGY = "change_strategy"
    ADD_RETRY = "add_retry"
    ADD_PRECONDITION = "add_precondition"
    REMOVE_UNSTABLE = "remove_unstable"
    MERGE_STEPS = "merge_steps"
    ADD_FALLBACK = "add_fallback"
    OPTIMIZE_DELAY = "optimize_delay"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Recommendation:
    rec_type: RecommendationType
    severity: Severity
    step_index: int | None
    step_id: str | None
    description: str
    rationale: str
    suggested_change: dict = field(default_factory=dict)
    estimated_impact: float = 0.0

    def to_dict(self) -> dict:
        return {
            "rec_type": self.rec_type.value,
            "severity": self.severity.value,
            "step_index": self.step_index,
            "step_id": self.step_id,
            "description": self.description,
            "rationale": self.rationale,
            "suggested_change": self.suggested_change,
            "estimated_impact": round(self.estimated_impact, 3),
        }


class ExecutionRecord:
    def __init__(
        self,
        flow_id: str,
        step_results: list[dict],
        total_duration_ms: float,
        success_rate: float,
        timestamp: float | None = None,
    ):
        self.flow_id = flow_id
        self.step_results = step_results
        self.total_duration_ms = total_duration_ms
        self.success_rate = success_rate
        self.timestamp = timestamp or time.time()


class WorkflowRecommender:
    def __init__(self, max_history: int = 100):
        self._history: list[ExecutionRecord] = []
        self._max_history = max_history

    def record_execution(self, record: ExecutionRecord) -> None:
        self._history.append(record)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def analyze_flow(self, flow: AutomationFlow) -> list[Recommendation]:
        recommendations: list[Recommendation] = []

        recommendations.extend(self._analyze_position_strategy(flow))
        recommendations.extend(self._analyze_retry_config(flow))
        recommendations.extend(self._analyze_missing_waits(flow))
        recommendations.extend(self._analyze_error_handling(flow))
        recommendations.extend(self._analyze_historical_patterns(flow))

        severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        recommendations.sort(
            key=lambda r: severity_order.get(r.severity.value, 0),
            reverse=True,
        )

        return recommendations

    def get_flow_health_score(self, flow: AutomationFlow) -> dict:
        recommendations = self.analyze_flow(flow)
        if not recommendations:
            return {"score": 1.0, "level": "EXCELLENT", "issues": 0}

        critical = sum(1 for r in recommendations if r.severity == Severity.CRITICAL)
        high = sum(1 for r in recommendations if r.severity == Severity.HIGH)
        medium = sum(1 for r in recommendations if r.severity == Severity.MEDIUM)
        low = sum(1 for r in recommendations if r.severity == Severity.LOW)

        penalty = critical * 0.3 + high * 0.15 + medium * 0.05 + low * 0.02
        score = max(0.0, 1.0 - penalty)

        if score >= 0.9:
            level = "EXCELLENT"
        elif score >= 0.7:
            level = "GOOD"
        elif score >= 0.5:
            level = "FAIR"
        else:
            level = "POOR"

        return {
            "score": round(score, 3),
            "level": level,
            "issues": len(recommendations),
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
        }

    def _analyze_position_strategy(self, flow: AutomationFlow) -> list[Recommendation]:
        recommendations = []
        for i, step in enumerate(flow.steps):
            if step.type in (StepType.WAIT, StepType.CONDITION, StepType.LOOP):
                continue
            target = step.target
            if target is None:
                continue
            if target.strategy == LocateStrategy.POSITION and not (
                target.selector or target.xpath or target.accessibility_id or target.title
            ):
                recommendations.append(Recommendation(
                    rec_type=RecommendationType.CHANGE_STRATEGY,
                    severity=Severity.HIGH,
                    step_index=i,
                    step_id=step.id,
                    description=f"步骤 {i} 使用纯坐标定位，建议添加语义定位器",
                    rationale="纯坐标定位在窗口位置变化时会失败，添加 selector/xpath/title 可大幅提高稳定性",
                    suggested_change={
                        "current_strategy": "position",
                        "recommended_strategy": "css_selector or accessibility_id",
                        "add_selector": True,
                    },
                    estimated_impact=0.3,
                ))
        return recommendations

    def _analyze_retry_config(self, flow: AutomationFlow) -> list[Recommendation]:
        recommendations = []
        for i, step in enumerate(flow.steps):
            if step.type in (StepType.WAIT, StepType.CONDITION, StepType.LOOP):
                continue
            if (
                step.on_error == ErrorAction.ABORT
                and step.retry_count == 0
                and step.type in (StepType.CLICK, StepType.TYPE, StepType.NAVIGATE)
            ):
                    recommendations.append(Recommendation(
                        rec_type=RecommendationType.ADD_RETRY,
                        severity=Severity.MEDIUM,
                        step_index=i,
                        step_id=step.id,
                        description=f"步骤 {i} ({step.type.value}) 无重试配置且错误时直接中止",
                        rationale=f"{step.type.value} 操作常因瞬态问题失败，建议至少重试1-2次",
                        suggested_change={
                            "retry_count": 2,
                            "on_error": "retry",
                        },
                        estimated_impact=0.2,
                    ))
        return recommendations

    def _analyze_missing_waits(self, flow: AutomationFlow) -> list[Recommendation]:
        recommendations = []
        for i, step in enumerate(flow.steps):
            if i == 0:
                continue
            prev = flow.steps[i - 1]
            if (
                prev.type == StepType.NAVIGATE
                and step.type not in (StepType.WAIT, StepType.CONDITION)
                and step.delay < 500
            ):
                    recommendations.append(Recommendation(
                        rec_type=RecommendationType.ADD_WAIT,
                        severity=Severity.HIGH,
                        step_index=i,
                        step_id=step.id,
                        description=f"步骤 {i} 在导航后无等待，建议增加等待时间",
                        rationale="页面导航后需要时间加载，立即操作可能导致元素未找到",
                        suggested_change={
                            "add_wait_before": True,
                            "wait_type": "element_exists",
                            "min_delay_ms": 1000,
                        },
                        estimated_impact=0.25,
                    ))

            if (
                prev.type == StepType.SWITCH_WINDOW
                and step.type not in (StepType.WAIT, StepType.CONDITION)
                and step.delay < 300
            ):
                    recommendations.append(Recommendation(
                        rec_type=RecommendationType.ADD_WAIT,
                        severity=Severity.MEDIUM,
                        step_index=i,
                        step_id=step.id,
                        description=f"步骤 {i} 在窗口切换后无等待",
                        rationale="窗口切换后需要时间激活目标窗口",
                        suggested_change={
                            "add_wait_before": True,
                            "min_delay_ms": 500,
                        },
                        estimated_impact=0.15,
                    ))
        return recommendations

    def _analyze_error_handling(self, flow: AutomationFlow) -> list[Recommendation]:
        recommendations = []
        has_fallback = any(
            s.on_error == ErrorAction.FALLBACK or s.on_error == ErrorAction.RELOCATE
            for s in flow.steps
        )
        if not has_fallback and len(flow.steps) > 3:
            recommendations.append(Recommendation(
                rec_type=RecommendationType.ADD_FALLBACK,
                severity=Severity.MEDIUM,
                step_index=None,
                step_id=None,
                description="工作流无任何回退/重定位策略",
                rationale="长流程中至少应对关键步骤设置 RELOCATE 或 FALLBACK 策略，避免单点失败导致整体中止",
                suggested_change={
                    "apply_to_steps": "all interactive steps",
                    "recommended_on_error": "relocate",
                },
                estimated_impact=0.2,
            ))
        return recommendations

    def _analyze_historical_patterns(self, flow: AutomationFlow) -> list[Recommendation]:
        recommendations = []
        if not self._history:
            return recommendations

        matching_records = [r for r in self._history if r.flow_id == flow.id]
        if not matching_records:
            similar_records = [r for r in self._history if len(r.step_results) == len(flow.steps)]
            if not similar_records:
                return recommendations
            matching_records = similar_records[-5:]

        step_failure_rates: dict[int, float] = {}
        for record in matching_records:
            for sr in record.step_results:
                idx = sr.get("step_index", 0)
                if not sr.get("success", True):
                    step_failure_rates[idx] = step_failure_rates.get(idx, 0) + 1

        total_runs = len(matching_records)
        for idx, failures in step_failure_rates.items():
            failure_rate = failures / total_runs
            if failure_rate >= 0.5 and idx < len(flow.steps):
                step = flow.steps[idx]
                recommendations.append(Recommendation(
                    rec_type=RecommendationType.ADD_PRECONDITION,
                    severity=Severity.CRITICAL,
                    step_index=idx,
                    step_id=step.id,
                    description=f"步骤 {idx} ({step.type.value}) 历史失败率 {failure_rate:.0%}",
                    rationale=f"该步骤在最近 {total_runs} 次执行中失败了 {failures} 次，建议添加前置条件或增加重试",
                    suggested_change={
                        "add_precondition": "element_exists",
                        "increase_retry": True,
                        "failure_rate": round(failure_rate, 3),
                    },
                    estimated_impact=0.4,
                ))

        avg_duration = sum(r.total_duration_ms for r in matching_records) / len(matching_records)
        if avg_duration > 60000:
            slow_steps = []
            for record in matching_records:
                for sr in record.step_results:
                    duration = sr.get("duration_ms", 0)
                    if duration > 5000:
                        slow_steps.append(sr.get("step_index", 0))
            if slow_steps:
                from collections import Counter

                step_counts = Counter(slow_steps)
                slowest_idx, count = step_counts.most_common(1)[0]
                if slowest_idx < len(flow.steps):
                    recommendations.append(Recommendation(
                        rec_type=RecommendationType.OPTIMIZE_DELAY,
                        severity=Severity.LOW,
                        step_index=slowest_idx,
                        step_id=flow.steps[slowest_idx].id,
                        description=f"步骤 {slowest_idx} 平均耗时过长",
                        rationale=f"该步骤在 {count}/{total_runs} 次执行中耗时超过5秒，建议优化等待策略",
                        suggested_change={
                            "optimize_wait": True,
                            "replace_fixed_wait": "condition_wait",
                        },
                        estimated_impact=0.1,
                    ))

        return recommendations

    def get_stats(self) -> dict:
        if not self._history:
            return {"total_executions": 0}
        return {
            "total_executions": len(self._history),
            "avg_success_rate": round(sum(r.success_rate for r in self._history) / len(self._history), 3),
            "avg_duration_ms": round(sum(r.total_duration_ms for r in self._history) / len(self._history), 1),
        }
