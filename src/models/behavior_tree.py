from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BTNodeType(str, Enum):
    SEQUENCE = "sequence"
    SELECTOR = "selector"
    PARALLEL = "parallel"
    ACTION = "action"
    CONDITION = "condition"
    DECORATOR = "decorator"
    SUBTREE = "subtree"


class DecoratorType(str, Enum):
    RETRY = "retry"
    TIMEOUT = "timeout"
    INVERTER = "inverter"
    REPEAT = "repeat"
    COOLDOWN = "cooldown"
    FORCE_SUCCESS = "force_success"
    FORCE_FAILURE = "force_failure"


class BTCondition(BaseModel):
    field: str = ""
    operator: str = "equals"
    value: Any = None

    model_config = {"extra": "allow"}


class BTNodeModel(BaseModel):
    node_id: str = ""
    node_type: BTNodeType = BTNodeType.ACTION
    label: str = ""
    children: list[BTNodeModel] = Field(default_factory=list)
    condition: BTCondition | None = None
    action_ref: str | None = None
    decorator_type: DecoratorType | None = None
    decorator_params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    def find_node(self, node_id: str) -> BTNodeModel | None:
        if self.node_id == node_id:
            return self
        for child in self.children:
            result = child.find_node(node_id)
            if result is not None:
                return result
        return None

    def count_nodes(self) -> int:
        return 1 + sum(child.count_nodes() for child in self.children)

    def count_by_type(self, node_type: BTNodeType) -> int:
        count = 1 if self.node_type == node_type else 0
        return count + sum(child.count_by_type(node_type) for child in self.children)

    def max_depth(self) -> int:
        if not self.children:
            return 1
        return 1 + max(child.max_depth() for child in self.children)

    def flatten_actions(self) -> list[BTNodeModel]:
        actions: list[BTNodeModel] = []
        if self.node_type == BTNodeType.ACTION:
            actions.append(self)
        for child in self.children:
            actions.extend(child.flatten_actions())
        return actions


class BehaviorTreeModel(BaseModel):
    tree_id: str = ""
    name: str = ""
    root: BTNodeModel = Field(default_factory=BTNodeModel)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    @property
    def total_nodes(self) -> int:
        return self.root.count_nodes()

    @property
    def depth(self) -> int:
        return self.root.max_depth()

    @property
    def action_count(self) -> int:
        return self.root.count_by_type(BTNodeType.ACTION)

    def find_node(self, node_id: str) -> BTNodeModel | None:
        return self.root.find_node(node_id)

    def get_action_nodes(self) -> list[BTNodeModel]:
        return self.root.flatten_actions()

    def to_flow_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["stats"] = {
            "total_nodes": self.total_nodes,
            "depth": self.depth,
            "action_count": self.action_count,
        }
        return data
