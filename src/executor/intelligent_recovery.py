from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

HEALING_HISTORY_MAX = 500
ROOT_CAUSE_CONFIDENCE_THRESHOLD = 0.6


class RootCauseCategory(StrEnum):
    SELECTOR_STALE = "selector_stale"
    ELEMENT_NOT_LOADED = "element_not_loaded"
    ELEMENT_OBSCURED = "element_obscured"
    TIMING_ISSUE = "timing_issue"
    STATE_MISMATCH = "state_mismatch"
    PERMISSION_DENIED = "permission_denied"
    NETWORK_ERROR = "network_error"
    DATA_VALIDATION = "data_validation"
    CONCURRENT_CONFLICT = "concurrent_conflict"
    UNKNOWN = "unknown"


class HealingLevel(StrEnum):
    L0_INSTANT_RETRY = "l0_instant_retry"
    L1_PARAM_ADJUST = "l1_param_adjust"
    L2_PATH_REPLACE = "l2_path_replace"
    L3_SEMANTIC_FIX = "l3_semantic_fix"
    L4_HUMAN_ESCALATE = "l4_human_escalate"


@dataclass
class RootCauseAnalysis:
    category: RootCauseCategory
    confidence: float
    evidence: list[str] = field(default_factory=list)
    contributing_factors: dict[str, float] = field(default_factory=dict)
    suggested_level: HealingLevel = HealingLevel.L0_INSTANT_RETRY


@dataclass
class HealingAction:
    level: HealingLevel
    action_type: str
    parameters: dict = field(default_factory=dict)
    estimated_success_rate: float = 0.5
    estimated_time_ms: float = 1000.0
    description: str = ""


@dataclass
class HealingRecord:
    step_id: str
    error_type: str
    root_cause: RootCauseCategory
    healing_level: HealingLevel
    healing_action: str
    success: bool
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0


