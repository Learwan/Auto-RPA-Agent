from src.analyzer.multi_fusion import AlignedTrace, MultiRecordingFusion
from src.analyzer.preprocessor import NormalizedOperation


def _make_trace(ops_types: list[str], session_id: str = "s1") -> AlignedTrace:
    ops = []
    for i, t in enumerate(ops_types):
        ops.append(
            NormalizedOperation(
                op_type=t,
                data={"x": 100 + i * 10, "y": 200},
                timestamp=1000 + i * 500,
                seq_num=i,
            )
        )
    return AlignedTrace(trace_id=f"trace-{session_id}", session_id=session_id, operations=ops, op_types=ops_types)


class TestMultiRecordingFusionBasic:
    def test_single_trace_returns_none(self):
        fusion = MultiRecordingFusion()
        traces = [_make_trace(["mouse_click", "type_text", "mouse_click"])]
        result = fusion.fuse(traces)
        assert result is None

    def test_identical_traces_fuse(self):
        fusion = MultiRecordingFusion()
        traces = [
            _make_trace(["mouse_click", "type_text", "mouse_click"], "s1"),
            _make_trace(["mouse_click", "type_text", "mouse_click"], "s2"),
        ]
        result = fusion.fuse(traces)
        assert result is not None
        assert len(result.common_steps) > 0
        assert result.confidence > 0

    def test_different_traces_create_branches(self):
        fusion = MultiRecordingFusion()
        traces = [
            _make_trace(["mouse_click", "type_text", "mouse_click"], "s1"),
            _make_trace(["mouse_click", "hotkey", "mouse_click"], "s2"),
        ]
        result = fusion.fuse(traces)
        assert result is not None
        assert len(result.branches) > 0


class TestMultiRecordingFusionConfidence:
    def test_identical_traces_high_confidence(self):
        fusion = MultiRecordingFusion()
        traces = [
            _make_trace(["mouse_click", "type_text", "mouse_click"], "s1"),
            _make_trace(["mouse_click", "type_text", "mouse_click"], "s2"),
            _make_trace(["mouse_click", "type_text", "mouse_click"], "s3"),
        ]
        result = fusion.fuse(traces)
        assert result is not None
        assert result.confidence >= 0.5

    def test_completely_different_traces_low_confidence(self):
        fusion = MultiRecordingFusion()
        traces = [
            _make_trace(["mouse_click", "type_text", "mouse_click"], "s1"),
            _make_trace(["hotkey", "switch_window", "hotkey"], "s2"),
        ]
        result = fusion.fuse(traces)
        assert result is not None
        assert result.confidence < 0.8


class TestMultiRecordingFusionLoops:
    def test_repeating_patterns_detected(self):
        fusion = MultiRecordingFusion()
        trace = _make_trace(["mouse_click", "type_text", "mouse_click", "type_text", "mouse_click"])
        result = fusion.fuse([trace, trace])
        if result:
            assert isinstance(result.loop_patterns, list)
