from src.db.models import DesktopSnapshotModel
from src.models.automation import AutomationFlow, ErrorHandlingPolicy


class TestAutomationFlowBehaviorTree:
    def test_behavior_tree_default_none(self):
        flow = AutomationFlow(
            id="f1",
            name="Test",
            steps=[],
            error_handling=ErrorHandlingPolicy(),
        )
        assert flow.behavior_tree is None

    def test_behavior_tree_stored(self):
        bt = {"node_type": "sequence", "children": [{"node_type": "action", "label": "click"}]}
        flow = AutomationFlow(
            id="f1",
            name="Test",
            steps=[],
            error_handling=ErrorHandlingPolicy(),
            behavior_tree=bt,
        )
        assert flow.behavior_tree is not None
        assert flow.behavior_tree["node_type"] == "sequence"
        assert len(flow.behavior_tree["children"]) == 1

    def test_behavior_tree_serialized_in_dump(self):
        bt = {"node_type": "sequence", "children": []}
        flow = AutomationFlow(
            id="f1",
            name="Test",
            steps=[],
            error_handling=ErrorHandlingPolicy(),
            behavior_tree=bt,
        )
        data = flow.model_dump()
        assert "behavior_tree" in data
        assert data["behavior_tree"]["node_type"] == "sequence"


class TestDesktopSnapshotModelFields:
    def test_model_has_new_columns(self):
        columns = {c.name for c in DesktopSnapshotModel.__table__.columns}
        assert "active_window_class" in columns
        assert "active_window_process" in columns
        assert "active_window_bounds" in columns
        assert "window_list" in columns

    def test_model_has_original_columns(self):
        columns = {c.name for c in DesktopSnapshotModel.__table__.columns}
        assert "id" in columns
        assert "timestamp" in columns
        assert "active_window_title" in columns
        assert "active_window_app" in columns
        assert "screenshot_path" in columns