class RootCauseAnalyzer:
    def __init__(self):
        self._exception_map: dict[type, RootCauseCategory] = {
            TimeoutError: RootCauseCategory.TIMING_ISSUE,
            ConnectionError: RootCauseCategory.NETWORK_ERROR,
            PermissionError: RootCauseCategory.PERMISSION_DENIED,
            ValueError: RootCauseCategory.DATA_VALIDATION,
        }
        self._keyword_map: dict[str, RootCauseCategory] = {
            "not found": RootCauseCategory.SELECTOR_STALE,
            "no such element": RootCauseCategory.SELECTOR_STALE,
            "stale element": RootCauseCategory.SELECTOR_STALE,
            "window not found": RootCauseCategory.STATE_MISMATCH,
            "窗口未找到": RootCauseCategory.STATE_MISMATCH,
            "目标窗口不可用": RootCauseCategory.STATE_MISMATCH,
            "无法激活目标窗口": RootCauseCategory.STATE_MISMATCH,
            "focus window": RootCauseCategory.STATE_MISMATCH,
            "焦点窗口": RootCauseCategory.STATE_MISMATCH,
            "element not interactable": RootCauseCategory.ELEMENT_OBSCURED,
            "element click intercepted": RootCauseCategory.ELEMENT_OBSCURED,
            "timeout": RootCauseCategory.TIMING_ISSUE,
            "timed out": RootCauseCategory.TIMING_ISSUE,
            "not loaded": RootCauseCategory.ELEMENT_NOT_LOADED,
            "loading": RootCauseCategory.ELEMENT_NOT_LOADED,
            "state": RootCauseCategory.STATE_MISMATCH,
            "unexpected": RootCauseCategory.STATE_MISMATCH,
            "permission": RootCauseCategory.PERMISSION_DENIED,
            "access denied": RootCauseCategory.PERMISSION_DENIED,
            "network": RootCauseCategory.NETWORK_ERROR,
            "connection": RootCauseCategory.NETWORK_ERROR,
            "validation": RootCauseCategory.DATA_VALIDATION,
            "invalid": RootCauseCategory.DATA_VALIDATION,
            "conflict": RootCauseCategory.CONCURRENT_CONFLICT,
            "locked": RootCauseCategory.CONCURRENT_CONFLICT,
        }
        self._level_map: dict[RootCauseCategory, HealingLevel] = {
            RootCauseCategory.SELECTOR_STALE: HealingLevel.L1_PARAM_ADJUST,
            RootCauseCategory.ELEMENT_NOT_LOADED: HealingLevel.L0_INSTANT_RETRY,
            RootCauseCategory.ELEMENT_OBSCURED: HealingLevel.L1_PARAM_ADJUST,
            RootCauseCategory.TIMING_ISSUE: HealingLevel.L0_INSTANT_RETRY,
            RootCauseCategory.STATE_MISMATCH: HealingLevel.L1_PARAM_ADJUST,
            RootCauseCategory.PERMISSION_DENIED: HealingLevel.L4_HUMAN_ESCALATE,
            RootCauseCategory.NETWORK_ERROR: HealingLevel.L0_INSTANT_RETRY,
            RootCauseCategory.DATA_VALIDATION: HealingLevel.L1_PARAM_ADJUST,
            RootCauseCategory.CONCURRENT_CONFLICT: HealingLevel.L2_PATH_REPLACE,
            RootCauseCategory.UNKNOWN: HealingLevel.L0_INSTANT_RETRY,
        }

    def analyze(self, error: Exception, context: dict | None = None) -> RootCauseAnalysis:
        category = self._classify_by_exception(error)
        if category == RootCauseCategory.UNKNOWN:
            category = self._classify_by_keyword(error)

        if context:
            context_category = self._classify_by_context(context)
            if context_category != RootCauseCategory.UNKNOWN:
                category = context_category

        evidence = self._collect_evidence(error, context)
        contributing = self._analyze_contributing_factors(error, context)
        suggested_level = self._level_map.get(category, HealingLevel.L0_INSTANT_RETRY)

        confidence = 0.7
        if category == RootCauseCategory.UNKNOWN:
            confidence = 0.3
        if contributing:
            max_factor = max(contributing.values())
            confidence = min(confidence + max_factor * 0.2, 0.95)

        return RootCauseAnalysis(
            category=category,
            confidence=confidence,
            evidence=evidence,
            contributing_factors=contributing,
            suggested_level=suggested_level,
        )

    def _classify_by_exception(self, error: Exception) -> RootCauseCategory:
        for exc_type, category in self._exception_map.items():
            if isinstance(error, exc_type):
                return category
        return RootCauseCategory.UNKNOWN

    def _classify_by_keyword(self, error: Exception) -> RootCauseCategory:
        error_msg = str(error).lower()
        for keyword, category in self._keyword_map.items():
            if keyword in error_msg:
                return category
        return RootCauseCategory.UNKNOWN

    def _classify_by_context(self, context: dict) -> RootCauseCategory:
        if context.get("window_target"):
            return RootCauseCategory.STATE_MISMATCH
        if context.get("retry_count", 0) > 3:
            return RootCauseCategory.SELECTOR_STALE
        if context.get("locate_time_ms", 0) > 5000:
            return RootCauseCategory.ELEMENT_NOT_LOADED
        if context.get("screenshot_changed", False):
            return RootCauseCategory.STATE_MISMATCH
        if context.get("network_available") is False:
            return RootCauseCategory.NETWORK_ERROR
        return RootCauseCategory.UNKNOWN

    def _collect_evidence(self, error: Exception, context: dict | None) -> list[str]:
        evidence = [f"Error: {type(error).__name__}: {str(error)[:200]}"]
        if context:
            if context.get("retry_count", 0) > 0:
                evidence.append(f"Retry count: {context['retry_count']}")
            if context.get("step_type"):
                evidence.append(f"Step type: {context['step_type']}")
            if context.get("strategy_used"):
                evidence.append(f"Strategy: {context['strategy_used']}")
        return evidence

    def _analyze_contributing_factors(self, error: Exception, context: dict | None) -> dict[str, float]:
        factors = {}
        if not context:
            return factors

        retry_count = context.get("retry_count", 0)
        if retry_count > 0:
            factors["repeated_failure"] = min(retry_count * 0.2, 0.8)

        if context.get("locate_time_ms", 0) > 3000:
            factors["slow_location"] = 0.4

        if context.get("confidence", 1.0) < 0.7:
            factors["low_confidence"] = 0.3

        if context.get("screenshot_similarity", 1.0) < 0.8:
            factors["visual_change"] = 0.5

        return factors


