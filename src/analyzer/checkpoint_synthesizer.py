from __future__ import annotations

from src.models.automation import AutomationFlow, AutomationStep, LocateStrategy, StepCondition, StepType


class CheckpointSynthesizer:
    def apply(self, flow: AutomationFlow, semantic_context: dict | None = None) -> dict:
        ordered_steps = flow.ordered_steps()
        annotations = {
            int(item.get("step_index")): item
            for item in (semantic_context or {}).get("annotations", [])
            if isinstance(item, dict) and isinstance(item.get("step_index"), int)
        }

        preconditions_added = 0
        postconditions_added = 0
        steps_with_checkpoints = 0

        for index, step in enumerate(ordered_steps):
            annotation = annotations.get(index, {})
            generated_preconditions, generated_postconditions = self._synthesize_for_step(step)

            for condition in generated_preconditions:
                if self._append_precondition(step, condition):
                    preconditions_added += 1

            postconditions_added += self._merge_postconditions(step, generated_postconditions)

            pre_checkpoint_payloads = [
                {
                    **condition.model_dump(mode="json", exclude_none=True),
                    "description": self._describe_condition(condition),
                }
                for condition in step.preconditions
            ]
            post_checkpoint_payloads = [
                item
                for item in (step.metadata or {}).get("postconditions", [])
                if isinstance(item, dict)
            ]

            if pre_checkpoint_payloads or post_checkpoint_payloads:
                steps_with_checkpoints += 1
                step.metadata = {
                    **(step.metadata or {}),
                    "checkpoints": {
                        "pre": pre_checkpoint_payloads,
                        "post": post_checkpoint_payloads,
                        "intent_hint": annotation.get("business_term"),
                        "data_flow_role": annotation.get("data_flow_role"),
                    },
                }

        return {
            "steps_with_checkpoints": steps_with_checkpoints,
            "preconditions_added": preconditions_added,
            "postconditions_added": postconditions_added,
        }

    def _synthesize_for_step(self, step: AutomationStep) -> tuple[list[StepCondition], list[dict]]:
        target = step.target
        has_window_hint = bool(target and target.window_title)
        has_semantic_locator = self._has_semantic_locator(step)
        has_position_target = bool(target and target.position)

        preconditions: list[StepCondition] = []
        postconditions: list[dict] = []

        if step.type == StepType.SWITCH_WINDOW:
            preconditions.append(
                StepCondition(field="window_exists", operator="truthy", value=True, timeout_ms=6000)
            )
            postconditions.append(
                {
                    "field": "window_exists",
                    "operator": "truthy",
                    "value": True,
                    "timeout_ms": 6000,
                    "critical": True,
                    "description": "Window should be available after switching",
                }
            )
            return preconditions, postconditions

        if step.type == StepType.NAVIGATE:
            preconditions.append(
                StepCondition(field="page_stable", operator="truthy", value=True, timeout_ms=3000)
            )
            postconditions.append(
                {
                    "field": "page_stable",
                    "operator": "truthy",
                    "value": True,
                    "timeout_ms": 3500,
                    "critical": False,
                    "description": "Page should stabilize after navigation",
                }
            )
            return preconditions, postconditions

        requires_confirmation = step.locator_requires_confirmation()

        if step.type in {
            StepType.CLICK,
            StepType.TYPE,
            StepType.SCROLL,
            StepType.DRAG,
            StepType.UPLOAD_FILE,
            StepType.DOWNLOAD_FILE,
            StepType.HOTKEY,
        }:
            if has_window_hint:
                preconditions.append(
                    StepCondition(field="window_exists", operator="truthy", value=True, timeout_ms=5000)
                )

            if has_semantic_locator and not requires_confirmation:
                preconditions.append(
                    StepCondition(field="element_exists", operator="truthy", value=True, timeout_ms=5000)
                )
            elif has_position_target:
                preconditions.append(
                    StepCondition(field="page_stable", operator="truthy", value=True, timeout_ms=2500)
                )

            postconditions.append(
                {
                    "field": "page_stable",
                    "operator": "truthy",
                    "value": True,
                    "timeout_ms": 2500,
                    "critical": False,
                    "description": "UI should stabilize after action",
                }
            )

        return preconditions, postconditions

    def _has_semantic_locator(self, step: AutomationStep) -> bool:
        target = step.target
        if target is None:
            return False
        if target.strategy in {LocateStrategy.CSS_SELECTOR, LocateStrategy.XPATH, LocateStrategy.ACCESSIBILITY_ID}:
            return True
        return bool(target.selector or target.xpath or target.accessibility_id or target.title or target.text_contains)

    def _append_precondition(self, step: AutomationStep, condition: StepCondition) -> bool:
        existing_signatures = {
            self._condition_signature(item.field, item.operator, item.value)
            for item in step.preconditions
        }
        signature = self._condition_signature(condition.field, condition.operator, condition.value)
        if signature in existing_signatures:
            return False
        step.preconditions.append(condition)
        return True

    def _merge_postconditions(self, step: AutomationStep, generated: list[dict]) -> int:
        metadata = step.metadata or {}
        existing_items = metadata.get("postconditions", [])
        merged_items: list[dict] = [item for item in existing_items if isinstance(item, dict)]
        existing_signatures = {
            self._condition_signature(
                item.get("field", ""),
                item.get("operator", "eq"),
                item.get("value"),
            )
            for item in merged_items
        }

        added = 0
        for item in generated:
            signature = self._condition_signature(
                item.get("field", ""),
                item.get("operator", "eq"),
                item.get("value"),
            )
            if signature in existing_signatures:
                continue
            merged_items.append(item)
            existing_signatures.add(signature)
            added += 1

        if added > 0 or "postconditions" in metadata:
            step.metadata = {
                **metadata,
                "postconditions": merged_items,
            }
        return added

    @staticmethod
    def _condition_signature(field: str, operator: str, value) -> str:
        return f"{field}|{operator}|{repr(value)}"

    @staticmethod
    def _describe_condition(condition: StepCondition) -> str:
        if condition.field == "window_exists":
            return "Target window is available"
        if condition.field == "element_exists":
            return "Target element is available"
        if condition.field == "page_stable":
            return "Page/UI is stable"
        return f"{condition.field} {condition.operator} {condition.value}"
