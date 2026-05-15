from __future__ import annotations

import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

FEEDBACK_WINDOW = 50
EVALUATION_INTERVAL_S = 300
MUTATION_RATE = 0.15
POPULATION_SIZE = 10
ELITE_COUNT = 2


class CapabilityDimension(StrEnum):
    SEGMENTATION = "segmentation"
    EXECUTION = "execution"
    ERROR_RECOVERY = "error_recovery"
    ELEMENT_LOCATION = "element_location"
    VISUAL_COMPARISON = "visual_comparison"
    WORKFLOW_OPTIMIZATION = "workflow_optimization"


@dataclass
class ExecutionFeedback:
    step_id: str
    success: bool
    duration_ms: float
    error_type: str | None = None
    strategy_used: str = ""
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class CapabilityScore:
    dimension: CapabilityDimension
    score: float
    trend: str = "stable"
    sample_count: int = 0
    last_updated: float = field(default_factory=time.time)


@dataclass
class TuningParameter:
    name: str
    current_value: float
    min_value: float
    max_value: float
    step_size: float
    impact_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class EvolutionResult:
    generation: int
    best_params: dict[str, float]
    best_fitness: float
    improvement_rate: float
    evaluations: int


class FeedbackCollector:
    def __init__(self, window_size: int = FEEDBACK_WINDOW):
        self._window_size = window_size
        self._feedback: deque[ExecutionFeedback] = deque(maxlen=window_size * 10)
        self._step_feedback: dict[str, deque[ExecutionFeedback]] = {}

    def record(self, feedback: ExecutionFeedback) -> None:
        self._feedback.append(feedback)

        if feedback.step_id not in self._step_feedback:
            self._step_feedback[feedback.step_id] = deque(maxlen=self._window_size)
        self._step_feedback[feedback.step_id].append(feedback)

    def get_recent_stats(self, window: int | None = None) -> dict:
        w = window or self._window_size
        recent = list(self._feedback)[-w:]

        if not recent:
            return {"count": 0}

        success_rate = sum(1 for f in recent if f.success) / len(recent)
        avg_duration = sum(f.duration_ms for f in recent) / len(recent)
        error_types: dict[str, int] = {}
        for f in recent:
            if f.error_type:
                error_types[f.error_type] = error_types.get(f.error_type, 0) + 1

        return {
            "count": len(recent),
            "success_rate": round(success_rate, 4),
            "avg_duration_ms": round(avg_duration, 1),
            "error_types": error_types,
        }

    def get_step_stats(self, step_id: str) -> dict:
        feedbacks = list(self._step_feedback.get(step_id, []))
        if not feedbacks:
            return {"count": 0}
        success_rate = sum(1 for f in feedbacks if f.success) / len(feedbacks)
        return {
            "count": len(feedbacks),
            "success_rate": round(success_rate, 4),
            "avg_duration_ms": round(sum(f.duration_ms for f in feedbacks) / len(feedbacks), 1),
        }


class CapabilitySelfAssessment:
    def __init__(self):
        self._scores: dict[str, CapabilityScore] = {}
        self._assessment_history: list[dict] = []

    def assess(self, feedback_collector: FeedbackCollector) -> dict[str, CapabilityScore]:
        stats = feedback_collector.get_recent_stats()
        count = stats.get("count", 0)
        success_rate = stats.get("success_rate", 0.0)

        dimensions = {
            CapabilityDimension.SEGMENTATION: success_rate * 0.8 + 0.2,
            CapabilityDimension.EXECUTION: success_rate,
            CapabilityDimension.ERROR_RECOVERY: min(success_rate + 0.1, 1.0),
            CapabilityDimension.ELEMENT_LOCATION: success_rate * 0.9 + 0.1,
            CapabilityDimension.VISUAL_COMPARISON: success_rate * 0.85 + 0.15,
            CapabilityDimension.WORKFLOW_OPTIMIZATION: success_rate * 0.7 + 0.3,
        }

        for dim, score_val in dimensions.items():
            old_score = self._scores.get(dim.value)
            trend = "stable"
            if old_score:
                if score_val > old_score.score + 0.02:
                    trend = "improving"
                elif score_val < old_score.score - 0.02:
                    trend = "declining"

            self._scores[dim.value] = CapabilityScore(
                dimension=dim,
                score=round(score_val, 4),
                trend=trend,
                sample_count=count,
            )

        self._assessment_history.append({
            "timestamp": time.time(),
            "scores": {k: s.score for k, s in self._scores.items()},
        })
        if len(self._assessment_history) > 100:
            self._assessment_history = self._assessment_history[-100:]

        return dict(self._scores)

    def get_weakest_dimension(self) -> CapabilityDimension | None:
        if not self._scores:
            return None
        weakest = min(self._scores.values(), key=lambda s: s.score)
        return weakest.dimension

    def get_score(self, dimension: CapabilityDimension) -> float:
        score = self._scores.get(dimension.value)
        return score.score if score else 0.5

    def get_summary(self) -> dict:
        if not self._scores:
            return {"overall": 0.5, "dimensions": {}}
        overall = sum(s.score for s in self._scores.values()) / len(self._scores)
        return {
            "overall": round(overall, 4),
            "dimensions": {
                dim.value: {"score": s.score, "trend": s.trend}
                for dim, s in self._scores.items()
            },
        }


