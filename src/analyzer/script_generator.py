import json
import uuid

from src.analyzer.preprocessor import NormalizedOperation
from src.analyzer.step_confidence import StepConfidenceScore
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    DetectedPattern,
    ErrorAction,
    ErrorHandlingPolicy,
    FlowVariable,
    LocateStrategy,
    StepCondition,
    StepTarget,
    StepType,
)
from src.models.behavior_tree import BehaviorTreeModel, BTNodeModel, BTNodeType, DecoratorType

STEP_TYPE_DEFAULTS = {
    StepType.CLICK: {"delay": 300, "on_error": ErrorAction.RETRY, "retry_count": 3},
    StepType.TYPE: {"delay": 100, "on_error": ErrorAction.RETRY, "retry_count": 2},
    StepType.HOTKEY: {"delay": 200, "on_error": ErrorAction.RETRY, "retry_count": 2},
    StepType.SCROLL: {"delay": 300, "on_error": ErrorAction.SKIP, "retry_count": 1},
    StepType.DRAG: {"delay": 500, "on_error": ErrorAction.RETRY, "retry_count": 2},
    StepType.SWITCH_WINDOW: {"delay": 1000, "on_error": ErrorAction.RETRY, "retry_count": 3},
    StepType.FILE_OP: {"delay": 500, "on_error": ErrorAction.ABORT, "retry_count": 1},
    StepType.NAVIGATE: {"delay": 0, "on_error": ErrorAction.RETRY, "retry_count": 2},
    StepType.UPLOAD_FILE: {"delay": 300, "on_error": ErrorAction.RETRY, "retry_count": 2},
    StepType.DOWNLOAD_FILE: {"delay": 500, "on_error": ErrorAction.RETRY, "retry_count": 2},
    StepType.WAIT: {"delay": 0, "on_error": ErrorAction.SKIP, "retry_count": 0},
    StepType.CONDITION: {"delay": 0, "on_error": ErrorAction.SKIP, "retry_count": 0},
    StepType.LOOP: {"delay": 0, "on_error": ErrorAction.ABORT, "retry_count": 0},
}

WAIT_INSERT_THRESHOLD_MS = 1500
MAX_INFERRED_WAIT_MS = 10000
CONDITION_WAIT_INSERT_TYPES = {StepType.CLICK, StepType.TYPE, StepType.NAVIGATE, StepType.SWITCH_WINDOW}
CONDITION_WAIT_TIMEOUT_MS = 5000

STRATEGY_WEIGHTS = {
    LocateStrategy.ACCESSIBILITY_ID: 0.95,
    LocateStrategy.CSS_SELECTOR: 0.90,
    LocateStrategy.XPATH: 0.85,
    LocateStrategy.TEXT_MATCH: 0.75,
    LocateStrategy.IMAGE_MATCH: 0.70,
    LocateStrategy.POSITION: 0.40,
}