class SelfHealingStrategy:
    def __init__(self):
        self._strategies: dict[HealingLevel, list[HealingAction]] = {
            HealingLevel.L0_INSTANT_RETRY: [
                HealingAction(
                    level=HealingLevel.L0_INSTANT_RETRY,
                    action_type="retry_with_backoff",
                    parameters={"max_attempts": 3, "base_delay_ms": 500},
                    estimated_success_rate=0.3,
                    estimated_time_ms=1500,
                    description="指数退避重试",
                ),
                HealingAction(
                    level=HealingLevel.L0_INSTANT_RETRY,
                    action_type="refresh_and_retry",
                    parameters={"refresh_first": True},
                    estimated_success_rate=0.4,
                    estimated_time_ms=3000,
                    description="刷新页面后重试",
                ),
            ],
            HealingLevel.L1_PARAM_ADJUST: [
                HealingAction(
                    level=HealingLevel.L1_PARAM_ADJUST,
                    action_type="increase_timeout",
                    parameters={"timeout_multiplier": 2.0},
                    estimated_success_rate=0.5,
                    estimated_time_ms=2000,
                    description="增加等待超时时间",
                ),
                HealingAction(
                    level=HealingLevel.L1_PARAM_ADJUST,
                    action_type="fallback_selector",
                    parameters={"strategy_priority": ["accessibility_id", "xpath", "image", "coordinate"]},
                    estimated_success_rate=0.6,
                    estimated_time_ms=3000,
                    description="降级选择器策略",
                ),
                HealingAction(
                    level=HealingLevel.L1_PARAM_ADJUST,
                    action_type="scroll_into_view",
                    parameters={"scroll_direction": "center"},
                    estimated_success_rate=0.5,
                    estimated_time_ms=1000,
                    description="滚动到元素可见区域",
                ),
            ],
            HealingLevel.L2_PATH_REPLACE: [
                HealingAction(
                    level=HealingLevel.L2_PATH_REPLACE,
                    action_type="alternative_path",
                    parameters={},
                    estimated_success_rate=0.7,
                    estimated_time_ms=5000,
                    description="使用等效操作路径",
                ),
                HealingAction(
                    level=HealingLevel.L2_PATH_REPLACE,
                    action_type="keyboard_shortcut",
                    parameters={},
                    estimated_success_rate=0.6,
                    estimated_time_ms=500,
                    description="使用键盘快捷键替代点击",
                ),
            ],
            HealingLevel.L3_SEMANTIC_FIX: [
                HealingAction(
                    level=HealingLevel.L3_SEMANTIC_FIX,
                    action_type="re_analyze_page",
                    parameters={},
                    estimated_success_rate=0.85,
                    estimated_time_ms=10000,
                    description="重新分析页面语义并定位元素",
                ),
            ],
            HealingLevel.L4_HUMAN_ESCALATE: [
                HealingAction(
                    level=HealingLevel.L4_HUMAN_ESCALATE,
                    action_type="ask_user",
                    parameters={"timeout_seconds": 300},
                    estimated_success_rate=1.0,
                    estimated_time_ms=60000,
                    description="请求人工介入",
                ),
            ],
        }

    def get_strategy(self, level: HealingLevel) -> list[HealingAction]:
        return self._strategies.get(level, [])

    def get_best_action(self, level: HealingLevel) -> HealingAction | None:
        actions = self._strategies.get(level, [])
        if not actions:
            return None
        return max(actions, key=lambda a: a.estimated_success_rate)

    def get_escalation_path(self, start_level: HealingLevel) -> list[HealingLevel]:
        levels = [
            HealingLevel.L0_INSTANT_RETRY,
            HealingLevel.L1_PARAM_ADJUST,
            HealingLevel.L2_PATH_REPLACE,
            HealingLevel.L3_SEMANTIC_FIX,
            HealingLevel.L4_HUMAN_ESCALATE,
        ]
        start_idx = levels.index(start_level) if start_level in levels else 0
        return levels[start_idx:]


