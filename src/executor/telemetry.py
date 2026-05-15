from __future__ import annotations

import logging
import math
import random
import time
from collections import Counter, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StepTelemetry:
    step_id: str
    step_type: str
    strategy_used: str
    locate_time_ms: float
    execute_time_ms: float
    verify_time_ms: float
    total_time_ms: float
    success: bool
    retry_count: int
    error_category: str | None = None
    confidence: float = 0.0
    screenshot_similarity: float = 0.0
    timestamp: float = field(default_factory=time.time)


class TelemetryCollector:
    def __init__(self, max_records: int = 10000):
        self._records: deque[StepTelemetry] = deque(maxlen=max_records)
        self._flow_records: dict[str, list[StepTelemetry]] = {}

    def record(self, telemetry: StepTelemetry) -> None:
        self._records.append(telemetry)

    def get_step_statistics(self, step_id: str) -> dict:
        records = [r for r in self._records if r.step_id == step_id]
        if not records:
            return {"count": 0}
        success_rate = sum(1 for r in records if r.success) / len(records)
        avg_time = sum(r.total_time_ms for r in records) / len(records)
        strategy_stats: dict[str, dict] = {}
        for r in records:
            s = r.strategy_used
            if s not in strategy_stats:
                strategy_stats[s] = {"count": 0, "success": 0}
            strategy_stats[s]["count"] += 1
            if r.success:
                strategy_stats[s]["success"] += 1
        best_strategy = None
        if strategy_stats:
            best_strategy = max(
                strategy_stats.items(),
                key=lambda x: x[1]["success"] / max(x[1]["count"], 1),
            )[0]
        error_categories = dict(
            Counter(r.error_category for r in records if r.error_category)
        )
        return {
            "count": len(records),
            "success_rate": round(success_rate, 3),
            "avg_time_ms": round(avg_time, 1),
            "p95_time_ms": self._percentile(
                [r.total_time_ms for r in records], 95
            ),
            "best_strategy": best_strategy,
            "error_categories": error_categories,
        }

    def get_flow_summary(self) -> dict:
        if not self._records:
            return {"total": 0}
        total = len(self._records)
        successes = sum(1 for r in self._records if r.success)
        avg_time = sum(r.total_time_ms for r in self._records) / total
        return {
            "total": total,
            "success_rate": round(successes / total, 3),
            "avg_step_time_ms": round(avg_time, 1),
        }

    @staticmethod
    def _percentile(data: list[float], p: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]


PARAM_BOUNDS = {
    "retry_count": (0, 5),
    "delay_ms": (0, 5000),
    "wait_timeout_ms": (1000, 30000),
    "stability_threshold": (0.7, 0.99),
}


class BayesianStrategyOptimizer:
    def __init__(self):
        self._observations: list[dict] = []

    def suggest_params(
        self, step_id: str, telemetry: TelemetryCollector
    ) -> dict:
        stats = telemetry.get_step_statistics(step_id)
        if stats.get("count", 0) < 5:
            return self._default_params()
        best_obs = (
            max(self._observations, key=lambda o: o["reward"])
            if self._observations
            else None
        )
        if best_obs:
            params = best_obs["params"].copy()
            for key, (low, high) in PARAM_BOUNDS.items():
                if key in params:
                    noise = random.gauss(0, (high - low) * 0.1)
                    params[key] = max(low, min(high, params[key] + noise))
                    if isinstance(PARAM_BOUNDS[key][0], int):
                        params[key] = int(round(params[key]))
            return params
        if stats.get("success_rate", 1.0) < 0.5:
            return {
                "retry_count": 3,
                "delay_ms": 1000,
                "wait_timeout_ms": 15000,
                "stability_threshold": 0.90,
            }
        if stats.get("success_rate", 1.0) < 0.8:
            return {
                "retry_count": 2,
                "delay_ms": 500,
                "wait_timeout_ms": 10000,
                "stability_threshold": 0.85,
            }
        return self._default_params()

    def observe(self, params: dict, reward: float) -> None:
        self._observations.append({"params": params, "reward": reward})
        if len(self._observations) > 500:
            self._observations = self._observations[-500:]

    @staticmethod
    def _default_params() -> dict:
        return {
            "retry_count": 2,
            "delay_ms": 500,
            "wait_timeout_ms": 10000,
            "stability_threshold": 0.85,
        }


