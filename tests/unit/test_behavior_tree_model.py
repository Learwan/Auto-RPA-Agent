import pytest

from src.models.behavior_tree import (
    BTCondition,
    BTNodeType,
    BTNodeModel,
    BehaviorTreeModel,
    DecoratorType,
)


class TestBTNodeModel:
    def test_action_node(self):
        node = BTNodeModel(node_id="n1", node_type=BTNodeType.ACTION, label="Click Save")
        assert node.node_type == BTNodeType.ACTION
        assert node.label == "Click Save"
        assert node.children == []

    def test_find_node_self(self):
        node = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE)
        found = node.find_node("root")
        assert found is node

    def test_find_node_child(self):
        child = BTNodeModel(node_id="child1", node_type=BTNodeType.ACTION)
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE, children=[child])
        found = root.find_node("child1")
        assert found is child

    def test_find_node_not_found(self):
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE)
        assert root.find_node("missing") is None

    def test_count_nodes(self):
        child1 = BTNodeModel(node_id="c1", node_type=BTNodeType.ACTION)
        child2 = BTNodeModel(node_id="c2", node_type=BTNodeType.ACTION)
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE, children=[child1, child2])
        assert root.count_nodes() == 3

    def test_count_by_type(self):
        action1 = BTNodeModel(node_id="a1", node_type=BTNodeType.ACTION)
        action2 = BTNodeModel(node_id="a2", node_type=BTNodeType.ACTION)
        cond = BTNodeModel(node_id="cond", node_type=BTNodeType.CONDITION)
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE, children=[action1, cond, action2])
        assert root.count_by_type(BTNodeType.ACTION) == 2
        assert root.count_by_type(BTNodeType.CONDITION) == 1
        assert root.count_by_type(BTNodeType.SELECTOR) == 0

    def test_max_depth(self):
        leaf = BTNodeModel(node_id="leaf", node_type=BTNodeType.ACTION)
        mid = BTNodeModel(node_id="mid", node_type=BTNodeType.SEQUENCE, children=[leaf])
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE, children=[mid])
        assert root.max_depth() == 3

    def test_flatten_actions(self):
        action1 = BTNodeModel(node_id="a1", node_type=BTNodeType.ACTION)
        cond = BTNodeModel(node_id="c1", node_type=BTNodeType.CONDITION)
        action2 = BTNodeModel(node_id="a2", node_type=BTNodeType.ACTION)
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE, children=[action1, cond, action2])
        actions = root.flatten_actions()
        assert len(actions) == 2
        assert actions[0].node_id == "a1"
        assert actions[1].node_id == "a2"


class TestBTCondition:
    def test_creation(self):
        cond = BTCondition(field="window_title", operator="contains", value="Chrome")
        assert cond.field == "window_title"
        assert cond.operator == "contains"


class TestBehaviorTreeModel:
    def _make_tree(self):
        action1 = BTNodeModel(node_id="a1", node_type=BTNodeType.ACTION, label="Open App")
        action2 = BTNodeModel(node_id="a2", node_type=BTNodeType.ACTION, label="Click Save")
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE, children=[action1, action2])
        return BehaviorTreeModel(tree_id="bt-1", name="Test Tree", root=root)

    def test_total_nodes(self):
        tree = self._make_tree()
        assert tree.total_nodes == 3

    def test_depth(self):
        tree = self._make_tree()
        assert tree.depth == 2

    def test_action_count(self):
        tree = self._make_tree()
        assert tree.action_count == 2

    def test_find_node(self):
        tree = self._make_tree()
        found = tree.find_node("a1")
        assert found is not None
        assert found.label == "Open App"

    def test_get_action_nodes(self):
        tree = self._make_tree()
        actions = tree.get_action_nodes()
        assert len(actions) == 2

    def test_to_flow_dict(self):
        tree = self._make_tree()
        d = tree.to_flow_dict()
        assert d["tree_id"] == "bt-1"
        assert d["name"] == "Test Tree"
        assert "root" in d
        assert d["stats"]["total_nodes"] == 3
        assert d["stats"]["depth"] == 2
        assert d["stats"]["action_count"] == 2

    def test_decorator_node(self):
        action = BTNodeModel(node_id="a1", node_type=BTNodeType.ACTION, label="Retry Me")
        decorator = BTNodeModel(
            node_id="d1",
            node_type=BTNodeType.DECORATOR,
            decorator_type=DecoratorType.RETRY,
            decorator_params={"max_attempts": 5},
            children=[action],
        )
        root = BTNodeModel(node_id="root", node_type=BTNodeType.SEQUENCE, children=[decorator])
        tree = BehaviorTreeModel(tree_id="bt-2", root=root)
        assert tree.total_nodes == 3
