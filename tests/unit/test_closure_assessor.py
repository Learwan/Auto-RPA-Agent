from src.analyzer.closure_assessor import FlowClosureAssessor
from src.models.automation import AutomationFlow, AutomationStep, LocateStrategy, StepCondition, StepTarget, StepType


def _make_certified_flow() -> AutomationFlow:
    step = AutomationStep(
        id="step-1",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="save-button",
            title="保存",
            text_contains="保存",
            role="button",
            class_name="AXButton",
            window_title="编辑商品",
            expected_attributes={"functional_label": "保存按钮"},
        ),
        preconditions=[StepCondition(field="window_exists", operator="truthy", value=True, timeout_ms=5000)],
        metadata={
            "postconditions": [
                {
                    "field": "page_stable",
                    "operator": "truthy",
                    "value": True,
                    "timeout_ms": 2500,
                }
            ]
        },
        description="点击保存按钮",
    )
    return AutomationFlow(id="flow-certified", name="Certified", steps=[step], confidence=0.76)


def test_assess_marks_certified_flow_ready():
    assessor = FlowClosureAssessor()

    assessment = assessor.assess(_make_certified_flow())

    assert assessment.ready is True
    assert assessment.status == "certified"
    assert assessment.metrics["certified_ratio"] == 1.0
    assert assessment.metrics["checkpoint_ratio"] == 1.0


def test_assess_blocks_position_only_flow():
    assessor = FlowClosureAssessor()
    flow = AutomationFlow(
        id="flow-weak",
        name="Weak",
        steps=[
            AutomationStep(
                id="step-weak-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(
                    strategy=LocateStrategy.POSITION,
                    position={"x": 100, "y": 200},
                    window_title="编辑商品",
                ),
                preconditions=[StepCondition(field="page_stable", operator="truthy", value=True, timeout_ms=2500)],
                metadata={
                    "postconditions": [
                        {
                            "field": "page_stable",
                            "operator": "truthy",
                            "value": True,
                            "timeout_ms": 2500,
                        }
                    ]
                },
                description="点击坐标",
            )
        ],
        confidence=0.8,
    )

    assessment = assessor.assess(flow)

    assert assessment.ready is False
    assert assessment.status == "needs_review"
    assert any(issue.code == "position_only_target" for issue in assessment.issues)


def test_assess_accepts_reinforced_text_match_without_confirmation_issue():
    assessor = FlowClosureAssessor()
    flow = AutomationFlow(
        id="flow-text",
        name="Reinforced Text",
        steps=[
            AutomationStep(
                id="step-text-1",
                type=StepType.CLICK,
                action={"button": "left"},
                target=StepTarget(
                    strategy=LocateStrategy.TEXT_MATCH,
                    title="保存按钮",
                    text_contains="保存按钮",
                    role="button",
                    class_name="AXButton",
                    window_title="编辑商品",
                    expected_attributes={
                        "functional_label": "保存按钮",
                        "description": "保存当前记录",
                    },
                ),
                preconditions=[StepCondition(field="window_exists", operator="truthy", value=True, timeout_ms=5000)],
                metadata={
                    "postconditions": [
                        {
                            "field": "page_stable",
                            "operator": "truthy",
                            "value": True,
                            "timeout_ms": 2500,
                        }
                    ]
                },
                description="点击保存按钮",
            )
        ],
        confidence=0.82,
    )

    assessment = assessor.assess(flow)

    assert assessment.ready is True
    assert not any(issue.code == "locator_requires_confirmation" for issue in assessment.issues)


def test_locator_requires_confirmation_ignores_stale_true_metadata_when_target_is_reinforced():
    step = AutomationStep(
        id="step-text-stale",
        type=StepType.CLICK,
        action={"button": "left"},
        target=StepTarget(
            strategy=LocateStrategy.TEXT_MATCH,
            title="SOP 自动生成",
            text_contains="SOP 自动生成",
            window_title="SOP 自动生成",
            expected_attributes={"app_name": "访达"},
        ),
        metadata={"locator_requires_confirmation": True, "locator_confirmed": False},
    )

    assert step.locator_requires_confirmation() is False
