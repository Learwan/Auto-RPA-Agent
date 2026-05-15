import json

from src.analyzer.preprocessor import NormalizedOperation
from src.analyzer.script_generator import WAIT_INSERT_THRESHOLD_MS, ScriptGenerator
from src.models.automation import (
    DetectedPattern,
    ErrorAction,
    LocateStrategy,
    StepType,
)


def _make_ops(count: int, base_ts: int = 1000, gap: int = 500) -> list[NormalizedOperation]:
    ops = []
    for i in range(count):
        ops.append(
            NormalizedOperation(
                op_type="mouse_click",
                data={
                    "x": 100 + i * 50,
                    "y": 200,
                    "button": "left",
                    "scroll_dx": 0,
                    "scroll_dy": 0,
                    "drag_start": None,
                },
                timestamp=base_ts + i * gap,
                seq_num=i,
                context={"platform": "desktop", "active_window": {"title": "TestApp"}},
            )
        )
    return ops


def _make_pattern_with_ops(op_count: int = 3, support: int = 2) -> DetectedPattern:
    return DetectedPattern(
        id="pat-1",
        pattern_sequence=[StepType.CLICK] * op_count,
        instances=[{"start_idx": i * op_count, "end_idx": (i + 1) * op_count - 1} for i in range(support)],
        support=support,
        avg_duration_ms=1000,
        confidence=0.8,
    )


class TestScriptGeneratorPatternToFlow:
    def test_generates_flow_from_pattern(self):
        generator = ScriptGenerator()
        ops = _make_ops(6)
        pattern = _make_pattern_with_ops(3, 2)

        flows = generator.generate([pattern], ops)
        assert len(flows) == 1
        assert len(flows[0].steps) > 0

    def test_flow_name_contains_pattern_info(self):
        generator = ScriptGenerator()
        ops = _make_ops(6)
        pattern = _make_pattern_with_ops(3, 2)

        flows = generator.generate([pattern], ops)
        assert "Auto-" in flows[0].name

    def test_empty_pattern_returns_no_flow(self):
        generator = ScriptGenerator()
        ops = _make_ops(3)
        pattern = DetectedPattern(
            id="pat-empty",
            pattern_sequence=[],
            instances=[],
            support=0,
        )

        flows = generator.generate([pattern], ops)
        assert len(flows) == 0


class TestScriptGeneratorDirectFlow:
    def test_generates_direct_flow(self):
        generator = ScriptGenerator()
        ops = _make_ops(5)

        flow = generator.generate_direct(ops)
        assert flow is not None
        assert len(flow.steps) > 0
        assert "Direct" in flow.name

    def test_direct_flow_empty_ops_returns_none(self):
        generator = ScriptGenerator()
        flow = generator.generate_direct([])
        assert flow is None


class TestScriptGeneratorWaitInference:
    def test_infers_wait_step_on_large_gap(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 100, "y": 200, "button": "left", "scroll_dx": 0, "scroll_dy": 0, "drag_start": None},
                timestamp=1000,
                seq_num=0,
                context={"platform": "desktop"},
            ),
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 200, "y": 300, "button": "left", "scroll_dx": 0, "scroll_dy": 0, "drag_start": None},
                timestamp=1000 + WAIT_INSERT_THRESHOLD_MS + 500,
                seq_num=1,
                context={"platform": "desktop"},
            ),
        ]

        flow = generator.generate_direct(ops)
        assert flow is not None
        wait_steps = [s for s in flow.steps if s.type == StepType.WAIT]
        assert len(wait_steps) >= 1

    def test_no_wait_step_on_small_gap(self):
        generator = ScriptGenerator()
        ops = _make_ops(3, gap=500)

        flow = generator.generate_direct(ops)
        assert flow is not None
        wait_steps = [s for s in flow.steps if s.type == StepType.WAIT]
        assert len(wait_steps) == 0


