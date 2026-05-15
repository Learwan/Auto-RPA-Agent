from src.analyzer.checkpoint_synthesizer import CheckpointSynthesizer
from src.models.automation import AutomationStep, LocateStrategy, StepTarget, StepType


def test_position_only_step_still_gets_stability_checkpoints():
    step = AutomationStep(
        id="step-1",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(
            strategy=LocateStrategy.POSITION,
            position={"x": 120, "y": 240},
            window_title="编辑商品",
        ),
        metadata={"locator_requires_confirmation": True},
        description="点击保存",
    )

    synthesizer = CheckpointSynthesizer()

    preconditions, postconditions = synthesizer._synthesize_for_step(step)

    assert [condition.field for condition in preconditions] == ["window_exists", "page_stable"]
    assert [item["field"] for item in postconditions] == ["page_stable"]
