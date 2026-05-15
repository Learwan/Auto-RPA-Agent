from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from src.analyzer.semantic_annotator import StepAnnotation
from src.models.automation import AutomationStep

logger = logging.getLogger(__name__)


class BTNodeType(StrEnum):
    SEQUENCE = "sequence"
    SELECTOR = "selector"
    PARALLEL = "parallel"
    ACTION = "action"
    CONDITION = "condition"
    DECORATOR = "decorator"


class DecoratorType(StrEnum):
    RETRY = "retry"
    TIMEOUT = "timeout"
    INVERTER = "inverter"


@dataclass
class BTNode:
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    node_type: BTNodeType = BTNodeType.ACTION
    children: list[BTNode] = field(default_factory=list)
    action: AutomationStep | None = None
    condition: str | None = None
    decorator_type: DecoratorType | None = None
    decorator_params: dict = field(default_factory=dict)
    label: str = ""

    def to_dict(self) -> dict:
        result = {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "label": self.label,
        }
        if self.action:
            result["action_id"] = self.action.id
        if self.condition:
            result["condition"] = self.condition
        if self.decorator_type:
            result["decorator_type"] = self.decorator_type.value
            result["decorator_params"] = self.decorator_params
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        return result

    @classmethod
    def from_dict(cls, data: dict) -> BTNode:
        node = cls(
            node_id=data.get("node_id", str(uuid.uuid4())),
            node_type=BTNodeType(data.get("node_type", "action")),
            condition=data.get("condition"),
            decorator_type=DecoratorType(data["decorator_type"]) if data.get("decorator_type") else None,
            decorator_params=data.get("decorator_params", {}),
            label=data.get("label", ""),
        )
        for child_data in data.get("children", []):
            node.children.append(cls.from_dict(child_data))
        return node


class BehaviorTreeGenerator:
    def generate(
        self,
        steps: list[AutomationStep],
        annotations: list[StepAnnotation] | None = None,
    ) -> BTNode:
        if not steps:
            return BTNode(node_type=BTNodeType.SEQUENCE, label="empty_flow")

        root = BTNode(node_type=BTNodeType.SEQUENCE, label="workflow_root")

        i = 0
        while i < len(steps):
            step = steps[i]

            if self._is_conditional_step(step, annotations, i):
                selector = self._create_selector_node(step, steps, annotations, i)
                root.children.append(selector)
                i = self._skip_branch(steps, i)
            elif self._is_retry_step(step):
                decorator = self._create_retry_decorator(step)
                root.children.append(decorator)
                i += 1
            else:
                action_node = BTNode(
                    node_type=BTNodeType.ACTION,
                    action=step,
                    label=self._get_step_label(step, annotations, i),
                )
                root.children.append(action_node)
                i += 1

        return root

    def _is_conditional_step(
        self,
        step: AutomationStep,
        annotations: list[StepAnnotation] | None,
        index: int,
    ) -> bool:
        if step.condition is not None:
            return True
        if step.type.value == "condition":
            return True
        if annotations and index < len(annotations):
            ann = annotations[index]
            if ann.category == "navigation" and "切换" in ann.action:
                return True
        return False

    def _is_retry_step(self, step: AutomationStep) -> bool:
        return step.retry_count > 2 and step.on_error.value == "retry"

    def _create_selector_node(
        self,
        step: AutomationStep,
        steps: list[AutomationStep],
        annotations: list[StepAnnotation] | None,
        index: int,
    ) -> BTNode:
        selector = BTNode(
            node_type=BTNodeType.SELECTOR,
            label=f"conditional_{index}",
        )

        condition_node = BTNode(
            node_type=BTNodeType.CONDITION,
            condition=step.condition.field if step.condition else "window_active",
            label=f"check_{index}",
        )

        action_node = BTNode(
            node_type=BTNodeType.ACTION,
            action=step,
            label=self._get_step_label(step, annotations, index),
        )

        sequence = BTNode(
            node_type=BTNodeType.SEQUENCE,
            label=f"branch_{index}",
            children=[condition_node, action_node],
        )

        selector.children.append(sequence)
        return selector

    def _create_retry_decorator(self, step: AutomationStep) -> BTNode:
        action_node = BTNode(
            node_type=BTNodeType.ACTION,
            action=step,
            label=step.description or f"retry_step_{step.id[:8]}",
        )
        return BTNode(
            node_type=BTNodeType.DECORATOR,
            decorator_type=DecoratorType.RETRY,
            decorator_params={"max_attempts": step.retry_count, "delay_ms": step.delay},
            children=[action_node],
            label=f"retry_{step.retry_count}",
        )

    def _skip_branch(self, steps: list[AutomationStep], current: int) -> int:
        return current + 1

    def _get_step_label(
        self,
        step: AutomationStep,
        annotations: list[StepAnnotation] | None,
        index: int,
    ) -> str:
        if annotations and index < len(annotations):
            ann = annotations[index]
            if ann.action:
                return ann.action[:80]
        if step.description:
            return step.description[:80]
        return f"{step.type.value}_{index}"

    def extract_variables(self, root: BTNode) -> list[dict]:
        variables = []
        self._collect_variables(root, variables)
        return variables

    def _collect_variables(self, node: BTNode, variables: list[dict]) -> None:
        if node.action and node.action.type.value == "type":
            text = node.action.action.get("text", "")
            if text and len(text) > 3 and text.startswith("${") and text.endswith("}"):
                var_name = text[2:-1]
                variables.append(
                    {
                        "name": var_name,
                        "type": "string",
                        "source_step": node.action.id,
                    }
                )

        for child in node.children:
            self._collect_variables(child, variables)
