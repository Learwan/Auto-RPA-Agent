from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

STABILITY_THRESHOLD = 0.85
REQUIRED_CONSECUTIVE = 2
SAMPLE_INTERVAL_S = 0.05
ANIMATION_SAMPLE_INTERVAL_S = 0.033
ANIMATION_MIN_FRAMES = 3
DOM_MUTATION_WINDOW_MS = 500
NETWORK_IDLE_MS = 500


@dataclass
class StabilityReport:
    stable: bool
    scores: dict[str, float] = field(default_factory=dict)
    fused: float = 0.0
    elapsed_ms: float = 0.0
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "stable": self.stable,
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "fused": round(self.fused, 3),
            "elapsed_ms": round(self.elapsed_ms, 1),
            "details": self.details,
        }


class StabilitySignal:
    async def sample(self, platform_adapter, target_element=None) -> float:
        return 1.0


class DOMStabilitySignal(StabilitySignal):
    def __init__(self, mutation_window_ms: int = DOM_MUTATION_WINDOW_MS):
        self._mutation_window_ms = mutation_window_ms

    async def sample(self, platform_adapter, target_element=None) -> float:
        try:
            if hasattr(platform_adapter, "get_platform_name") and platform_adapter.get_platform_name() != "web":
                return 1.0
            if hasattr(platform_adapter, "execute_js"):
                count = await platform_adapter.execute_js(
                    "window.__dom_stability?.getRecentCount(500) ?? 999"
                )
                if count == 0:
                    return 1.0
                if count <= 3:
                    return 0.8
                if count <= 10:
                    return 0.4
                return 0.0
        except Exception:
            pass
        return 0.7


class AnimationCompletionSignal(StabilitySignal):
    def __init__(
        self,
        frame_threshold: float = 0.01,
        min_frames: int = ANIMATION_MIN_FRAMES,
    ):
        self._frame_threshold = frame_threshold
        self._min_frames = min_frames
        self._recent_scores: deque[float] = deque(maxlen=10)

    async def sample(self, platform_adapter, target_element=None) -> float:
        try:
            screenshot1 = await platform_adapter.capture_screen()
            if not screenshot1:
                return 0.5
            await asyncio.sleep(ANIMATION_SAMPLE_INTERVAL_S)
            screenshot2 = await platform_adapter.capture_screen()
            if not screenshot2:
                return 0.5

            from src.executor.screenshot_comparator import ScreenshotComparator

            comparator = ScreenshotComparator(similarity_threshold=0.99)
            screen_w, screen_h = await platform_adapter.get_screen_size()
            result = comparator.compare(screenshot1, screenshot2, screen_w, screen_h)

            stability = 1.0 - min(result.change_ratio * 50, 1.0)
            self._recent_scores.append(stability)

            if len(self._recent_scores) < self._min_frames:
                return 0.5
            recent = list(self._recent_scores)[-self._min_frames :]
            return min(recent)
        except Exception:
            return 0.5


class LayoutStabilitySignal(StabilitySignal):
    def __init__(self, position_epsilon: float = 2.0, size_epsilon: float = 2.0):
        self._position_epsilon = position_epsilon
        self._size_epsilon = size_epsilon
        self._last_bounds: dict | None = None

    async def sample(self, platform_adapter, target_element=None) -> float:
        if target_element is None:
            return 0.8
        try:
            from src.executor.element_locator import ElementLocator

            locator = ElementLocator(platform_adapter, enable_self_healing=False)
            located = await locator.locate(target_element)
            if located is None:
                return 0.0
            bounds = None
            if located.element and located.element.bounds:
                b = located.element.bounds
                bounds = {"x": b.x, "y": b.y, "w": b.width, "h": b.height}
            if self._last_bounds is None or bounds is None:
                self._last_bounds = bounds
                return 0.7
            dx = abs(bounds["x"] - self._last_bounds["x"])
            dy = abs(bounds["y"] - self._last_bounds["y"])
            dw = abs(bounds["w"] - self._last_bounds["w"])
            dh = abs(bounds["h"] - self._last_bounds["h"])
            self._last_bounds = bounds
            if (
                dx <= self._position_epsilon
                and dy <= self._position_epsilon
                and dw <= self._size_epsilon
                and dh <= self._size_epsilon
            ):
                return 1.0
            return 0.3
        except Exception:
            return 0.5