class GPBayesianOptimizer:
    def __init__(self, param_bounds: dict | None = None):
        self._param_bounds = param_bounds or PARAM_BOUNDS
        self._observations: list[dict] = []
        self._kernel_scale = 1.0
        self._noise_variance = 0.01
        self._exploration_factor = 2.0

    def suggest_params(self, step_id: str = "", telemetry: TelemetryCollector | None = None) -> dict:
        if len(self._observations) < 5:
            return self._random_explore()

        if telemetry and step_id:
            stats = telemetry.get_step_statistics(step_id)
            if stats.get("count", 0) < 5:
                return self._random_explore()

        candidates = self._generate_candidates(200)
        best_idx = self._acquire_ei(candidates)
        return self._array_to_params(candidates[best_idx])

    def observe(self, params: dict, reward: float) -> None:
        self._observations.append({"params": params, "reward": reward})
        if len(self._observations) > 500:
            self._observations = self._observations[-500:]

    def _acquire_ei(self, candidates: list[list[float]]) -> int:
        if not self._observations:
            return random.randint(0, len(candidates) - 1)

        best_reward = max(o["reward"] for o in self._observations)

        best_ei = -1.0
        best_idx = 0

        for idx, candidate in enumerate(candidates):
            mu, sigma = self._predict(candidate)
            if sigma < 1e-9:
                continue

            z = (mu - best_reward) / sigma
            ei = (mu - best_reward) * self._norm_cdf(z) + sigma * self._norm_pdf(z)

            if ei > best_ei:
                best_ei = ei
                best_idx = idx

        return best_idx

    def _predict(self, candidate: list[float]) -> tuple[float, float]:
        if not self._observations:
            return 0.0, 1.0

        weights = []
        rewards = []

        for obs in self._observations:
            obs_vec = self._params_to_array(obs["params"])
            dist = self._euclidean(candidate, obs_vec)
            w = math.exp(-0.5 * (dist / self._kernel_scale) ** 2)
            weights.append(w)
            rewards.append(obs["reward"])

        total_weight = sum(weights)
        if total_weight < 1e-10:
            avg_reward = sum(rewards) / len(rewards)
            return avg_reward, 1.0

        mu = sum(w * r for w, r in zip(weights, rewards, strict=False)) / total_weight

        variance = sum(w * (r - mu) ** 2 for w, r in zip(weights, rewards, strict=False)) / total_weight
        sigma = math.sqrt(max(variance, self._noise_variance))

        return mu, sigma

    def _generate_candidates(self, n: int) -> list[list[float]]:
        candidates = []
        for _ in range(n):
            candidate = []
            for _key, (low, high) in self._param_bounds.items():
                val = random.uniform(low, high)
                if isinstance(low, int):
                    val = int(round(val))
                candidate.append(float(val))
            candidates.append(candidate)
        return candidates

    def _random_explore(self) -> dict:
        params = {}
        for key, (low, high) in self._param_bounds.items():
            val = random.uniform(low, high)
            if isinstance(low, int):
                val = int(round(val))
            params[key] = val
        return params

    def _params_to_array(self, params: dict) -> list[float]:
        return [float(params.get(key, (low + high) / 2)) for key, (low, high) in self._param_bounds.items()]

    def _array_to_params(self, arr: list[float]) -> dict:
        params = {}
        for i, (key, (low, high)) in enumerate(self._param_bounds.items()):
            val = arr[i] if i < len(arr) else (low + high) / 2
            val = max(low, min(high, val))
            if isinstance(low, int):
                val = int(round(val))
            params[key] = val
        return params

    @staticmethod
    def _euclidean(a: list[float], b: list[float]) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=False)))

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def _norm_pdf(x: float) -> float:
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


