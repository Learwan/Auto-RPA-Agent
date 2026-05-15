import pytest

from src.models.segment import SegmentBoundary, SegmentFeature, SegmentResult, SemanticSegmentModel


class TestSemanticSegmentModel:
    def test_creation(self):
        seg = SemanticSegmentModel(
            segment_id="seg-1",
            session_id="sess-1",
            start_idx=0,
            end_idx=5,
            label="Click operations",
            confidence=0.85,
        )
        assert seg.segment_id == "seg-1"
        assert seg.start_idx == 0
        assert seg.end_idx == 5

    def test_length_property(self):
        seg = SemanticSegmentModel(segment_id="s1", start_idx=0, end_idx=9)
        assert seg.length == 10

    def test_boundary_type_default(self):
        seg = SemanticSegmentModel(segment_id="s1", start_idx=0, end_idx=5)
        assert seg.boundary_type == SegmentBoundary.HARD

    def test_to_segment_dict(self):
        seg = SemanticSegmentModel(
            segment_id="s1",
            session_id="sess-1",
            start_idx=0,
            end_idx=5,
            label="Test",
            confidence=0.9,
        )
        d = seg.to_segment_dict()
        assert d["segment_id"] == "s1"
        assert d["label"] == "Test"
        assert d["boundary_type"] == "hard"
        assert d["confidence"] == 0.9


class TestSegmentFeature:
    def test_defaults(self):
        feat = SegmentFeature()
        assert feat.dominant_op_type == ""
        assert feat.type_uniformity == 0.0
        assert feat.window_titles == []

    def test_custom_values(self):
        feat = SegmentFeature(
            dominant_op_type="click",
            type_uniformity=0.8,
            window_titles=["Chrome", "VSCode"],
        )
        assert feat.dominant_op_type == "click"
        assert len(feat.window_titles) == 2


class TestSegmentResult:
    def test_empty_result(self):
        result = SegmentResult(session_id="sess-1")
        result.compute_stats()
        assert result.avg_confidence == 0.0
        assert result.boundary_count == 0

    def test_compute_stats(self):
        segments = [
            SemanticSegmentModel(
                segment_id="s1", start_idx=0, end_idx=5, confidence=0.8, boundary_type=SegmentBoundary.HARD
            ),
            SemanticSegmentModel(
                segment_id="s2", start_idx=6, end_idx=10, confidence=0.6, boundary_type=SegmentBoundary.SOFT
            ),
        ]
        result = SegmentResult(session_id="sess-1", segments=segments, total_operations=11)
        result.compute_stats()
        assert result.avg_confidence == pytest.approx(0.7, abs=0.01)
        assert result.boundary_count == 1