class LearningHealingStore:
    def __init__(self, max_records: int = HEALING_HISTORY_MAX):
        self._records: deque[HealingRecord] = deque(maxlen=max_records)
        self._success_by_cause: dict[RootCauseCategory, dict[str, int]] = {}
        self._success_by_action: dict[str, dict[str, int]] = {}

    def record(self, record: HealingRecord) -> None:
        self._records.append(record)

        if record.root_cause not in self._success_by_cause:
            self._success_by_cause[record.root_cause] = {"success": 0, "total": 0}
        self._success_by_cause[record.root_cause]["total"] += 1
        if record.success:
            self._success_by_cause[record.root_cause]["success"] += 1

        action_key = f"{record.healing_level}:{record.healing_action}"
        if action_key not in self._success_by_action:
            self._success_by_action[action_key] = {"success": 0, "total": 0}
        self._success_by_action[action_key]["total"] += 1
        if record.success:
            self._success_by_action[action_key]["success"] += 1

    def get_best_action_for_cause(self, cause: RootCauseCategory) -> str | None:
        cause_records = [r for r in self._records if r.root_cause == cause and r.success]
        if not cause_records:
            return None
        action_counts: dict[str, int] = {}
        for r in cause_records:
            action_counts[r.healing_action] = action_counts.get(r.healing_action, 0) + 1
        return max(action_counts, key=action_counts.get) if action_counts else None

    def get_success_rate(self, cause: RootCauseCategory | None = None, action: str | None = None) -> float:
        if cause:
            stats = self._success_by_cause.get(cause, {"success": 0, "total": 0})
            return stats["success"] / max(stats["total"], 1)
        if action:
            stats = self._success_by_action.get(action, {"success": 0, "total": 0})
            return stats["success"] / max(stats["total"], 1)
        total = len(self._records)
        if total == 0:
            return 0.0
        return sum(1 for r in self._records if r.success) / total

    def get_stats(self) -> dict:
        return {
            "total_records": len(self._records),
            "overall_success_rate": self.get_success_rate(),
            "cause_stats": {
                cause.value: {
                    "success_rate": stats["success"] / max(stats["total"], 1),
                    "total": stats["total"],
                }
                for cause, stats in self._success_by_cause.items()
            },
        }


class IntelligentErrorRecovery:
    def __init__(self):
        self._root_cause_analyzer = RootCauseAnalyzer()
        self._healing_strategy = SelfHealingStrategy()
        self._learning_store = LearningHealingStore()

    def analyze_and_heal(self, error: Exception, context: dict | None = None) -> HealingAction:
        analysis = self._root_cause_analyzer.analyze(error, context)

        if context:
            if context.get("window_target") and analysis.category in {
                RootCauseCategory.STATE_MISMATCH,
                RootCauseCategory.CONCURRENT_CONFLICT,
                RootCauseCategory.ELEMENT_NOT_LOADED,
            }:
                return HealingAction(
                    level=HealingLevel.L2_PATH_REPLACE,
                    action_type="wait_for_window",
                    parameters={
                        "timeout_ms": 4000,
                        "poll_interval_ms": 250,
                    },
                    estimated_success_rate=0.7,
                    estimated_time_ms=4000,
                    description="等待目标窗口恢复并重新激活",
                )
            if context.get("can_scroll_into_view") and analysis.category in {
                RootCauseCategory.ELEMENT_OBSCURED,
                RootCauseCategory.SELECTOR_STALE,
            }:
                return HealingAction(
                    level=HealingLevel.L1_PARAM_ADJUST,
                    action_type="scroll_into_view",
                    parameters={
                        "scroll_direction": "center",
                        "delta": 500,
                    },
                    estimated_success_rate=0.55,
                    estimated_time_ms=1200,
                    description="滚动目标区域后重新尝试当前步骤",
                )

        learned_action = self._learning_store.get_best_action_for_cause(analysis.category)
        if learned_action and analysis.confidence >= ROOT_CAUSE_CONFIDENCE_THRESHOLD:
            level = self._infer_level_from_action(learned_action)
            actions = self._healing_strategy.get_strategy(level)
            for action in actions:
                if action.action_type == learned_action:
                    return action

        best_action = self._healing_strategy.get_best_action(analysis.suggested_level)
        if best_action:
            return best_action

        escalation = self._healing_strategy.get_escalation_path(analysis.suggested_level)
        for level in escalation:
            action = self._healing_strategy.get_best_action(level)
            if action:
                return action

        return HealingAction(
            level=HealingLevel.L4_HUMAN_ESCALATE,
            action_type="ask_user",
            estimated_success_rate=1.0,
            description="无可用自愈策略，请求人工介入",
        )

    def record_outcome(
        self, step_id: str, error: Exception, action: HealingAction, success: bool, duration_ms: float = 0.0
    ) -> None:
        analysis = self._root_cause_analyzer.analyze(error)
        self._learning_store.record(HealingRecord(
            step_id=step_id,
            error_type=type(error).__name__,
            root_cause=analysis.category,
            healing_level=action.level,
            healing_action=action.action_type,
            success=success,
            duration_ms=duration_ms,
        ))

    def get_healing_stats(self) -> dict:
        return self._learning_store.get_stats()

    @property
    def root_cause_analyzer(self) -> RootCauseAnalyzer:
        return self._root_cause_analyzer

    @staticmethod
    def _infer_level_from_action(action_type: str) -> HealingLevel:
        for level in HealingLevel:
            if level.value in action_type or action_type.startswith("l"):
                return level
        return HealingLevel.L0_INSTANT_RETRY