class MultiObjectiveOptimizer:
    def __init__(self, param_bounds: dict | None = None):
        self._param_bounds = param_bounds or PARAM_BOUNDS
        self._observations: list[dict] = []

    def suggest_params(self) -> dict:
        if len(self._observations) < 5:
            return self._random_params()

        pareto_front = self._compute_pareto_front()
        if not pareto_front:
            return self._random_params()

        best = random.choice(pareto_front)
        params = best["params"].copy()

        for key, (low, high) in self._param_bounds.items():
            if key in params:
                noise = random.gauss(0, (high - low) * 0.05)
                params[key] = max(low, min(high, params[key] + noise))
                if isinstance(low, int):
                    params[key] = int(round(params[key]))

        return params

    def observe(self, params: dict, success_rate: float, avg_time_ms: float) -> None:
        self._observations.append({
            "params": params,
            "success_rate": success_rate,
            "avg_time_ms": avg_time_ms,
        })
        if len(self._observations) > 500:
            self._observations = self._observations[-500:]

    def _compute_pareto_front(self) -> list[dict]:
        if not self._observations:
            return []

        pareto = []
        for obs in self._observations:
            dominated = False
            for other in self._observations:
                if other is obs:
                    continue
                if (other["success_rate"] >= obs["success_rate"]
                        and other["avg_time_ms"] <= obs["avg_time_ms"]
                        and (other["success_rate"] > obs["success_rate"]
                             or other["avg_time_ms"] < obs["avg_time_ms"])):
                    dominated = True
                    break
            if not dominated:
                pareto.append(obs)

        return pareto

    def _random_params(self) -> dict:
        params = {}
        for key, (low, high) in self._param_bounds.items():
            val = random.uniform(low, high)
            if isinstance(low, int):
                val = int(round(val))
            params[key] = val
        return params


@dataclass
class StepProfile:
    step_id: str
    step_type: str
    phase_timings: dict[str, float]
    total_ms: float
    success: bool
    retry_count: int = 0
    retry_waste_ms: float = 0.0
    confidence: float = 0.0


@dataclass
class BottleneckReport:
    bottleneck_type: str
    step_id: str
    severity: str
    details: str
    suggestion: str


class WorkflowProfiler:
    def __init__(self):
        self._profiles: list[StepProfile] = []

    def record_profile(self, profile: StepProfile) -> None:
        self._profiles.append(profile)
        if len(self._profiles) > 1000:
            self._profiles = self._profiles[-1000:]

    def detect_bottlenecks(self) -> list[BottleneckReport]:
        if not self._profiles:
            return []
        bottlenecks: list[BottleneckReport] = []
        total_time = sum(p.total_ms for p in self._profiles)
        if total_time <= 0:
            return bottlenecks

        for profile in self._profiles:
            ratio = profile.total_ms / total_time
            if ratio > 0.3:
                bottlenecks.append(
                    BottleneckReport(
                        bottleneck_type="step_dominant",
                        step_id=profile.step_id,
                        severity="high",
                        details=(
                            f"步骤耗时占总时间 {ratio:.1%} "
                            f"({profile.total_ms:.0f}ms)"
                        ),
                        suggestion="考虑优化该步骤或将其拆分为并行子步骤",
                    )
                )

            locate_time = profile.phase_timings.get("locate", 0)
            locate_ratio = locate_time / max(profile.total_ms, 1)
            if locate_ratio > 0.5 and profile.total_ms > 1000:
                bottlenecks.append(
                    BottleneckReport(
                        bottleneck_type="locate_slow",
                        step_id=profile.step_id,
                        severity="medium",
                        details=(
                            f"定位耗时占步骤 {locate_ratio:.1%} "
                            f"({locate_time:.0f}ms)"
                        ),
                        suggestion="考虑使用更高效的定位策略或增加元素缓存",
                    )
                )

        total_retry_waste = sum(p.retry_waste_ms for p in self._profiles)
        if total_retry_waste > total_time * 0.2:
            bottlenecks.append(
                BottleneckReport(
                    bottleneck_type="retry_waste",
                    step_id="global",
                    severity="high",
                    details=(
                        f"重试浪费 {total_retry_waste:.0f}ms "
                        f"(占总时间 {total_retry_waste / total_time:.1%})"
                    ),
                    suggestion="优化等待策略，减少因时序问题导致的重试",
                )
            )

        return bottlenecks

    def generate_flamegraph_data(self) -> dict:
        if not self._profiles:
            return {"name": "workflow", "value": 0, "children": []}
        return {
            "name": "workflow",
            "value": sum(p.total_ms for p in self._profiles),
            "children": [
                {
                    "name": f"{p.step_type}:{p.step_id[:8]}",
                    "value": p.total_ms,
                    "children": [
                        {
                            "name": "locate",
                            "value": p.phase_timings.get("locate", 0),
                        },
                        {
                            "name": "execute",
                            "value": p.phase_timings.get("execute", 0),
                        },
                        {
                            "name": "verify",
                            "value": p.phase_timings.get("verify", 0),
                        },
                        {
                            "name": "wait",
                            "value": p.phase_timings.get("wait", 0),
                        },
                    ],
                }
                for p in self._profiles
            ],
        }

    def get_summary(self) -> dict:
        if not self._profiles:
            return {"total_steps": 0}
        total_time = sum(p.total_ms for p in self._profiles)
        successes = sum(1 for p in self._profiles if p.success)
        avg_conf = (
            sum(p.confidence for p in self._profiles) / len(self._profiles)
        )
        return {
            "total_steps": len(self._profiles),
            "total_time_ms": round(total_time, 1),
            "success_rate": round(successes / len(self._profiles), 3),
            "avg_confidence": round(avg_conf, 3),
            "avg_step_time_ms": round(total_time / len(self._profiles), 1),
        }


