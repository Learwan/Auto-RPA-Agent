from src.analyzer.bt_generator import BehaviorTreeGenerator, BTNode, BTNodeType, DecoratorType
from src.models.automation import AutomationStep, ErrorAction, LocateStrategy, StepCondition, StepTarget, StepType


def _make_step(
    step_type: StepType = StepType.CLICK,
    retry_count: int = 3,
    on_error: ErrorAction = ErrorAction.RETRY,
    condition: StepCondition | None = None,
) -> AutomationStep:
    return AutomationStep(
        id=f"step-{_make_step._counter}",
        type=step_type,
        action={"button": "left"},
        target=StepTarget(strategy=LocateStrategy.POSITION, position={"x": 100, "y": 200}),
        on_error=on_error,
        retry_count=retry_count,
        condition=condition,
    )


_make_step._counter = 0


def _make_steps(count: int, step_type: StepType = StepType.CLICK) -> list[AutomationStep]:
    steps = []
    for i in range(count):
        _make_step._counter = i
        steps.append(_make_step(step_type))
    _make_step._counter = count
    return steps


class TestBTGeneratorBasic:
    def test_empty_steps_creates_empty_root(self):
        gen = BehaviorTreeGenerator()
        root = gen.generate([])
        assert root.node_type == BTNodeType.SEQUENCE
        assert len(root.children) == 0

    def test_single_action_step(self):
        gen = BehaviorTreeGenerator()
        steps = [_make_step(retry_count=1, on_error=ErrorAction.ABORT)]
        root = gen.generate(steps)
        assert len(root.children) == 1
        assert root.children[0].node_type == BTNodeType.ACTION

    def test_multiple_action_steps(self):
        gen = BehaviorTreeGenerator()
        steps = [_make_step(retry_count=1, on_error=ErrorAction.ABORT) for _ in range(5)]
        root = gen.generate(steps)
        assert len(root.children) == 5


class TestBTGeneratorRetryDecorator:
    def test_high_retry_creates_decorator(self):
        gen = BehaviorTreeGenerator()
        steps = [_make_step(retry_count=5, on_error=ErrorAction.RETRY)]
        root = gen.generate(steps)
        assert len(root.children) == 1
        child = root.children[0]
        assert child.node_type == BTNodeType.DECORATOR
        assert child.decorator_type == DecoratorType.RETRY

    def test_low_retry_creates_action(self):
        gen = BehaviorTreeGenerator()
        steps = [_make_step(retry_count=1, on_error=ErrorAction.RETRY)]
        root = gen.generate(steps)
        assert len(root.children) == 1
        assert root.children[0].node_type == BTNodeType.ACTION


class TestBTNodeSerialization:
    def test_to_dict_and_from_dict(self):
        node = BTNode(
            node_type=BTNodeType.SEQUENCE,
            label="test_root",
            children=[
                BTNode(node_type=BTNodeType.ACTION, label="action_1"),
                BTNode(node_type=BTNodeType.CONDITION, condition="window_active", label="cond_1"),
            ],
        )
        d = node.to_dict()
        assert d["node_type"] == "sequence"
        assert len(d["children"]) == 2

        restored = BTNode.from_dict(d)
        assert restored.node_type == BTNodeType.SEQUENCE
        assert len(restored.children) == 2
        assert restored.children[0].node_type == BTNodeType.ACTION
        assert restored.children[1].condition == "window_active"

    def test_decorator_to_dict(self):
        node = BTNode(
            node_type=BTNodeType.DECORATOR,
            decorator_type=DecoratorType.RETRY,
            decorator_params={"max_attempts": 3},
            children=[BTNode(node_type=BTNodeType.ACTION, label="retry_action")],
            label="retry_3",
        )
        d = node.to_dict()
        assert d["decorator_type"] == "retry"
        assert d["decorator_params"]["max_attempts"] == 3


class TestBTGeneratorVariables:
    def test_extract_variables_from_type_steps(self):
        gen = BehaviorTreeGenerator()
        steps = [
            AutomationStep(
                id="step-0",
                type=StepType.TYPE,
                action={"text": "${input_text_1}"},
                target=StepTarget(),
            ),
        ]
        root = gen.generate(steps)
        variables = gen.extract_variables(root)
        assert len(variables) == 1
        assert variables[0]["name"] == "input_text_1"

    def test_no_variables_for_click_steps(self):
        gen = BehaviorTreeGenerator()
        steps = _make_steps(3, StepType.CLICK)
        root = gen.generate(steps)
        variables = gen.extract_variables(root)
        assert len(variables) == 0