class ScriptGenerator:
    def __init__(self):
        self._execution_history: list[dict] = []

    def generate(self, patterns: list[DetectedPattern], operations: list[NormalizedOperation]) -> list[AutomationFlow]:
        flows = []
        for pattern in patterns:
            flow = self._pattern_to_flow(pattern, operations)
            if flow:
                flows.append(flow)
        return flows

    def generate_direct(self, operations: list[NormalizedOperation]) -> AutomationFlow | None:
        if not operations:
            return None
        steps = self._create_steps(operations)
        if not steps:
            return None
        variables = self._extract_variables(steps, operations)
        step_scores = [self._compute_step_confidence(s) for s in steps]
        flow_confidence = self._aggregate_flow_confidence(step_scores)
        risk_areas = [
            s.description or s.id
            for s, sc in zip(steps, step_scores, strict=False)
            if sc.level in ("LOW", "CRITICAL")
        ]

        return AutomationFlow(
            id=str(uuid.uuid4()),
            name=f"Direct-{len(operations)}steps",
            description=f"Direct flow from {len(operations)} operations",
            steps=steps,
            variables=variables,
            error_handling=ErrorHandlingPolicy(),
            confidence=flow_confidence,
            metadata={
                "step_scores": [sc.to_dict() for sc in step_scores],
                "risk_areas": risk_areas,
            },
        )

    def _pattern_to_flow(
        self, pattern: DetectedPattern, operations: list[NormalizedOperation]
    ) -> AutomationFlow | None:
        if not pattern.instances:
            return None
        first_instance = pattern.instances[0]
        start_idx = first_instance["start_idx"]
        end_idx = first_instance["end_idx"]

        instance_ops = operations[start_idx : end_idx + 1]
        steps = self._create_steps(instance_ops)
        variables = self._extract_variables(steps, instance_ops)

        source_pattern = pattern.model_dump()
        if source_pattern.get("instances") and len(source_pattern["instances"]) > 3:
            source_pattern["instances"] = source_pattern["instances"][:3]
            source_pattern["instances_truncated"] = True

        return AutomationFlow(
            id=str(uuid.uuid4()),
            name=f"Auto-{pattern.pattern_type}-{len(pattern.pattern_sequence)}steps",
            description=f"Automated flow from pattern (support={pattern.support}, confidence={pattern.confidence:.2f})",
            steps=steps,
            variables=variables,
            error_handling=ErrorHandlingPolicy(),
            confidence=pattern.confidence,
            source_pattern=source_pattern,
        )

    def _create_steps(self, operations: list[NormalizedOperation]) -> list[AutomationStep]:
        steps = []
        previous_op: NormalizedOperation | None = None
        for i, op in enumerate(operations):
            if previous_op is not None:
                wait_step = self._build_inferred_wait_step(previous_op, op)
                if wait_step is not None:
                    steps.append(wait_step)
            step = self._op_to_step(op, i)
            if step:
                condition_step = self._build_condition_wait_step(step, steps)
                if condition_step is not None:
                    steps.append(condition_step)
                steps.append(step)
            previous_op = op
        return steps

    def _build_inferred_wait_step(
        self,
        previous_op: NormalizedOperation,
        current_op: NormalizedOperation,
    ) -> AutomationStep | None:
        gap_ms = current_op.timestamp - previous_op.timestamp
        if gap_ms < WAIT_INSERT_THRESHOLD_MS:
            return None

        wait_ms = min(gap_ms, MAX_INFERRED_WAIT_MS)
        wait_seconds = round(wait_ms / 1000.0, 2)
        return AutomationStep(
            id=str(uuid.uuid4()),
            type=StepType.WAIT,
            action={"duration": wait_seconds},
            target=StepTarget(),
            execution_order=None,
            delay=0,
            on_error=ErrorAction.SKIP,
            retry_count=0,
            description=f"Inferred wait for UI response ({gap_ms}ms)",
            metadata={"source": "inferred_wait", "gap_ms": gap_ms},
        )

    def _build_condition_wait_step(
        self,
        step: AutomationStep,
        existing_steps: list[AutomationStep],
    ) -> AutomationStep | None:
        if step.type not in CONDITION_WAIT_INSERT_TYPES:
            return None
        if step.locator_requires_confirmation():
            return None
        target = step.target
        if target is None:
            return None

        has_semantic_locator = bool(
            target.selector or target.xpath
            or target.accessibility_id or target.title
        )
        has_position = target.position is not None

        if not has_semantic_locator and not has_position:
            return None

        if target.strategy == LocateStrategy.POSITION and not has_semantic_locator:
            condition = StepCondition(
                field="page_stable",
                operator="truthy",
                value=True,
                timeout_ms=CONDITION_WAIT_TIMEOUT_MS,
            )
            return AutomationStep(
                id=str(uuid.uuid4()),
                type=StepType.CONDITION,
                action={"check": "page_stable"},
                target=StepTarget(),
                execution_order=None,
                condition=condition,
                delay=0,
                on_error=ErrorAction.SKIP,
                retry_count=0,
                description=f"Wait for page stability before {step.type.value}",
                metadata={"source": "condition_wait", "for_step_id": step.id},
            )

        condition = StepCondition(
            field="element_exists",
            operator="truthy",
            value=True,
            timeout_ms=CONDITION_WAIT_TIMEOUT_MS,
        )
        return AutomationStep(
            id=str(uuid.uuid4()),
            type=StepType.CONDITION,
            action={"check": "element_exists"},
            target=target.model_copy(),
            execution_order=None,
            condition=condition,
            delay=0,
            on_error=ErrorAction.SKIP,
            retry_count=0,
            description=f"Wait for element before {step.type.value}",
            metadata={"source": "condition_wait", "for_step_id": step.id},
        )

    def _compute_step_confidence(self, step: AutomationStep) -> StepConfidenceScore:
        selector_stability = self._evaluate_selector_stability(step)
        semantic_relevance = self._evaluate_semantic_relevance(step)
        structural_confidence = self._evaluate_structural_confidence(step)
        historical_success_rate = self._evaluate_historical_success_rate(step)
        return StepConfidenceScore(
            selector_stability=selector_stability,
            semantic_relevance=semantic_relevance,
            structural_confidence=structural_confidence,
            historical_success_rate=historical_success_rate,
        )

    def _evaluate_selector_stability(self, step: AutomationStep) -> float:
        target = step.target
        if target is None:
            return 0.2
        strategy = target.strategy
        base = STRATEGY_WEIGHTS.get(strategy, 0.4)
        multi_locator_bonus = 0.0
        locator_count = sum(1 for attr in [
            target.selector, target.xpath, target.accessibility_id,
            target.title, target.text_contains,
        ] if attr)
        if locator_count >= 3:
            multi_locator_bonus = 0.15
        elif locator_count >= 2:
            multi_locator_bonus = 0.08
        position_bonus = 0.0
        if target.position and strategy == LocateStrategy.POSITION:
            position_bonus = 0.10
        return min(1.0, base + multi_locator_bonus + position_bonus)

    def _evaluate_semantic_relevance(self, step: AutomationStep) -> float:
        target = step.target
        if target is None:
            return 0.3
        score = 0.5
        if target.title:
            score += 0.15
        if target.role:
            score += 0.10
        if target.selector or target.xpath:
            score += 0.10
        if step.description and len(step.description) > 10:
            score += 0.05
        if step.type in (StepType.CLICK, StepType.TYPE, StepType.NAVIGATE):
            score += 0.10
        return min(1.0, score)

    def _evaluate_structural_confidence(self, step: AutomationStep) -> float:
        score = 0.5
        if step.type in STEP_TYPE_DEFAULTS:
            score += 0.15
        if step.on_error != ErrorAction.ABORT:
            score += 0.10
        if step.retry_count >= 2:
            score += 0.10
        if step.preconditions:
            score += 0.10
        if step.depends_on:
            score += 0.05
        return min(1.0, score)

    def _evaluate_historical_success_rate(self, step: AutomationStep) -> float:
        if not self._execution_history:
            return 0.5
        matching = [
            r for r in self._execution_history
            if r.get("step_type") == step.type.value
        ]
        if not matching:
            return 0.5
        successes = sum(1 for r in matching if r.get("success", False))
        return successes / len(matching)

    @staticmethod
    def _aggregate_flow_confidence(scores: list[StepConfidenceScore]) -> float:
        if not scores:
            return 0.5
        composites = [s.composite for s in scores]
        avg = sum(composites) / len(composites)
        min_score = min(composites)
        return round(avg * 0.7 + min_score * 0.3, 3)

    def record_execution_result(self, step_type: str, success: bool) -> None:
        self._execution_history.append({
            "step_type": step_type,
            "success": success,
        })
        if len(self._execution_history) > 500:
            self._execution_history = self._execution_history[-500:]

    def _op_to_step(self, op: NormalizedOperation, idx: int) -> AutomationStep | None:
        step_type = self._map_op_type(op.op_type)
        if not step_type:
            return None

        action = self._build_action(op, step_type)
        target = self._build_target(op, step_type)
        description = self._build_description(op, step_type, idx)
        defaults = STEP_TYPE_DEFAULTS.get(step_type, {"delay": 500, "on_error": ErrorAction.ABORT, "retry_count": 3})
        locator_requires_confirmation = target.inferred_requires_confirmation()
        on_error = defaults["on_error"]
        retry_count = defaults["retry_count"]

        if locator_requires_confirmation and step_type in {
            StepType.CLICK,
            StepType.TYPE,
            StepType.SCROLL,
            StepType.DRAG,
            StepType.SWITCH_WINDOW,
            StepType.UPLOAD_FILE,
            StepType.DOWNLOAD_FILE,
        }:
            on_error = ErrorAction.ASK_USER
            retry_count = 0

        return AutomationStep(
            id=str(uuid.uuid4()),
            type=step_type,
            action=action,
            target=target,
            execution_order=idx,
            delay=defaults["delay"],
            on_error=on_error,
            retry_count=retry_count,
            description=description,
            metadata={
                "source_op_type": op.op_type,
                "source_seq_num": op.seq_num,
                "source_timestamp": op.timestamp,
                "source_platform": (op.context or {}).get("platform"),
                "locator_requires_confirmation": locator_requires_confirmation,
                "locator_confirmed": not locator_requires_confirmation,
                "locator_stability": target.inferred_locator_stability(),
                "locator_strategy": target.strategy.value,
                "locator_confirmation_reason": self._build_locator_confirmation_reason(target),
            },
        )

    @staticmethod
    def _build_locator_confirmation_reason(target: StepTarget) -> str | None:
        if not target.inferred_requires_confirmation():
            return None
        if target.strategy == LocateStrategy.POSITION:
            return "录制结果主要依赖坐标，界面轻微变化就可能失效。"
        if target.strategy == LocateStrategy.TEXT_MATCH:
            return "录制结果主要依赖文本匹配，建议先人工确认目标文案和窗口状态。"
        if target.strategy == LocateStrategy.IMAGE_MATCH:
            return "录制结果主要依赖图像匹配，建议先人工确认当前界面与录制截图一致。"
        return "录制结果缺少稳定定位线索，建议执行前先确认。"

    def _map_op_type(self, op_type: str) -> StepType | None:
        mapping = {
            "mouse_click": StepType.CLICK,
            "mouse_right_click": StepType.CLICK,
            "mouse_double_click": StepType.CLICK,
            "mouse_scroll": StepType.SCROLL,
            "mouse_drag_end": StepType.DRAG,
            "type_text": StepType.TYPE,
            "hotkey": StepType.HOTKEY,
            "switch_window": StepType.SWITCH_WINDOW,
            "navigation": StepType.NAVIGATE,
            "upload_file": StepType.UPLOAD_FILE,
            "download_file": StepType.DOWNLOAD_FILE,
            "file_op": StepType.FILE_OP,
        }
        return mapping.get(op_type)

    def _build_action(self, op: NormalizedOperation, step_type: StepType) -> dict:
        action = {}
        if step_type == StepType.CLICK:
            action = {"button": op.data.get("button", "left"), "click_type": "single"}
            if op.op_type == "mouse_double_click":
                action["click_type"] = "double"
            elif op.op_type == "mouse_right_click":
                action["click_type"] = "right"
        elif step_type == StepType.TYPE:
            action = {"text": op.data.get("text", "")}
        elif step_type == StepType.HOTKEY:
            action = {"key": op.data.get("key", ""), "modifiers": op.data.get("modifiers", [])}
        elif step_type == StepType.SCROLL:
            action = {"delta": op.data.get("scroll_delta", 0)}
        elif step_type == StepType.DRAG:
            drag_start = op.data.get("drag_start")
            if drag_start:
                action = {
                    "start_x": drag_start.get("x", 0),
                    "start_y": drag_start.get("y", 0),
                    "end_x": op.data.get("x", 0),
                    "end_y": op.data.get("y", 0),
                }
        elif step_type == StepType.SWITCH_WINDOW:
            action = {
                "app_name": op.data.get("app_name", ""),
                "title": op.data.get("title", ""),
                "window_title": op.data.get("title", ""),
                "url": op.data.get("url"),
            }
        elif step_type == StepType.NAVIGATE:
            action = {"url": op.data.get("url"), "wait_until": "domcontentloaded"}
            transition = op.data.get("transition")
            from_url = op.data.get("from_url")
            from_title = op.data.get("from_title")
            same_document = bool(op.data.get("same_document", False))
            if transition:
                action["transition"] = transition
            if from_url:
                action["from_url"] = from_url
            if from_title:
                action["from_title"] = from_title
            if same_document:
                action["same_document"] = True
        elif step_type == StepType.UPLOAD_FILE:
            paths = op.data.get("paths") or ([op.data.get("src_path")] if op.data.get("src_path") else [])
            if len(paths) == 1:
                action = {"path": paths[0]}
            elif paths:
                action = {"file_paths": paths}
        elif step_type == StepType.DOWNLOAD_FILE:
            action = {"save_as": op.data.get("dest_path") or op.data.get("src_path")}
        elif step_type == StepType.FILE_OP:
            action = {"operation": op.data.get("operation", ""), "src_path": op.data.get("src_path", "")}
        return action

    def _build_description(self, op: NormalizedOperation, step_type: StepType, idx: int) -> str:
        if step_type != StepType.NAVIGATE:
            return f"Step {idx + 1}: {op.op_type}"

        transition = op.data.get("transition")
        from_title = op.data.get("from_title")
        from_url = op.data.get("from_url")
        to_title = op.data.get("title")
        to_url = op.data.get("url")
        same_document = bool(op.data.get("same_document", False))

        details = []
        if transition:
            details.append(f"via {transition}")
        if from_title or from_url:
            details.append(f"from {from_title or from_url}")
        if to_title or to_url:
            details.append(f"to {to_title or to_url}")
        if same_document:
            details.append("same-document")

        if not details:
            return f"Step {idx + 1}: {op.op_type}"
        return f"Step {idx + 1}: navigation ({', '.join(details)})"

    def _build_target(self, op: NormalizedOperation, step_type: StepType) -> StepTarget:
        target = StepTarget()
        context = op.context or {}
        focused_element = context.get("focused_element")
        active_window = context.get("active_window") or {}
        node_suggestion = context.get("node_suggestion") or {}
        is_web_context = bool(
            context.get("platform") == "web"
            or active_window.get("browser_type")
            or active_window.get("url")
            or (
                focused_element
                and (focused_element.get("selector") or focused_element.get("xpath") or focused_element.get("url"))
            )
        )

        if step_type in (
            StepType.CLICK,
            StepType.TYPE,
            StepType.SCROLL,
            StepType.DRAG,
            StepType.UPLOAD_FILE,
            StepType.DOWNLOAD_FILE,
        ):
            x = op.data.get("x", 0)
            y = op.data.get("y", 0)
            position = {"x": x, "y": y} if x or y else None
            suggested_target = self._build_node_suggestion_target(
                node_suggestion,
                active_window,
                position=position,
                screenshot_path=context.get("screenshot_path"),
            )

            if focused_element:
                selector = focused_element.get("selector")
                xpath = focused_element.get("xpath")
                identifier = focused_element.get("identifier")
                role = focused_element.get("role")
                title = focused_element.get("title")
                text_hint = self._focused_text_hint(focused_element)
                class_name = focused_element.get("class_name")
                bounds = focused_element.get("bounds")
                url = focused_element.get("url") or active_window.get("url")
                frame = focused_element.get("frame")
                expected_attributes = self._build_expected_attributes(focused_element)
                window_title = active_window.get("title") or active_window.get("app_name")

                if is_web_context and selector:
                    target = StepTarget(
                        strategy=LocateStrategy.CSS_SELECTOR,
                        selector=selector,
                        xpath=xpath,
                        position=position,
                        role=role,
                        title=title or text_hint,
                        text_contains=text_hint,
                        class_name=class_name,
                        window_title=window_title,
                        bounds=bounds,
                        url=url,
                        frame=frame,
                        expected_attributes=expected_attributes,
                    )
                elif is_web_context and xpath:
                    target = StepTarget(
                        strategy=LocateStrategy.XPATH,
                        selector=selector,
                        xpath=xpath,
                        position=position,
                        role=role,
                        title=title or text_hint,
                        text_contains=text_hint,
                        class_name=class_name,
                        window_title=window_title,
                        bounds=bounds,
                        url=url,
                        frame=frame,
                        expected_attributes=expected_attributes,
                    )
                elif identifier:
                    target = StepTarget(
                        strategy=LocateStrategy.ACCESSIBILITY_ID,
                        accessibility_id=identifier,
                        position=position,
                        role=role,
                        title=title or text_hint,
                        text_contains=text_hint,
                        class_name=class_name,
                        window_title=window_title,
                        bounds=bounds,
                        url=url if is_web_context else None,
                        frame=frame if is_web_context else None,
                        expected_attributes=expected_attributes,
                    )
                elif text_hint and role and not is_web_context:
                    target = StepTarget(
                        strategy=LocateStrategy.TEXT_MATCH,
                        title=title or text_hint,
                        text_contains=text_hint,
                        position=position,
                        role=role,
                        class_name=class_name,
                        window_title=window_title,
                        bounds=bounds,
                        expected_attributes=expected_attributes,
                    )
                elif role and not is_web_context:
                    target = StepTarget(
                        strategy=LocateStrategy.POSITION,
                        position=position,
                        role=role,
                        title=title or text_hint,
                        class_name=class_name,
                        window_title=window_title,
                        bounds=bounds,
                        expected_attributes=expected_attributes or None,
                    )
                elif is_web_context and text_hint:
                    target = StepTarget(
                        strategy=LocateStrategy.TEXT_MATCH,
                        title=title or text_hint,
                        text_contains=text_hint,
                        role=role,
                        class_name=class_name,
                        window_title=window_title,
                        bounds=bounds,
                        url=url,
                        frame=frame,
                        expected_attributes=expected_attributes,
                    )
                else:
                    target = StepTarget(
                        strategy=LocateStrategy.POSITION,
                        position=position,
                        window_title=window_title,
                        url=active_window.get("url") if is_web_context else None,
                        frame=frame if is_web_context else None,
                        expected_attributes=expected_attributes if focused_element else None,
                    )
            elif x and y:
                target = StepTarget(
                    strategy=LocateStrategy.POSITION,
                    position=position,
                    window_title=active_window.get("title") or active_window.get("app_name"),
                    url=active_window.get("url") if is_web_context else None,
                )
            target = self._upgrade_target_with_node_suggestion(target, suggested_target)
        elif step_type == StepType.SWITCH_WINDOW:
            title = op.data.get("title") or op.data.get("app_name")
            url = op.data.get("url") or active_window.get("url")
            app_name = op.data.get("app_name") or active_window.get("app_name")
            expected_attributes = {"app_name": app_name} if app_name else None
            if title or url:
                target = StepTarget(
                    strategy=LocateStrategy.TEXT_MATCH,
                    title=title,
                    text_contains=title,
                    window_title=title,
                    url=url,
                    expected_attributes=expected_attributes,
                )
        elif step_type == StepType.NAVIGATE:
            title = op.data.get("title") or active_window.get("title")
            url = op.data.get("url") or active_window.get("url")
            if title or url:
                target = StepTarget(
                    strategy=LocateStrategy.TEXT_MATCH,
                    title=title,
                    window_title=title,
                    url=url,
                )
        return target

    @staticmethod
    def _focused_text_hint(focused_element: dict) -> str | None:
        for value in (
            focused_element.get("title"),
            focused_element.get("value"),
            focused_element.get("functional_label"),
            focused_element.get("description"),
        ):
            if ScriptGenerator._is_meaningful_text_hint(value):
                return value.strip()
        return None

    @staticmethod
    def _build_expected_attributes(focused_element: dict) -> dict | None:
        expected_attributes = {
            "value": focused_element.get("value"),
            "description": focused_element.get("description"),
            "functional_label": focused_element.get("functional_label"),
            "input_type": focused_element.get("input_type"),
            "tag_name": focused_element.get("tag_name"),
        }
        compact = {key: value for key, value in expected_attributes.items() if value not in (None, "")}
        return compact or None

    @staticmethod
    def _build_node_suggestion_target(
        node_suggestion: dict,
        active_window: dict,
        *,
        position: dict | None = None,
        screenshot_path: str | None = None,
    ) -> StepTarget | None:
        if not isinstance(node_suggestion, dict) or not node_suggestion:
            return None

        suggested_target = node_suggestion.get("target") or {}
        if not isinstance(suggested_target, dict) or not suggested_target:
            return None

        strategy_raw = suggested_target.get("strategy")
        try:
            strategy = LocateStrategy(strategy_raw)
        except Exception:
            return None

        title = suggested_target.get("title") or suggested_target.get("text_contains")
        text_contains = suggested_target.get("text_contains") or suggested_target.get("text_preview") or title
        expected_attributes = ScriptGenerator._build_node_expected_attributes(
            node_suggestion,
            suggested_target,
            screenshot_path=screenshot_path,
        )
        return StepTarget(
            strategy=strategy,
            accessibility_id=suggested_target.get("accessibility_id"),
            title=title,
            text_contains=text_contains,
            role=suggested_target.get("role"),
            class_name=suggested_target.get("class_name"),
            selector=suggested_target.get("selector"),
            xpath=suggested_target.get("xpath"),
            url=suggested_target.get("url") or active_window.get("url"),
            frame=suggested_target.get("frame"),
            image_path=suggested_target.get("image_path"),
            position=suggested_target.get("position") or position,
            window_title=suggested_target.get("window_title") or active_window.get("title") or active_window.get("app_name"),
            bounds=suggested_target.get("bounds"),
            expected_attributes=expected_attributes,
        )

    @staticmethod
    def _build_node_expected_attributes(
        node_suggestion: dict,
        suggested_target: dict,
        *,
        screenshot_path: str | None = None,
    ) -> dict | None:
        vision_element = suggested_target.get("vision_element") or {}
        expected_attributes = {
            "snapshot_id": node_suggestion.get("snapshot_id"),
            "screenshot_path": screenshot_path,
            "vision_source": node_suggestion.get("vision_source"),
            "vision_confidence": node_suggestion.get("confidence"),
            "vision_reason": node_suggestion.get("confidence_reason"),
            "vision_label": vision_element.get("label"),
            "vision_element_type": vision_element.get("element_type"),
            "ocr_text": node_suggestion.get("ocr_text"),
            "ocr_confidence": node_suggestion.get("ocr_confidence"),
            "text_preview": suggested_target.get("text_preview"),
            "description": suggested_target.get("description"),
        }
        compact = {key: value for key, value in expected_attributes.items() if value not in (None, "")}
        return compact or None

    @staticmethod
    def _upgrade_target_with_node_suggestion(target: StepTarget, suggested_target: StepTarget | None) -> StepTarget:
        if suggested_target is None:
            return target

        has_recorded_identity = any(
            [
                target.accessibility_id,
                target.selector,
                target.xpath,
                target.title,
                target.text_contains,
                target.role,
                target.class_name,
                target.window_title,
                target.url,
                target.position,
                target.expected_attributes,
            ]
        )
        if not has_recorded_identity:
            return suggested_target
        if target.strategy == LocateStrategy.POSITION and suggested_target.strategy != LocateStrategy.POSITION:
            return suggested_target

        updates: dict[str, object] = {}
        for field in (
            "accessibility_id",
            "title",
            "text_contains",
            "role",
            "class_name",
            "selector",
            "xpath",
            "url",
            "frame",
            "image_path",
            "window_title",
            "bounds",
            "expected_attributes",
        ):
            current_value = getattr(target, field, None)
            suggested_value = getattr(suggested_target, field, None)
            if current_value in (None, "", {}, []) and suggested_value not in (None, "", {}, []):
                updates[field] = suggested_value
        return target.model_copy(update=updates) if updates else target

    @staticmethod
    def _is_meaningful_text_hint(value: object) -> bool:
        if not isinstance(value, str):
            return False
        text = value.strip()
        if not text:
            return False
        return text not in {"文本", "text", "label", "static text"}

    def _extract_variables(
        self, steps: list[AutomationStep], operations: list[NormalizedOperation]
    ) -> list[FlowVariable]:
        variables = []
        for step in steps:
            if step.type == StepType.TYPE and step.action.get("text"):
                text = step.action["text"]
                if len(text) > 3:
                    var_name = f"input_text_{len(variables) + 1}"
                    variables.append(
                        FlowVariable(
                            name=var_name,
                            var_type="string",
                            default_value=text,
                            description=f"Text input for step {step.id}",
                        )
                    )
                    step.action["text"] = f"${{{var_name}}}"
            elif step.type == StepType.SWITCH_WINDOW and step.action.get("title"):
                title = step.action["title"]
                if title:
                    var_name = f"window_title_{len(variables) + 1}"
                    variables.append(
                        FlowVariable(
                            name=var_name,
                            var_type="string",
                            default_value=title,
                            description=f"Window title for step {step.id}",
                        )
                    )
                    step.action["title"] = f"${{{var_name}}}"
            elif step.type == StepType.UPLOAD_FILE and step.action.get("path"):
                file_path = step.action["path"]
                if file_path:
                    var_name = f"upload_file_{len(variables) + 1}"
                    variables.append(
                        FlowVariable(
                            name=var_name,
                            var_type="string",
                            default_value=file_path,
                            description=f"Upload file path for step {step.id}",
                        )
                    )
                    step.action["path"] = f"${{{var_name}}}"
        return variables

    def generate_from_bt(self, bt_model: BehaviorTreeModel) -> AutomationFlow:
        steps = []
        self._bt_node_to_steps(bt_model.root, steps, [])
        variables = self._extract_bt_variables(bt_model)
        return AutomationFlow(
            id=bt_model.tree_id,
            name=bt_model.name or f"BT-{bt_model.tree_id[:8]}",
            description=bt_model.description or "Generated from behavior tree",
            steps=steps,
            variables=variables,
            error_handling=ErrorHandlingPolicy(),
            confidence=0.7,
            source_session_id=bt_model.source_session_id,
            behavior_tree=bt_model.to_flow_dict(),
        )

    def _bt_node_to_steps(
        self,
        node: BTNodeModel,
        steps: list[AutomationStep],
        depends_on: list[str],
    ) -> None:
        if node.node_type == BTNodeType.ACTION:
            step = self._bt_action_to_step(node, depends_on)
            steps.append(step)
        elif node.node_type == BTNodeType.DECORATOR:
            self._bt_decorator_to_steps(node, steps, depends_on)
        elif node.node_type == BTNodeType.SEQUENCE:
            self._bt_sequence_to_steps(node, steps, depends_on)
        elif node.node_type == BTNodeType.SELECTOR:
            self._bt_selector_to_steps(node, steps, depends_on)
        elif node.node_type == BTNodeType.CONDITION:
            step = self._bt_condition_to_step(node, depends_on)
            steps.append(step)
        elif node.node_type == BTNodeType.PARALLEL:
            for child in node.children:
                self._bt_node_to_steps(child, steps, depends_on)

    def _bt_action_to_step(
        self,
        node: BTNodeModel,
        depends_on: list[str],
    ) -> AutomationStep:
        step_type = StepType.CLICK
        action_data: dict = {}
        if node.metadata:
            step_type = StepType(node.metadata.get("step_type", "click"))
            action_data = node.metadata.get("action", {})
        defaults = STEP_TYPE_DEFAULTS.get(step_type, {"delay": 500, "on_error": ErrorAction.ABORT, "retry_count": 3})
        return AutomationStep(
            id=node.node_id,
            type=step_type,
            action=action_data,
            target=StepTarget(),
            depends_on=list(depends_on),
            delay=defaults["delay"],
            on_error=defaults["on_error"],
            retry_count=defaults["retry_count"],
            description=node.label or f"BT action {node.node_id[:8]}",
        )

    def _bt_condition_to_step(
        self,
        node: BTNodeModel,
        depends_on: list[str],
    ) -> AutomationStep:
        from src.models.automation import StepCondition

        condition = None
        if node.condition:
            condition = StepCondition(
                field=node.condition.field,
                operator=node.condition.operator,
                value=node.condition.value,
            )
        return AutomationStep(
            id=node.node_id,
            type=StepType.CONDITION,
            action={"check": node.condition.field if node.condition else "window_active"},
            target=StepTarget(),
            depends_on=list(depends_on),
            condition=condition,
            delay=0,
            on_error=ErrorAction.SKIP,
            retry_count=0,
            description=node.label or "Check condition",
        )

    def _bt_decorator_to_steps(
        self,
        node: BTNodeModel,
        steps: list[AutomationStep],
        depends_on: list[str],
    ) -> None:
        if not node.children:
            return
        child = node.children[0]
        self._bt_node_to_steps(child, steps, depends_on)
        if steps and node.decorator_type:
            last_step = steps[-1]
            if node.decorator_type == DecoratorType.RETRY:
                last_step.retry_count = node.decorator_params.get("max_attempts", 3)
                last_step.on_error = ErrorAction.RETRY
            elif node.decorator_type == DecoratorType.TIMEOUT:
                last_step.metadata["timeout_ms"] = node.decorator_params.get("timeout_ms", 5000)
            elif node.decorator_type == DecoratorType.LOOP:
                last_step.metadata["loop_count"] = node.decorator_params.get("count", 1)

    def _bt_sequence_to_steps(
        self,
        node: BTNodeModel,
        steps: list[AutomationStep],
        depends_on: list[str],
    ) -> None:
        for child in node.children:
            child_depends = depends_on if not steps else [steps[-1].id]
            self._bt_node_to_steps(child, steps, child_depends)

    def _bt_selector_to_steps(
        self,
        node: BTNodeModel,
        steps: list[AutomationStep],
        depends_on: list[str],
    ) -> None:
        for i, child in enumerate(node.children):
            self._bt_node_to_steps(child, steps, depends_on)
            if i == 0 and steps:
                last_step = steps[-1]
                if len(node.children) > 1:
                    last_step.metadata["has_fallback"] = True

    def _extract_bt_variables(self, bt_model: BehaviorTreeModel) -> list[FlowVariable]:
        variables = []
        for var_data in bt_model.variables:
            variables.append(
                FlowVariable(
                    name=var_data.get("name", f"var_{len(variables)}"),
                    var_type=var_data.get("type", "string"),
                    default_value=var_data.get("default"),
                    description=var_data.get("description", ""),
                )
            )
        return variables

    def export_json(self, flow: AutomationFlow) -> str:
        return json.dumps(flow.model_dump(), indent=2, ensure_ascii=False)

    def export_python(self, flow: AutomationFlow) -> str:
        if any(self._is_web_step(step) for step in flow.steps):
            return self._export_web_python(flow)

        lines = [
            "import pyautogui",
            "import time",
            "",
            f"def execute_{flow.name.replace('-', '_')}():",
            f'    """{flow.description}"""',
        ]
        for var in flow.variables:
            lines.append(f"    {var.name} = {self._python_expr(var.default_value)}")

        lines.append("")
        for step in flow.steps:
            lines.append(f"    # {step.description}")
            if step.type == StepType.CLICK:
                pos = step.target.position
                if pos:
                    lines.append(f"    pyautogui.click(x={pos.x}, y={pos.y})")
            elif step.type == StepType.TYPE:
                text = self._python_expr(step.action.get("text", ""))
                lines.append(f"    pyautogui.write({text})")
            elif step.type == StepType.HOTKEY:
                mods = step.action.get("modifiers", [])
                key = step.action.get("key", "")
                for m in mods:
                    lines.append(f"    pyautogui.keyDown('{m}')")
                lines.append(f"    pyautogui.press('{key}')")
                for m in reversed(mods):
                    lines.append(f"    pyautogui.keyUp('{m}')")
            elif step.type == StepType.SCROLL:
                delta = step.action.get("delta", 0)
                lines.append(f"    pyautogui.scroll({delta})")
            if step.delay > 0:
                lines.append(f"    time.sleep({step.delay / 1000:.1f})")
        lines.append("")
        lines.append("if __name__ == '__main__':")
        lines.append(f"    execute_{flow.name.replace('-', '_')}()")
        return "\n".join(lines)

    @staticmethod
    def _is_web_step(step: AutomationStep) -> bool:
        if step.type in {StepType.NAVIGATE, StepType.UPLOAD_FILE, StepType.DOWNLOAD_FILE}:
            return True
        target = step.target
        if target is None:
            return bool(step.action.get("url"))
        return bool(
            target.strategy in {LocateStrategy.CSS_SELECTOR, LocateStrategy.XPATH}
            or target.selector
            or target.xpath
            or target.url
            or target.frame
            or step.action.get("url")
        )

    @staticmethod
    def _python_expr(value) -> str:
        if isinstance(value, str):
            if value.startswith("${") and value.endswith("}"):
                return value[2:-1]
            return json.dumps(value, ensure_ascii=False)
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, list):
            return "[" + ", ".join(ScriptGenerator._python_expr(item) for item in value) + "]"
        if isinstance(value, dict):
            return (
                "{"
                + ", ".join(
                    f"{json.dumps(str(key), ensure_ascii=False)}: {ScriptGenerator._python_expr(item)}"
                    for key, item in value.items()
                )
                + "}"
            )
        return repr(value)

    def _export_web_python(self, flow: AutomationFlow) -> str:
        function_name = flow.name.replace("-", "_")
        lines = ["import asyncio"]
        if any(step.type == StepType.DOWNLOAD_FILE for step in flow.steps):
            lines.append("from pathlib import Path")
        lines.extend(
            [
                "from playwright.async_api import async_playwright",
                "",
                f"async def execute_{function_name}():",
                f'    """{flow.description}"""',
                "    async with async_playwright() as playwright:",
                "        browser = await playwright.chromium.launch(headless=True)",
                "        page = await browser.new_page()",
            ]
        )

        for var in flow.variables:
            lines.append(f"        {var.name} = {self._python_expr(var.default_value)}")
        if flow.variables:
            lines.append("")

        current_url = None
        for step in flow.steps:
            target = step.target
            step_url = target.url if target else None
            if step.type != StepType.NAVIGATE and step_url and step_url != current_url:
                lines.append(f"        await page.goto({json.dumps(step_url)}, wait_until='domcontentloaded')")
                current_url = step_url

            lines.append(f"        # {step.description}")
            selector = None
            if target:
                if target.selector:
                    selector = json.dumps(target.selector)
                elif target.xpath:
                    selector = json.dumps(f"xpath={target.xpath}")

            if step.type == StepType.NAVIGATE:
                if step.action.get("new_tab", False):
                    lines.append("        page = await browser.new_page()")
                navigate_url = step.action.get("url") or step_url
                wait_until = step.action.get("wait_until", "domcontentloaded")
                goto_expr = (
                    f"        await page.goto("
                    f"{self._python_expr(navigate_url)}, "
                    f"wait_until={self._python_expr(wait_until)})"
                )
                lines.append(goto_expr)
                wait_for = step.action.get("wait_for")
                if wait_for:
                    timeout_ms = int(step.action.get("timeout_ms", 30000))
                    wait_state = step.action.get("wait_state", "visible")
                    wait_expr = (
                        f"        await page.locator("
                        f"{self._python_expr(wait_for)}).first.wait_for("
                        f"state={self._python_expr(wait_state)}, "
                        f"timeout={timeout_ms})"
                    )
                    lines.append(wait_expr)
                current_url = navigate_url
            elif step.type == StepType.CLICK and selector:
                lines.append(f"        await page.locator({selector}).first.click()")
            elif step.type == StepType.TYPE and selector:
                text = self._python_expr(step.action.get("text", ""))
                lines.append(f"        await page.locator({selector}).first.fill({text})")
            elif step.type == StepType.UPLOAD_FILE and selector:
                file_value = step.action.get("file_paths") or step.action.get("paths") or step.action.get("path")
                lines.append(
                    f"        await page.locator({selector}).first.set_input_files({self._python_expr(file_value)})"
                )
            elif step.type == StepType.DOWNLOAD_FILE and selector:
                timeout_ms = int(step.action.get("timeout_ms", 30000))
                lines.append(f"        async with page.expect_download(timeout={timeout_ms}) as download_info:")
                lines.append(f"            await page.locator({selector}).first.click()")
                lines.append("        download = await download_info.value")
                save_as = step.action.get("save_as") or step.action.get("path")
                download_dir = step.action.get("download_dir")
                if save_as:
                    lines.append(f"        save_path = Path({self._python_expr(save_as)}).expanduser()")
                    lines.append("        save_path.parent.mkdir(parents=True, exist_ok=True)")
                    lines.append("        await download.save_as(str(save_path))")
                elif download_dir:
                    lines.append(f"        download_dir = Path({self._python_expr(download_dir)}).expanduser()")
                    lines.append("        download_dir.mkdir(parents=True, exist_ok=True)")
                    lines.append("        await download.save_as(str(download_dir / download.suggested_filename))")
            elif step.type == StepType.HOTKEY:
                modifiers = step.action.get("modifiers", [])
                key = step.action.get("key", "")
                chord_parts = [*modifiers, key] if key else modifiers
                if chord_parts:
                    chord = "+".join(chord_parts)
                    lines.append(f"        await page.keyboard.press({json.dumps(chord)})")
            elif step.type == StepType.SCROLL:
                delta = int(step.action.get("delta", 0))
                lines.append(f"        await page.mouse.wheel(0, {-delta * 120})")
            elif step.type == StepType.WAIT:
                duration = float(step.action.get("duration", step.delay / 1000.0))
                lines.append(f"        await asyncio.sleep({duration})")

            if step.delay > 0 and step.type != StepType.WAIT:
                lines.append(f"        await asyncio.sleep({step.delay / 1000:.1f})")

        lines.append("        await browser.close()")
        lines.append("")
        lines.append("if __name__ == '__main__':")
        lines.append(f"    asyncio.run(execute_{function_name}())")
        return "\n".join(lines)
