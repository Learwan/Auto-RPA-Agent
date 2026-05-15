from src.analyzer.step_confidence import StepConfidenceScore


def test_default_initialization():
    score = StepConfidenceScore()
    assert score.selector_stability == 0.5
    assert score.semantic_relevance == 0.5
    assert score.structural_confidence == 0.5
    assert score.historical_success_rate == 0.5


def test_custom_initialization():
    score = StepConfidenceScore(
        selector_stability=0.9,
        semantic_relevance=0.8,
        structural_confidence=0.7,
        historical_success_rate=0.6,
    )
    assert score.selector_stability == 0.9
    assert score.semantic_relevance == 0.8
    assert score.structural_confidence == 0.7
    assert score.historical_success_rate == 0.6


def test_composite_all_max():
    score = StepConfidenceScore(1.0, 1.0, 1.0, 1.0)
    assert score.composite == 1.0


def test_composite_all_min():
    score = StepConfidenceScore(0.0, 0.0, 0.0, 0.0)
    assert score.composite == 0.0


def test_composite_weighted():
    score = StepConfidenceScore(1.0, 0.0, 0.0, 0.0)
    expected = 1.0 * 0.30
    assert abs(score.composite - expected) < 1e-9


def test_level_high():
    score = StepConfidenceScore(0.9, 0.9, 0.9, 0.9)
    assert score.level == "HIGH"


def test_level_medium():
    score = StepConfidenceScore(0.7, 0.7, 0.7, 0.7)
    assert score.level == "MEDIUM"


def test_level_low():
    score = StepConfidenceScore(0.4, 0.4, 0.4, 0.4)
    assert score.level == "LOW"


def test_level_critical():
    score = StepConfidenceScore(0.2, 0.2, 0.2, 0.2)
    assert score.level == "CRITICAL"


def test_level_boundary_high():
    score = StepConfidenceScore(0.85, 0.85, 0.85, 0.85)
    assert score.level == "HIGH"


def test_level_boundary_medium():
    score = StepConfidenceScore(0.60, 0.60, 0.60, 0.60)
    assert score.level == "MEDIUM"


def test_level_boundary_low():
    score = StepConfidenceScore(0.35, 0.35, 0.35, 0.35)
    assert score.level == "LOW"


def test_to_dict():
    score = StepConfidenceScore(0.85, 0.75, 0.65, 0.55)
    data = score.to_dict()

    assert data["selector_stability"] == 0.85
    assert data["semantic_relevance"] == 0.75
    assert data["structural_confidence"] == 0.65
    assert data["historical_success_rate"] == 0.55
    assert "composite" in data
    assert "level" in data
    assert all(isinstance(v, (int, float, str)) for v in data.values())


def test_from_dict():
    data = {
        "selector_stability": 0.85,
        "semantic_relevance": 0.75,
        "structural_confidence": 0.65,
        "historical_success_rate": 0.55,
    }
    score = StepConfidenceScore.from_dict(data)

    assert score.selector_stability == 0.85
    assert score.semantic_relevance == 0.75
    assert score.structural_confidence == 0.65
    assert score.historical_success_rate == 0.55


def test_from_dict_missing_fields():
    data = {"selector_stability": 0.85, "semantic_relevance": 0.75}
    score = StepConfidenceScore.from_dict(data)

    assert score.selector_stability == 0.85
    assert score.semantic_relevance == 0.75
    assert score.structural_confidence == 0.5
    assert score.historical_success_rate == 0.5


def test_roundtrip_dict():
    original = StepConfidenceScore(0.85, 0.75, 0.65, 0.55)
    data = original.to_dict()
    restored = StepConfidenceScore.from_dict(data)

    assert restored.selector_stability == original.selector_stability
    assert restored.semantic_relevance == original.semantic_relevance
    assert restored.structural_confidence == original.structural_confidence
    assert restored.historical_success_rate == original.historical_success_rate


def test_import_from_package():
    from src.analyzer import StepConfidenceScore as PkgScore

    score = PkgScore(0.9, 0.9, 0.9, 0.9)
    assert score.level == "HIGH"