@dataclass
class RegressionAlert:
    step_id: str
    direction: str
    current_ms: float
    baseline_ms: float
    deviation_sigma: float
    details: str


class EWMARegressionDetector:
    def __init__(self, lambda_: float = 0.2, l_factor: float = 2.96):
        self._lambda = lambda_
        self._l_factor = l_factor
        self._ewma: dict[str, float] = {}
        self._baselines: dict[str, tuple[float, float]] = {}
        self._history: dict[str, list[float]] = {}

    def record_execution(self, step_id: str, duration_ms: float) -> RegressionAlert | None:
        if step_id not in self._history:
            self._history[step_id] = []
        self._history[step_id].append(duration_ms)
        if len(self._history[step_id]) > 50:
            self._history[step_id] = self._history[step_id][-50:]

        if step_id not in self._baselines:
            if len(self._history[step_id]) >= 10:
                data = self._history[step_id][:10]
                mean = sum(data) / len(data)
                variance = sum((x - mean) ** 2 for x in data) / len(data)
                std = math.sqrt(variance) if variance > 0 else 1.0
                self._baselines[step_id] = (mean, std)
                self._ewma[step_id] = duration_ms
            return None

        mean, std = self._baselines[step_id]

        prev_ewma = self._ewma.get(step_id, mean)
        self._ewma[step_id] = self._lambda * duration_ms + (1 - self._lambda) * prev_ewma

        n = len(self._history[step_id])
        control_width = self._l_factor * std * math.sqrt(
            self._lambda / (2 - self._lambda) * (1 - (1 - self._lambda) ** (2 * n))
        )

        ucl = mean + control_width
        lcl = mean - control_width

        current_ewma = self._ewma[step_id]

        if current_ewma > ucl:
            return RegressionAlert(
                step_id=step_id,
                direction="degradation",
                current_ms=duration_ms,
                baseline_ms=mean,
                deviation_sigma=(current_ewma - mean) / max(std, 1e-6),
                details=f"EWMA退化检测: EWMA={current_ewma:.1f} > UCL={ucl:.1f}",
            )

        if current_ewma < lcl:
            return RegressionAlert(
                step_id=step_id,
                direction="improvement",
                current_ms=duration_ms,
                baseline_ms=mean,
                deviation_sigma=(mean - current_ewma) / max(std, 1e-6),
                details=f"EWMA改善检测: EWMA={current_ewma:.1f} < LCL={lcl:.1f}",
            )

        return None