class TestScriptGeneratorStepMapping:
    def test_type_text_creates_type_step(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="type_text", data={"text": "hello"}, timestamp=1000, seq_num=0, context={"platform": "desktop"}
            ),
        ]

        flow = generator.generate_direct(ops)
        assert flow is not None
        assert any(s.type == StepType.TYPE for s in flow.steps)

    def test_hotkey_creates_hotkey_step(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="hotkey",
                data={"key": "c", "modifiers": ["ctrl"]},
                timestamp=1000,
                seq_num=0,
                context={"platform": "desktop"},
            ),
        ]

        flow = generator.generate_direct(ops)
        assert flow is not None
        assert any(s.type == StepType.HOTKEY for s in flow.steps)

    def test_switch_window_creates_switch_step(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="switch_window",
                data={"app_name": "Chrome", "title": "Google"},
                timestamp=1000,
                seq_num=0,
                context={"platform": "desktop"},
            ),
        ]

        flow = generator.generate_direct(ops)
        assert flow is not None
        switch_steps = [s for s in flow.steps if s.type == StepType.SWITCH_WINDOW]
        assert len(switch_steps) == 1
        assert switch_steps[0].target.expected_attributes == {"app_name": "Chrome"}
        assert switch_steps[0].metadata["locator_requires_confirmation"] is False
        assert switch_steps[0].on_error != ErrorAction.ASK_USER

    def test_text_match_recording_marks_locator_for_confirmation(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 180, "y": 220, "button": "left", "scroll_dx": 0, "scroll_dy": 0, "drag_start": None},
                timestamp=1000,
                seq_num=0,
                context={
                    "platform": "desktop",
                    "focused_element": {
                        "title": "保存",
                        "role": "button",
                    },
                    "active_window": {"title": "Example App"},
                },
            ),
        ]

        flow = generator.generate_direct(ops)

        assert flow is not None
        click_steps = [s for s in flow.steps if s.type == StepType.CLICK]
        assert len(click_steps) == 1
        assert click_steps[0].target.strategy == LocateStrategy.TEXT_MATCH
        assert click_steps[0].metadata["locator_requires_confirmation"] is True
        assert click_steps[0].on_error == ErrorAction.ASK_USER
        assert click_steps[0].retry_count == 0
        assert all(s.type != StepType.CONDITION for s in flow.steps)

    def test_weak_title_uses_functional_label_for_text_match(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 180, "y": 220, "button": "left", "scroll_dx": 0, "scroll_dy": 0, "drag_start": None},
                timestamp=1000,
                seq_num=0,
                context={
                    "platform": "desktop",
                    "focused_element": {
                        "role": "button",
                        "functional_label": "保存按钮",
                        "description": "保存当前记录",
                        "class_name": "AXButton",
                    },
                    "active_window": {"title": "Example App", "app_name": "Example App"},
                },
            ),
        ]

        flow = generator.generate_direct(ops)

        assert flow is not None
        click_steps = [s for s in flow.steps if s.type == StepType.CLICK]
        assert len(click_steps) == 1
        assert click_steps[0].target.strategy == LocateStrategy.TEXT_MATCH
        assert click_steps[0].target.text_contains == "保存按钮"
        assert click_steps[0].target.expected_attributes == {
            "description": "保存当前记录",
            "functional_label": "保存按钮",
        }
        assert click_steps[0].metadata["locator_requires_confirmation"] is False
        assert click_steps[0].on_error != ErrorAction.ASK_USER

    def test_prefers_meaningful_value_over_generic_functional_label(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 180, "y": 220, "button": "left", "scroll_dx": 0, "scroll_dy": 0, "drag_start": None},
                timestamp=1000,
                seq_num=0,
                context={
                    "platform": "desktop",
                    "focused_element": {
                        "role": "AXStaticText",
                        "functional_label": "文本",
                        "value": "AP80x Arc项目管理沟通群",
                        "class_name": "文本",
                    },
                    "active_window": {"title": "企业微信", "app_name": "企业微信"},
                },
            ),
        ]

        flow = generator.generate_direct(ops)

        assert flow is not None
        click_steps = [s for s in flow.steps if s.type == StepType.CLICK]
        assert len(click_steps) == 1
        assert click_steps[0].target.strategy == LocateStrategy.TEXT_MATCH
        assert click_steps[0].target.text_contains == "AP80x Arc项目管理沟通群"
        assert click_steps[0].target.window_title == "企业微信"
        assert click_steps[0].target.expected_attributes == {
            "value": "AP80x Arc项目管理沟通群",
            "functional_label": "文本",
        }
        assert click_steps[0].metadata["locator_requires_confirmation"] is False

    def test_uses_persisted_node_suggestion_to_upgrade_weak_click_target(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 320, "y": 220, "button": "left", "scroll_dx": 0, "scroll_dy": 0, "drag_start": None},
                timestamp=1000,
                seq_num=0,
                context={
                    "platform": "desktop",
                    "screenshot_path": "snapshot://snap-visual-anchor-1",
                    "active_window": {"title": "企业微信", "app_name": "企业微信"},
                    "node_suggestion": {
                        "snapshot_id": "snap-visual-anchor-1",
                        "vision_source": "local_vlm",
                        "confidence": 0.82,
                        "confidence_reason": "缺少本地焦点元素，已改用视觉识别结果生成节点建议。",
                        "target": {
                            "strategy": "text_match",
                            "text_contains": "AP80x Arc项目管理沟通群",
                            "role": "AXStaticText",
                            "class_name": "文本",
                            "window_title": "企业微信",
                            "vision_element": {
                                "label": "AP80x Arc项目管理沟通群",
                                "element_type": "text",
                            },
                        },
                    },
                },
            ),
        ]

        flow = generator.generate_direct(ops)

        assert flow is not None
        click_steps = [s for s in flow.steps if s.type == StepType.CLICK]
        assert len(click_steps) == 1
        assert click_steps[0].target.strategy == LocateStrategy.TEXT_MATCH
        assert click_steps[0].target.text_contains == "AP80x Arc项目管理沟通群"
        assert click_steps[0].target.window_title == "企业微信"
        assert click_steps[0].target.expected_attributes == {
            "snapshot_id": "snap-visual-anchor-1",
            "screenshot_path": "snapshot://snap-visual-anchor-1",
            "vision_source": "local_vlm",
            "vision_confidence": 0.82,
            "vision_reason": "缺少本地焦点元素，已改用视觉识别结果生成节点建议。",
            "vision_label": "AP80x Arc项目管理沟通群",
            "vision_element_type": "text",
        }
        assert click_steps[0].metadata["locator_requires_confirmation"] is False