class NetworkIdleSignal(StabilitySignal):
    def __init__(self, idle_timeout_ms: int = NETWORK_IDLE_MS):
        self._idle_timeout_ms = idle_timeout_ms

    async def sample(self, platform_adapter, target_element=None) -> float:
        try:
            if hasattr(platform_adapter, "execute_js"):
                pending = await platform_adapter.execute_js(
                    "window.__network_idle?.pending ?? -1"
                )
                if pending == 0:
                    return 1.0
                if pending <= 2:
                    return 0.7
                if pending > 0:
                    return 0.3
        except Exception:
            pass
        return 0.8


DEFAULT_SIGNAL_WEIGHTS = {
    "dom": 0.30,
    "animation": 0.25,
    "layout": 0.25,
    "network": 0.20,
}


class MultiSignalWaiter:
    def __init__(
        self,
        weights: dict[str, float] | None = None,
        stability_threshold: float = STABILITY_THRESHOLD,
        required_consecutive: int = REQUIRED_CONSECUTIVE,
    ):
        self._signals: dict[str, StabilitySignal] = {
            "dom": DOMStabilitySignal(),
            "animation": AnimationCompletionSignal(),
            "layout": LayoutStabilitySignal(),
            "network": NetworkIdleSignal(),
        }
        self._weights = weights or DEFAULT_SIGNAL_WEIGHTS
        self._stability_threshold = stability_threshold
        self._required_consecutive = required_consecutive

    async def wait_for_stable(
        self,
        platform_adapter,
        target_element=None,
        timeout_ms: int = 10000,
        required_signals: list[str] | None = None,
    ) -> StabilityReport:
        required = required_signals or list(self._signals.keys())
        deadline = time.monotonic() + timeout_ms / 1000.0
        consecutive_stable = 0
        last_scores: dict[str, float] = {}
        last_fused = 0.0
        start = time.monotonic()

        while time.monotonic() < deadline:
            scores: dict[str, float] = {}
            for name in required:
                signal = self._signals.get(name)
                if signal is None:
                    continue
                try:
                    score = await signal.sample(platform_adapter, target_element)
                    scores[name] = score
                except Exception:
                    scores[name] = 0.0

            fused = sum(
                self._weights.get(n, 0.25) * scores.get(n, 0.0) for n in required
            )
            last_scores = scores
            last_fused = fused

            if fused >= self._stability_threshold:
                consecutive_stable += 1
                if consecutive_stable >= self._required_consecutive:
                    elapsed = (time.monotonic() - start) * 1000
                    return StabilityReport(
                        stable=True,
                        scores=scores,
                        fused=fused,
                        elapsed_ms=elapsed,
                        details="All stability signals converged",
                    )
            else:
                consecutive_stable = 0

            await asyncio.sleep(SAMPLE_INTERVAL_S)

        elapsed = (time.monotonic() - start) * 1000
        failed = [n for n in required if last_scores.get(n, 0) < 0.5]
        return StabilityReport(
            stable=False,
            scores=last_scores,
            fused=last_fused,
            elapsed_ms=elapsed,
            details=f"Timeout: signals not stable ({', '.join(failed)})" if failed else "Timeout",
        )

    async def wait_for_element_stable(
        self,
        locator,
        target,
        timeout_ms: int = 10000,
    ) -> StabilityReport:
        from src.executor.step_verifier import ElementStateChecker

        state_checker = ElementStateChecker(locator._adapter)
        deadline = time.monotonic() + timeout_ms / 1000.0
        start = time.monotonic()
        consecutive_stable = 0

        while time.monotonic() < deadline:
            located = await locator.locate(target)
            if located is None:
                await asyncio.sleep(SAMPLE_INTERVAL_S)
                consecutive_stable = 0
                continue
            report = await state_checker.check_actionability(located, target)
            if report.all_passed:
                consecutive_stable += 1
                if consecutive_stable >= self._required_consecutive:
                    elapsed = (time.monotonic() - start) * 1000
                    return StabilityReport(
                        stable=True,
                        scores={"actionability": report.score},
                        fused=report.score,
                        elapsed_ms=elapsed,
                        details="Element is actionable",
                    )
            else:
                consecutive_stable = 0
            await asyncio.sleep(SAMPLE_INTERVAL_S)

        elapsed = (time.monotonic() - start) * 1000
        return StabilityReport(
            stable=False,
            scores={"actionability": 0.0},
            fused=0.0,
            elapsed_ms=elapsed,
            details="Timeout: element not actionable",
        )