class PELTChangePointDetector:
    def __init__(self, penalty: float = 10.0):
        self._penalty = penalty

    def detect(self, series: list[float]) -> list[int]:
        n = len(series)
        if n < 2:
            return []

        cost = [0.0] * (n + 1)
        cost[0] = -self._penalty
        changepoints = [0] * (n + 1)
        r_set = [0]

        for t in range(1, n + 1):
            min_cost = float("inf")
            best_tau = 0

            for tau in r_set:
                segment = series[tau:t]
                c = self._segment_cost(segment) + cost[tau] + self._penalty
                if c < min_cost:
                    min_cost = c
                    best_tau = tau

            cost[t] = min_cost
            changepoints[t] = best_tau

            r_set = [
                tau for tau in r_set
                if cost[tau] + self._segment_cost(series[tau:t]) <= cost[t]
            ]
            r_set.append(t)

        result = []
        t = n
        while t > 0:
            if changepoints[t] > 0:
                result.append(changepoints[t])
            t = changepoints[t]

        return sorted(result)

    @staticmethod
    def _segment_cost(segment: list[float]) -> float:
        if not segment:
            return 0.0
        mean = sum(segment) / len(segment)
        variance = sum((x - mean) ** 2 for x in segment) / len(segment)
        if variance < 1e-10:
            return 0.0
        return len(segment) * math.log(2 * math.pi * variance) / 2


class SuccessRateRegressionDetector:
    def __init__(self, window: int = 20, threshold_sigma: float = 2.5):
        self._window = window
        self._threshold_sigma = threshold_sigma
        self._history: dict[str, deque[bool]] = {}

    def record(self, step_id: str, success: bool) -> RegressionAlert | None:
        if step_id not in self._history:
            self._history[step_id] = deque(maxlen=self._window * 2)
        self._history[step_id].append(success)

        history = list(self._history[step_id])
        if len(history) < self._window:
            return None

        baseline = history[:self._window]
        recent = history[-self._window:]

        baseline_rate = sum(baseline) / len(baseline)
        recent_rate = sum(recent) / len(recent)

        p = baseline_rate
        if p <= 0 or p >= 1:
            return None

        se = math.sqrt(p * (1 - p) / len(recent))
        z_score = (baseline_rate - recent_rate) / max(se, 1e-6)

        if z_score > self._threshold_sigma:
            return RegressionAlert(
                step_id=step_id,
                direction="degradation",
                current_ms=0.0,
                baseline_ms=baseline_rate * 100,
                deviation_sigma=z_score,
                details=f"成功率退化: {recent_rate:.1%} <- {baseline_rate:.1%} (z={z_score:.1f})",
            )

        return None


class StrategyRegressionDetector:
    def __init__(self, window: int = 20):
        self._window = window
        self._strategy_history: dict[str, dict[str, deque[bool]]] = {}

    def record(self, step_id: str, strategy: str, success: bool) -> RegressionAlert | None:
        if step_id not in self._strategy_history:
            self._strategy_history[step_id] = {}
        if strategy not in self._strategy_history[step_id]:
            self._strategy_history[step_id][strategy] = deque(maxlen=self._window * 2)

        self._strategy_history[step_id][strategy].append(success)

        history = list(self._strategy_history[step_id][strategy])
        if len(history) < self._window:
            return None

        baseline = history[:self._window]
        recent = history[-self._window:]

        baseline_rate = sum(baseline) / len(baseline)
        recent_rate = sum(recent) / len(recent)

        drop = baseline_rate - recent_rate
        if drop > 0.2:
            return RegressionAlert(
                step_id=step_id,
                direction="degradation",
                current_ms=0.0,
                baseline_ms=baseline_rate * 100,
                deviation_sigma=drop / 0.1,
                details=f"策略'{strategy}'退化: {recent_rate:.1%} <- {baseline_rate:.1%}",
            )

        return None


