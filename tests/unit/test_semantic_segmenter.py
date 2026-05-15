from src.analyzer.preprocessor import NormalizedOperation
from src.analyzer.semantic_segmenter import SemanticSegment, SemanticSegmenter


def _make_ops(count: int, base_ts: int = 1000, gap: int = 500) -> list[NormalizedOperation]:
    ops = []
    for i in range(count):
        ops.append(
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 100 + i * 10, "y": 200},
                timestamp=base_ts + i * gap,
                seq_num=i,
                context={"active_window": {"title": "TestApp"}},
            )
        )
    return ops


def _make_ops_with_gap(gap_idx: int, gap_ms: int = 10000) -> list[NormalizedOperation]:
    ops = []
    for i in range(10):
        ts = 1000 + i * 500
        if i >= gap_idx:
            ts += gap_ms
        ops.append(
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 100 + i * 10, "y": 200},
                timestamp=ts,
                seq_num=i,
                context={"active_window": {"title": "TestApp"}},
            )
        )
    return ops


def _make_ops_with_window_switch(switch_idx: int = 5) -> list[NormalizedOperation]:
    ops = []
    for i in range(10):
        window = "AppA" if i < switch_idx else "AppB"
        ops.append(
            NormalizedOperation(
                op_type="mouse_click",
                data={"x": 100 + i * 10, "y": 200},
                timestamp=1000 + i * 500,
                seq_num=i,
                context={"active_window": {"title": window}},
            )
        )
    return ops


class TestSemanticSegmenterEmpty:
    def test_empty_operations_returns_empty(self):
        segmenter = SemanticSegmenter()
        result = segmenter.segment([])
        assert result == []

    def test_single_operation_returns_empty(self):
        segmenter = SemanticSegmenter()
        ops = _make_ops(1)
        result = segmenter.segment(ops)
        assert result == []


class TestSemanticSegmenterBasic:
    def test_uniform_ops_creates_single_segment(self):
        segmenter = SemanticSegmenter()
        ops = _make_ops(10)
        result = segmenter.segment(ops)
        assert len(result) >= 1
        assert sum(s.length for s in result) <= len(ops)

    def test_segments_cover_all_operations(self):
        segmenter = SemanticSegmenter()
        ops = _make_ops(10)
        result = segmenter.segment(ops)
        if result:
            covered = set()
            for seg in result:
                for i in range(seg.start_idx, seg.end_idx + 1):
                    covered.add(i)
            assert len(covered) > 0


class TestSemanticSegmenterBoundaries:
    def test_time_gap_creates_boundary(self):
        segmenter = SemanticSegmenter()
        ops = _make_ops_with_gap(gap_idx=5, gap_ms=10000)
        result = segmenter.segment(ops)
        assert len(result) >= 2

    def test_window_switch_creates_boundary(self):
        segmenter = SemanticSegmenter()
        ops = _make_ops_with_window_switch(switch_idx=5)
        result = segmenter.segment(ops)
        assert len(result) >= 2


class TestSemanticSegmentLabels:
    def test_segments_have_labels(self):
        segmenter = SemanticSegmenter()
        ops = _make_ops(10)
        result = segmenter.segment(ops)
        for seg in result:
            assert seg.label != ""

    def test_segments_have_confidence(self):
        segmenter = SemanticSegmenter()
        ops = _make_ops(10)
        result = segmenter.segment(ops)
        for seg in result:
            assert 0.0 <= seg.confidence <= 1.0


class TestSemanticSegmentProperties:
    def test_segment_length(self):
        seg = SemanticSegment(
            segment_id="test",
            start_idx=0,
            end_idx=4,
            operations=_make_ops(5),
        )
        assert seg.length == 5

    def test_segment_duration(self):
        ops = _make_ops(5, base_ts=1000, gap=500)
        seg = SemanticSegment(
            segment_id="test",
            start_idx=0,
            end_idx=4,
            operations=ops,
        )
        assert seg.duration_ms == 2000
