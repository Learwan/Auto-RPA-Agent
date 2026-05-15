from src.analyzer.confidence_scorer import ConfidenceScorer, ScoredFlow
from src.analyzer.preprocessor import NormalizedOperation
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    DetectedPattern,
    ErrorHandlingPolicy,
    LocateStrategy,
    StepTarget,
    StepType,
)


def _make_flow_with_strategy(strategy: LocateStrategy, step_count: int = 5) -> AutomationFlow:
    steps = []
    for i in range(step_count):
        target = StepTarget(strategy=strategy, position={"x": 100 + i * 50, "y": 200})
        steps.append(
            AutomationStep(
                id=f"step-{i}",
                type=StepType.CLICK,
                action={"button": "left"},
                target=target,
                execution_order=i,
                delay=300,
            )
        )
    return AutomationFlow(
        id="flow-1",
        name="Test Flow",
        steps=steps,
        error_handling=ErrorHandlingPolicy(),
        confidence=0.5,
    )


def _make_pattern(support: int = 3, instance_count: int = 3) -> DetectedPattern:
    instances = [{"start_idx": i * 3, "end_idx": i * 3 + 2} for i in range(instance_count)]
    return DetectedPattern(
        id="pat-1",
        pattern_sequence=[StepType.CLICK, StepType.TYPE, StepType.CLICK],
        instances=instances,
        support=support,
        avg_duration_ms=2000,
        confidence=0.7,
    )


def _make_operations(count: int = 10) -> list[NormalizedOperation]:
    ops = []
    for i in range(count):
        ops.append(
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 100 + i * 10, "y": 200},
                timestamp=1000 + i * 500,
                seq_num=i,
            )
        )
    return ops


class TestConfidenceScorerBasic:
    def test_score_flow_returns_scored_flow(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID)
        pattern = _make_pattern()
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        assert isinstance(result, ScoredFlow)
        assert 0.0 <= result.overall_confidence <= 1.0

    def test_score_flow_has_four_dimensions(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID)
        pattern = _make_pattern()
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        assert len(result.dimensions) == 4
        dim_names = [d.name for d in result.dimensions]
        assert "stability" in dim_names
        assert "locator_reliability" in dim_names
        assert "context_dependency" in dim_names
        assert "anomaly_detection" in dim_names


class TestConfidenceScorerLocatorReliability:
    def test_accessibility_id_strategy_scores_high(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID)
        pattern = _make_pattern()
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        locator_dim = next(d for d in result.dimensions if d.name == "locator_reliability")
        assert locator_dim.score >= 0.8

    def test_position_only_strategy_scores_low(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.POSITION)
        pattern = _make_pattern()
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        locator_dim = next(d for d in result.dimensions if d.name == "locator_reliability")
        assert locator_dim.score < 0.6

    def test_mixed_strategies_score_moderate(self):
        scorer = ConfidenceScorer()
        steps = []
        strategies = [LocateStrategy.ACCESSIBILITY_ID, LocateStrategy.POSITION, LocateStrategy.TEXT_MATCH]
        for i in range(6):
            strategy = strategies[i % len(strategies)]
            steps.append(
                AutomationStep(
                    id=f"step-{i}",
                    type=StepType.CLICK,
                    action={"button": "left"},
                    target=StepTarget(strategy=strategy, position={"x": 100, "y": 200}),
                    execution_order=i,
                    delay=300,
                )
            )
        flow = AutomationFlow(
            id="flow-1",
            name="Mixed Flow",
            steps=steps,
            error_handling=ErrorHandlingPolicy(),
            confidence=0.5,
        )
        pattern = _make_pattern()
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        locator_dim = next(d for d in result.dimensions if d.name == "locator_reliability")
        assert 0.4 <= locator_dim.score <= 0.9


class TestConfidenceScorerStability:
    def test_single_instance_gets_default_stability(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID)
        pattern = _make_pattern(instance_count=1, support=1)
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        stability_dim = next(d for d in result.dimensions if d.name == "stability")
        assert stability_dim.score == 0.5

    def test_multiple_instances_measures_stability(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID)
        pattern = _make_pattern(instance_count=5, support=5)
        ops = _make_operations(20)

        result = scorer.score_flow(flow, ops, pattern)
        stability_dim = next(d for d in result.dimensions if d.name == "stability")
        assert 0.0 <= stability_dim.score <= 1.0


class TestConfidenceScorerAnomaly:
    def test_too_few_steps_penalized(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID, step_count=2)
        pattern = _make_pattern()
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        anomaly_dim = next(d for d in result.dimensions if d.name == "anomaly_detection")
        assert anomaly_dim.score < 0.8

    def test_normal_step_count_no_penalty(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID, step_count=5)
        pattern = _make_pattern()
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        anomaly_dim = next(d for d in result.dimensions if d.name == "anomaly_detection")
        assert anomaly_dim.score >= 0.7


class TestConfidenceScorerReliability:
    def test_reliable_when_overall_above_threshold(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.ACCESSIBILITY_ID, step_count=5)
        pattern = _make_pattern(instance_count=5, support=5)
        ops = _make_operations(20)

        result = scorer.score_flow(flow, ops, pattern)
        if result.overall_confidence >= 0.7:
            assert result.is_reliable is True
        else:
            assert result.is_reliable is False

    def test_suggestions_generated_for_low_scores(self):
        scorer = ConfidenceScorer()
        flow = _make_flow_with_strategy(LocateStrategy.POSITION, step_count=2)
        pattern = _make_pattern(instance_count=1, support=1)
        ops = _make_operations()

        result = scorer.score_flow(flow, ops, pattern)
        assert isinstance(result.suggestions, list)