class MultiDimensionalRegressionDetector:
    def __init__(self):
        self._time_detector = EWMARegressionDetector()
        self._success_detector = SuccessRateRegressionDetector()
        self._strategy_detector = StrategyRegressionDetector()
        self._pelt_detector = PELTChangePointDetector()
        self._time_history: dict[str, list[float]] = {}

    def record_execution(self, telemetry: StepTelemetry) -> list[RegressionAlert]:
        alerts = []

        time_alert = self._time_detector.record_execution(
            telemetry.step_id, telemetry.total_time_ms
        )
        if time_alert:
            alerts.append(time_alert)

        success_alert = self._success_detector.record(
            telemetry.step_id, telemetry.success
        )
        if success_alert:
            alerts.append(success_alert)

        strategy_alert = self._strategy_detector.record(
            telemetry.step_id, telemetry.strategy_used, telemetry.success
        )
        if strategy_alert:
            alerts.append(strategy_alert)

        if telemetry.step_id not in self._time_history:
            self._time_history[telemetry.step_id] = []
        self._time_history[telemetry.step_id].append(telemetry.total_time_ms)
        if len(self._time_history[telemetry.step_id]) > 100:
            self._time_history[telemetry.step_id] = self._time_history[telemetry.step_id][-100:]

        if len(self._time_history[telemetry.step_id]) >= 10:
            change_points = self._pelt_detector.detect(
                self._time_history[telemetry.step_id]
            )
            if change_points and change_points[-1] > len(self._time_history[telemetry.step_id]) - 5:
                recent_vals = self._time_history[telemetry.step_id][-5:]
                baseline_vals = self._time_history[telemetry.step_id][:-5]
                if baseline_vals:
                    recent_avg = sum(recent_vals) / len(recent_vals)
                    baseline_avg = sum(baseline_vals) / len(baseline_vals)
                    if recent_avg > baseline_avg * 1.3:
                        baseline_std = math.sqrt(
                            sum((x - baseline_avg) ** 2 for x in baseline_vals) / len(baseline_vals)
                        )
                        alerts.append(RegressionAlert(
                            step_id=telemetry.step_id,
                            direction="degradation",
                            current_ms=recent_avg,
                            baseline_ms=baseline_avg,
                            deviation_sigma=(recent_avg - baseline_avg) / max(baseline_std, 1),
                            details=f"PELT变点检测: 性能在步骤{change_points[-1]}处突变",
                        ))

        return alerts


class PerformanceRegressionDetector:
    def __init__(self, window_size: int = 20, threshold: float = 3.0):
        self._window_size = window_size
        self._threshold = threshold
        self._baselines: dict[str, list[float]] = {}

    def record_execution(
        self, step_id: str, duration_ms: float
    ) -> RegressionAlert | None:
        if step_id not in self._baselines:
            self._baselines[step_id] = []
        self._baselines[step_id].append(duration_ms)
        if len(self._baselines[step_id]) > self._window_size * 2:
            self._baselines[step_id] = self._baselines[step_id][
                -self._window_size :
            ]

        history = self._baselines[step_id]
        if len(history) < self._window_size:
            return None

        recent = history[-self._window_size :]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = math.sqrt(variance)

        if std < 1e-6:
            return None

        cusum_pos = 0.0
        cusum_neg = 0.0
        for val in recent[-5:]:
            normalized = (val - mean) / std
            cusum_pos = max(0, cusum_pos + normalized - 0.5)
            cusum_neg = max(0, cusum_neg - normalized - 0.5)

        if cusum_pos > self._threshold:
            return RegressionAlert(
                step_id=step_id,
                direction="degradation",
                current_ms=duration_ms,
                baseline_ms=mean,
                deviation_sigma=(duration_ms - mean) / std,
                details=(
                    f"性能退化检测: CUSUM={cusum_pos:.1f} "
                    f"> {self._threshold}"
                ),
            )

        if cusum_neg > self._threshold:
            return RegressionAlert(
                step_id=step_id,
                direction="improvement",
                current_ms=duration_ms,
                baseline_ms=mean,
                deviation_sigma=(mean - duration_ms) / std,
                details=(
                    f"性能改善检测: CUSUM={cusum_neg:.1f} "
                    f"> {self._threshold}"
                ),
            )

        return None
