from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class BTNodeType(str, Enum):
    SEQUENCE = "sequence"
    SELECTOR = "selector"
    PARALLEL = "parallel"
    DECORATOR = "decorator"
    ACTION = "action"
    CONDITION = "condition"
    SUBTREE = "subtree"


class DecoratorType(str, Enum):
    RETRY = "retry"
    TIMEOUT = "timeout"
    INVERTER = "inverter"
    SUCCESS_LIMIT = "success_limit"
    FAILURE_LIMIT = "failure_limit"
    REPEAT = "repeat"


class BTCondition(BaseModel):
    """Condition gating a node (e.g. ``window_title contains 'Chrome'``)."""

    model_config = ConfigDict(extra="ignore")

    field: str
    operator: str = "eq"
    value: Any = None
    description: str | None = None


class BTNodeModel(BaseModel):
    """A single behaviour-tree node."""

    model_config = ConfigDict(extra="ignore")

    node_id: str = Field(default_factory=lambda: uuid4().hex)
    node_type: BTNodeType
    label: str | None = None
    description: str | None = None
    action_ref: str | None = None
    condition: BTCondition | None = None
    decorator_type: DecoratorType | None = None
    decorator_params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    children: list["BTNodeModel"] = Field(default_factory=list)

    def find_node(self, node_id: str) -> "BTNodeModel | None":
        if self.node_id == node_id:
            return self
        for child in self.children:
            found = child.find_node(node_id)
            if found is not None:
                return found
        return None

    def count_nodes(self) -> int:
        return 1 + sum(child.count_nodes() for child in self.children)

    def count_by_type(self, node_type: BTNodeType) -> int:
        total = 1 if self.node_type == node_type else 0
        for child in self.children:
            total += child.count_by_type(node_type)
        return total

    def max_depth(self) -> int:
        if not self.children:
            return 1
        return 1 + max(child.max_depth() for child in self.children)

    def flatten_actions(self) -> list["BTNodeModel"]:
        out: list[BTNodeModel] = []
        if self.node_type == BTNodeType.ACTION:
            out.append(self)
        for child in self.children:
            out.extend(child.flatten_actions())
        return out


BTNodeModel.model_rebuild()


class BehaviorTreeModel(BaseModel):
    """A complete behaviour tree associated with an automation flow."""

    model_config = ConfigDict(extra="ignore")

    tree_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str | None = None
    description: str | None = None
    root: BTNodeModel
    source_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

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
        return {
            "tree_id": self.tree_id,
            "name": self.name,
            "description": self.description,
            "root": self.root.model_dump(),
            "stats": {
                "total_nodes": self.total_nodes,
                "depth": self.depth,
                "action_count": self.action_count,
            },
        }