class TestScriptGeneratorVariableExtraction:
    def test_extracts_text_variables(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="type_text",
                data={"text": "long input text here"},
                timestamp=1000,
                seq_num=0,
                context={"platform": "desktop"},
            ),
        ]

        flow = generator.generate_direct(ops)
        assert flow is not None
        assert len(flow.variables) >= 1
        assert flow.variables[0].var_type == "string"

    def test_short_text_not_extracted(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="type_text", data={"text": "ab"}, timestamp=1000, seq_num=0, context={"platform": "desktop"}
            ),
        ]

        flow = generator.generate_direct(ops)
        assert flow is not None
        text_vars = [v for v in flow.variables if v.name.startswith("input_text")]
        assert len(text_vars) == 0


class TestScriptGeneratorExport:
    def test_export_json_produces_valid_json(self):
        generator = ScriptGenerator()
        ops = _make_ops(3)

        flow = generator.generate_direct(ops)
        assert flow is not None

        json_str = generator.export_json(flow)
        parsed = json.loads(json_str)
        assert "steps" in parsed
        assert "id" in parsed

    def test_export_python_produces_script(self):
        generator = ScriptGenerator()
        ops = _make_ops(3)

        flow = generator.generate_direct(ops)
        assert flow is not None

        python_str = generator.export_python(flow)
        assert "import" in python_str
        assert "def execute_" in python_str


class TestScriptGeneratorWebContext:
    def test_web_context_uses_css_selector(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 100, "y": 200, "button": "left", "scroll_dx": 0, "scroll_dy": 0, "drag_start": None},
                timestamp=1000,
                seq_num=0,
                context={
                    "platform": "web",
                    "focused_element": {
                        "selector": "#submit-btn",
                        "role": "button",
                        "title": "Submit",
                    },
                    "active_window": {"title": "Web App", "url": "https://example.com"},
                },
            ),
        ]

        flow = generator.generate_direct(ops)
        assert flow is not None
        click_steps = [s for s in flow.steps if s.type == StepType.CLICK]
        assert len(click_steps) >= 1
        assert click_steps[0].target.strategy == LocateStrategy.CSS_SELECTOR
        assert click_steps[0].target.selector == "#submit-btn"

    def test_switch_window_falls_back_to_app_name_when_title_missing(self):
        generator = ScriptGenerator()
        ops = [
            NormalizedOperation(
                op_type="switch_window",
                data={"app_name": "企业微信", "title": "", "url": None},
                timestamp=1000,
                seq_num=0,
                context={"platform": "desktop"},
            ),
        ]

        flow = generator.generate_direct(ops)

        assert flow is not None
        switch_steps = [s for s in flow.steps if s.type == StepType.SWITCH_WINDOW]
        assert len(switch_steps) == 1
        assert switch_steps[0].target.strategy == LocateStrategy.TEXT_MATCH
        assert switch_steps[0].target.title == "企业微信"