class AutoTuner:
    def __init__(self):
        self._params: dict[str, TuningParameter] = {
            "retry_count": TuningParameter("retry_count", 2.0, 0.0, 5.0, 1.0),
            "delay_ms": TuningParameter("delay_ms", 500.0, 100.0, 3000.0, 100.0),
            "wait_timeout_ms": TuningParameter("wait_timeout_ms", 10000.0, 2000.0, 30000.0, 1000.0),
            "stability_threshold": TuningParameter("stability_threshold", 0.85, 0.7, 0.99, 0.05),
            "confidence_threshold": TuningParameter("confidence_threshold", 0.7, 0.5, 0.95, 0.05),
        }
        self._population: list[dict[str, float]] = []
        self._fitness: list[float] = []
        self._generation = 0
        self._best_params: dict[str, float] = {}
        self._best_fitness = 0.0

    def initialize_population(self) -> None:
        self._population = []
        self._fitness = []

        self._population.append({name: p.current_value for name, p in self._params.items()})

        for _ in range(POPULATION_SIZE - 1):
            individual = {}
            for name, param in self._params.items():
                individual[name] = random.uniform(param.min_value, param.max_value)
            self._population.append(individual)

        self._fitness = [0.0] * len(self._population)

    def evaluate(self, individual: dict[str, float], feedback: FeedbackCollector) -> float:
        stats = feedback.get_recent_stats()
        success_rate = stats.get("success_rate", 0.5)
        avg_duration = stats.get("avg_duration_ms", 1000.0)

        normalized_duration = 1.0 - min(avg_duration / 30000.0, 1.0)

        retry_penalty = individual.get("retry_count", 2.0) * 0.02
        delay_penalty = individual.get("delay_ms", 500.0) / 10000.0 * 0.1

        fitness = success_rate * 0.7 + normalized_duration * 0.3 - retry_penalty - delay_penalty
        return max(0.0, min(1.0, fitness))

    def evolve(self, feedback: FeedbackCollector) -> EvolutionResult:
        if not self._population:
            self.initialize_population()

        for i, individual in enumerate(self._population):
            self._fitness[i] = self.evaluate(individual, feedback)

        sorted_indices = sorted(range(len(self._population)), key=lambda i: self._fitness[i], reverse=True)

        new_population = []
        for i in range(min(ELITE_COUNT, len(sorted_indices))):
            new_population.append(self._population[sorted_indices[i]].copy())

        while len(new_population) < POPULATION_SIZE:
            parent_a = self._tournament_select()
            parent_b = self._tournament_select()
            child = self._crossover(parent_a, parent_b)
            child = self._mutate(child)
            new_population.append(child)

        self._population = new_population
        self._generation += 1

        best_idx = max(range(len(self._population)), key=lambda i: self._fitness[i])
        old_best = self._best_fitness
        self._best_params = self._population[best_idx].copy()
        self._best_fitness = self._fitness[best_idx]

        improvement = self._best_fitness - old_best if old_best > 0 else 0.0

        return EvolutionResult(
            generation=self._generation,
            best_params=self._best_params,
            best_fitness=self._best_fitness,
            improvement_rate=improvement,
            evaluations=len(self._population),
        )

    def get_current_params(self) -> dict[str, float]:
        if self._best_params:
            return self._best_params.copy()
        return {name: p.current_value for name, p in self._params.items()}

    def _tournament_select(self, k: int = 3) -> dict[str, float]:
        indices = random.sample(range(len(self._population)), min(k, len(self._population)))
        best_idx = max(indices, key=lambda i: self._fitness[i])
        return self._population[best_idx].copy()

    def _crossover(self, parent_a: dict[str, float], parent_b: dict[str, float]) -> dict[str, float]:
        child = {}
        for key in parent_a:
            if random.random() < 0.5:
                child[key] = parent_a[key]
            else:
                child[key] = parent_b[key]
        return child

    def _mutate(self, individual: dict[str, float]) -> dict[str, float]:
        mutated = individual.copy()
        for name, param in self._params.items():
            if random.random() < MUTATION_RATE:
                delta = random.gauss(0, param.step_size)
                mutated[name] = max(param.min_value, min(param.max_value, mutated[name] + delta))
        return mutated


class SelfEvolutionSystem:
    def __init__(self):
        self._feedback_collector = FeedbackCollector()
        self._assessment = CapabilitySelfAssessment()
        self._tuner = AutoTuner()
        self._last_evaluation = 0.0
        self._evolution_count = 0

    def record_feedback(self, feedback: ExecutionFeedback) -> None:
        self._feedback_collector.record(feedback)

    def evaluate_and_tune(self) -> dict:
        now = time.time()
        if now - self._last_evaluation < EVALUATION_INTERVAL_S:
            return {"status": "skipped", "reason": "too_soon"}

        scores = self._assessment.assess(self._feedback_collector)
        evolution = self._tuner.evolve(self._feedback_collector)
        self._evolution_count += 1
        self._last_evaluation = now

        weakest = self._assessment.get_weakest_dimension()

        return {
            "status": "completed",
            "generation": evolution.generation,
            "best_fitness": evolution.best_fitness,
            "improvement_rate": evolution.improvement_rate,
            "weakest_dimension": weakest.value if weakest else "unknown",
            "current_params": evolution.best_params,
            "scores": {dim.value: s.score for dim, s in scores.items()},
        }

    def get_current_params(self) -> dict:
        return self._tuner.get_current_params()

    def get_assessment_summary(self) -> dict:
        return self._assessment.get_summary()

    def get_feedback_stats(self) -> dict:
        return self._feedback_collector.get_recent_stats()

    def get_evolution_stats(self) -> dict:
        return {
            "evolution_count": self._evolution_count,
            "generation": self._tuner._generation,
            "best_fitness": self._tuner._best_fitness,
            "population_size": len(self._tuner._population),
        }
