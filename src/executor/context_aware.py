from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

STATE_HISTORY_MAX = 50
STATE_SIMILARITY_THRESHOLD = 0.85
STRATEGY_ADAPT_WINDOW = 20


class ExecutionStrategy(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass
class AppState:
    app_name: str
    window_title: str
    visible_elements_hash: str
    ui_hash: str
    timestamp: float = field(default_factory=time.time)
    element_count: int = 0
    is_responsive: bool = True

    def fingerprint(self) -> str:
        raw = f"{self.app_name}|{self.window_title}|{self.visible_elements_hash}|{self.ui_hash}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]


@dataclass
class StateTransition:
    from_state: AppState | None
    to_state: AppState
    trigger: str
    timestamp: float = field(default_factory=time.time)
    verified: bool = False


@dataclass
class Precondition:
    app_name: str | None = None
    window_title_contains: str | None = None
    element_exists: str | None = None
    ui_state_hash: str | None = None

    def check(self, current_state: AppState) -> bool:
        if self.app_name and current_state.app_name != self.app_name:
            return False
        if self.window_title_contains and self.window_title_contains not in current_state.window_title:
            return False
        return not (self.ui_state_hash and current_state.ui_hash != self.ui_state_hash)


@dataclass
class Postcondition:
    app_name: str | None = None
    window_title_contains: str | None = None
    ui_changed: bool = False

    def check(self, before: AppState, after: AppState) -> bool:
        if self.app_name and after.app_name != self.app_name:
            return False
        if self.window_title_contains and self.window_title_contains not in after.window_title:
            return False
        return not (self.ui_changed and before.ui_hash == after.ui_hash)


class ApplicationStateMachine:
    def __init__(self):
        self._current_state: AppState | None = None
        self._state_history: deque[AppState] = deque(maxlen=STATE_HISTORY_MAX)
        self._transitions: list[StateTransition] = []
        self._state_cache: dict[str, int] = {}

    async def observe(self, platform_adapter) -> AppState:
        try:
            window_info = await platform_adapter.get_active_window()
            element_tree = await platform_adapter.get_element_tree()

            app_name = getattr(window_info, "app_name", "unknown")
            window_title = getattr(window_info, "title", "unknown")

            elements_str = str(element_tree)[:2000] if element_tree else ""
            elements_hash = hashlib.md5(elements_str.encode()).hexdigest()[:12]
            element_count = len(element_tree) if isinstance(element_tree, list) else 0

            state = AppState(
                app_name=app_name,
                window_title=window_title,
                visible_elements_hash=elements_hash,
                ui_hash=self._compute_ui_hash(elements_str, app_name, window_title),
                element_count=element_count,
                is_responsive=True,
            )

            if self._current_state and self._current_state.fingerprint() != state.fingerprint():
                transition = StateTransition(
                    from_state=self._current_state,
                    to_state=state,
                    trigger="state_change",
                )
                self._transitions.append(transition)

            self._current_state = state
            self._state_history.append(state)

            fp = state.fingerprint()
            self._state_cache[fp] = self._state_cache.get(fp, 0) + 1

            return state

        except Exception as e:
            logger.debug(f"State observation failed: {e}")
            return AppState(
                app_name="unknown",
                window_title="unknown",
                visible_elements_hash="",
                ui_hash="error",
                is_responsive=False,
            )

    def verify_precondition(self, precondition: Precondition) -> bool:
        if not self._current_state:
            return False
        return precondition.check(self._current_state)

    def verify_postcondition(self, precondition: Postcondition, before_state: AppState) -> bool:
        if not self._current_state:
            return False
        return precondition.check(before_state, self._current_state)

    def compute_state_similarity(self, state_a: AppState, state_b: AppState) -> float:
        if state_a.app_name != state_b.app_name:
            return 0.0

        title_sim = self._string_similarity(state_a.window_title, state_b.window_title)
        hash_match = 1.0 if state_a.visible_elements_hash == state_b.visible_elements_hash else 0.0

        return title_sim * 0.4 + hash_match * 0.6

    def get_stable_state(self) -> AppState | None:
        if not self._state_history:
            return None

        recent = list(self._state_history)[-5:]
        if all(s.fingerprint() == recent[0].fingerprint() for s in recent):
            return recent[0]
        return None

    def get_state_frequency(self, fingerprint: str) -> int:
        return self._state_cache.get(fingerprint, 0)

    @property
    def current_state(self) -> AppState | None:
        return self._current_state

    @property
    def transition_count(self) -> int:
        return len(self._transitions)

    @staticmethod
    def _compute_ui_hash(elements_str: str, app_name: str, window_title: str) -> str:
        raw = f"{app_name}|{window_title}|{elements_str}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    @staticmethod
    def _string_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0

        max_len = max(len(a), len(b))
        if max_len == 0:
            return 1.0

        common = sum(1 for ca, cb in zip(a, b, strict=False) if ca == cb)
        return common / max_len


@dataclass
class StepPerformance:
    step_id: str
    success: bool
    duration_ms: float
    timestamp: float = field(default_factory=time.time)


class AdaptiveExecutionStrategy:
    def __init__(self):
        self._current_strategy = ExecutionStrategy.BALANCED
        self._performance_history: deque[StepPerformance] = deque(maxlen=STRATEGY_ADAPT_WINDOW)
        self._strategy_stats: dict[ExecutionStrategy, dict] = {
            ExecutionStrategy.CONSERVATIVE: {"success": 0, "total": 0, "avg_time_ms": 0.0},
            ExecutionStrategy.BALANCED: {"success": 0, "total": 0, "avg_time_ms": 0.0},
            ExecutionStrategy.AGGRESSIVE: {"success": 0, "total": 0, "avg_time_ms": 0.0},
        }

    def record_step(self, step_id: str, success: bool, duration_ms: float) -> None:
        perf = StepPerformance(step_id=step_id, success=success, duration_ms=duration_ms)
        self._performance_history.append(perf)

        stats = self._strategy_stats[self._current_strategy]
        stats["total"] += 1
        if success:
            stats["success"] += 1
        total = stats["total"]
        if total > 0:
            old_avg = stats["avg_time_ms"]
            stats["avg_time_ms"] = old_avg + (duration_ms - old_avg) / total

        self._adapt()

    def get_current_strategy(self) -> ExecutionStrategy:
        return self._current_strategy

    def get_execution_params(self) -> dict:
        params = {
            ExecutionStrategy.CONSERVATIVE: {
                "retry_count": 4,
                "delay_ms": 1000,
                "wait_timeout_ms": 20000,
                "stability_threshold": 0.92,
                "verify_before_execute": True,
                "verify_after_execute": True,
            },
            ExecutionStrategy.BALANCED: {
                "retry_count": 2,
                "delay_ms": 500,
                "wait_timeout_ms": 10000,
                "stability_threshold": 0.85,
                "verify_before_execute": True,
                "verify_after_execute": False,
            },
            ExecutionStrategy.AGGRESSIVE: {
                "retry_count": 1,
                "delay_ms": 200,
                "wait_timeout_ms": 5000,
                "stability_threshold": 0.75,
                "verify_before_execute": False,
                "verify_after_execute": False,
            },
        }
        return params[self._current_strategy]

    def _adapt(self) -> None:
        if len(self._performance_history) < 5:
            return

        recent = list(self._performance_history)[-10:]
        success_rate = sum(1 for p in recent if p.success) / len(recent)
        avg_time = sum(p.duration_ms for p in recent) / len(recent)

        if success_rate < 0.6:
            self._switch_strategy(ExecutionStrategy.CONSERVATIVE)
        elif success_rate > 0.9 and avg_time > 3000:
            self._switch_strategy(ExecutionStrategy.AGGRESSIVE)
        elif success_rate > 0.75:
            self._switch_strategy(ExecutionStrategy.BALANCED)

    def _switch_strategy(self, new_strategy: ExecutionStrategy) -> None:
        if new_strategy == self._current_strategy:
            return
        logger.info(
            f"Strategy adapted: {self._current_strategy.value} -> {new_strategy.value}"
        )
        self._current_strategy = new_strategy


class ContextAwareExecutor:
    def __init__(self, platform_adapter=None):
        self._state_machine = ApplicationStateMachine()
        self._strategy = AdaptiveExecutionStrategy()
        self._platform_adapter = platform_adapter
        self._precondition_cache: dict[str, Precondition] = {}
        self._postcondition_cache: dict[str, Postcondition] = {}

    async def before_step(self, step_id: str, step_info: dict | None = None) -> dict:
        params = self._strategy.get_execution_params()
        state = None

        if self._platform_adapter and params.get("verify_before_execute"):
            state = await self._state_machine.observe(self._platform_adapter)

            precondition = self._precondition_cache.get(step_id)
            if precondition and not self._state_machine.verify_precondition(precondition):
                logger.warning(
                    f"Precondition not met for step {step_id}: "
                    f"app={state.app_name if state else 'unknown'}"
                )
                params["precondition_failed"] = True

        return {
            "execution_params": params,
            "current_state": state,
            "strategy": self._strategy.get_current_strategy(),
        }

    async def after_step(self, step_id: str, success: bool, duration_ms: float) -> dict:
        self._strategy.record_step(step_id, success, duration_ms)

        result = {
            "strategy": self._strategy.get_current_strategy(),
            "post_verified": False,
        }

        if self._platform_adapter and success:
            before_state = self._state_machine.current_state
            await self._state_machine.observe(self._platform_adapter)

            postcondition = self._postcondition_cache.get(step_id)
            if postcondition and before_state:
                result["post_verified"] = self._state_machine.verify_postcondition(
                    postcondition, before_state
                )

        return result

    def set_precondition(self, step_id: str, precondition: Precondition) -> None:
        self._precondition_cache[step_id] = precondition

    def set_postcondition(self, step_id: str, postcondition: Postcondition) -> None:
        self._postcondition_cache[step_id] = postcondition

    def learn_conditions_from_history(self, step_id: str, states: list[AppState]) -> None:
        if len(states) < 2:
            return

        before = states[0]
        after = states[-1]

        if before.app_name:
            self._precondition_cache[step_id] = Precondition(app_name=before.app_name)

        if after.app_name and after.app_name != before.app_name:
            self._postcondition_cache[step_id] = Postcondition(
                app_name=after.app_name,
                ui_changed=True,
            )
        elif after.ui_hash != before.ui_hash:
            self._postcondition_cache[step_id] = Postcondition(ui_changed=True)

    @property
    def state_machine(self) -> ApplicationStateMachine:
        return self._state_machine

    @property
    def strategy(self) -> AdaptiveExecutionStrategy:
        return self._strategy
