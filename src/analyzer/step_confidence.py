from __future__ import annotations


class StepConfidenceScore:

    COMPOSITE_WEIGHTS = {
        "selector_stability": 0.30,
        "semantic_relevance": 0.25,
        "structural_confidence": 0.20,
        "historical_success_rate": 0.25,
    }

    LEVEL_THRESHOLDS = {
        "HIGH": 0.85,
        "MEDIUM": 0.60,
        "LOW": 0.35,
    }

    def __init__(
        self,
        selector_stability: float = 0.5,
        semantic_relevance: float = 0.5,
        structural_confidence: float = 0.5,
        historical_success_rate: float = 0.5,
    ):
        self.selector_stability = selector_stability
        self.semantic_relevance = semantic_relevance
        self.structural_confidence = structural_confidence
        self.historical_success_rate = historical_success_rate

    @property
    def composite(self) -> float:
        return sum(
            getattr(self, k) * v
            for k, v in self.COMPOSITE_WEIGHTS.items()
        )

    @property
    def level(self) -> str:
        c = self.composite
        if c >= self.LEVEL_THRESHOLDS["HIGH"]:
            return "HIGH"
        if c >= self.LEVEL_THRESHOLDS["MEDIUM"]:
            return "MEDIUM"
        if c >= self.LEVEL_THRESHOLDS["LOW"]:
            return "LOW"
        return "CRITICAL"

    def to_dict(self) -> dict:
        return {
            "selector_stability": round(self.selector_stability, 3),
            "semantic_relevance": round(self.semantic_relevance, 3),
            "structural_confidence": round(self.structural_confidence, 3),
            "historical_success_rate": round(self.historical_success_rate, 3),
            "composite": round(self.composite, 3),
            "level": self.level,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepConfidenceScore":
        return cls(
            selector_stability=data.get("selector_stability", 0.5),
            semantic_relevance=data.get("semantic_relevance", 0.5),
            structural_confidence=data.get("structural_confidence", 0.5),
            historical_success_rate=data.get("historical_success_rate", 0.5),
        )
