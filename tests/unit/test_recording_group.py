import pytest

from src.models.recording_group import (
    FusionResult,
    FusedBranchModel,
    LoopPatternModel,
    RecordingGroup,
    RecordingSession,
    RecordingStatus,
)


class TestRecordingSession:
    def test_creation(self):
        session = RecordingSession(session_id="s1", name="Test Recording")
        assert session.status == RecordingStatus.PENDING

    def test_completed_status(self):
        session = RecordingSession(session_id="s1", status=RecordingStatus.COMPLETED)
        assert session.status == RecordingStatus.COMPLETED


class TestRecordingGroup:
    def test_creation(self):
        group = RecordingGroup(group_id="g1", name="Test Group")
        assert group.sessions == []
        assert group.min_recordings == 2
        assert group.status == "collecting"

    def test_completed_count(self):
        group = RecordingGroup(group_id="g1")
        group.sessions = [
            RecordingSession(session_id="s1", status=RecordingStatus.COMPLETED),
            RecordingSession(session_id="s2", status=RecordingStatus.RECORDING),
            RecordingSession(session_id="s3", status=RecordingStatus.COMPLETED),
        ]
        assert group.completed_count == 2

    def test_is_ready_for_fusion(self):
        group = RecordingGroup(group_id="g1", min_recordings=2)
        group.sessions = [
            RecordingSession(session_id="s1", status=RecordingStatus.COMPLETED),
            RecordingSession(session_id="s2", status=RecordingStatus.COMPLETED),
        ]
        assert group.is_ready_for_fusion is True

    def test_not_ready_for_fusion(self):
        group = RecordingGroup(group_id="g1", min_recordings=3)
        group.sessions = [
            RecordingSession(session_id="s1", status=RecordingStatus.COMPLETED),
            RecordingSession(session_id="s2", status=RecordingStatus.RECORDING),
        ]
        assert group.is_ready_for_fusion is False

    def test_add_session(self):
        group = RecordingGroup(group_id="g1")
        session = RecordingSession(session_id="s1", name="Test")
        group.add_session(session)
        assert len(group.sessions) == 1

    def test_add_session_no_duplicate(self):
        group = RecordingGroup(group_id="g1")
        session = RecordingSession(session_id="s1", name="Test")
        group.add_session(session)
        group.add_session(session)
        assert len(group.sessions) == 1

    def test_remove_session(self):
        group = RecordingGroup(group_id="g1")
        group.sessions = [RecordingSession(session_id="s1"), RecordingSession(session_id="s2")]
        group.remove_session("s1")
        assert len(group.sessions) == 1
        assert group.sessions[0].session_id == "s2"


class TestFusedBranchModel:
    def test_creation(self):
        branch = FusedBranchModel(
            branch_id="b1",
            condition="window_title == 'Chrome'",
            trace_ids=["s1", "s2"],
            confidence=0.85,
        )
        assert branch.condition == "window_title == 'Chrome'"
        assert len(branch.trace_ids) == 2


class TestLoopPatternModel:
    def test_creation(self):
        loop = LoopPatternModel(
            pattern_id="lp1",
            sequence="click_type_click",
            occurrences=3,
            positions=[0, 5, 10],
        )
        assert loop.occurrences == 3
        assert len(loop.positions) == 3


class TestFusionResult:
    def test_total_divergences(self):
        result = FusionResult(
            result_id="r1",
            group_id="g1",
            branches=[
                FusedBranchModel(branch_id="b1", condition="A"),
                FusedBranchModel(branch_id="b2", condition="B"),
            ],
        )
        assert result.total_divergences == 2

    def test_common_step_ratio(self):
        result = FusionResult(
            result_id="r1",
            group_id="g1",
            common_steps=[{"type": "click"}, {"type": "type"}],
            branches=[FusedBranchModel(branch_id="b1", steps=[{"type": "scroll"}])],
        )
        assert result.common_step_ratio == pytest.approx(2 / 3, abs=0.01)

    def test_empty_result_ratio(self):
        result = FusionResult(result_id="r1", group_id="g1")
        assert result.common_step_ratio == 0.0
