from src.analyzer.pattern_detector import MIN_PATTERN_LENGTH, MIN_SUPPORT, PatternDetector
from src.analyzer.preprocessor import NormalizedOperation
from src.models.automation import StepType


def _make_norm(op_type: str, timestamp: int, seq_num: int, **data) -> NormalizedOperation:
    return NormalizedOperation(
        op_type=op_type,
        data={"x": data.get("x", 100), "y": data.get("y", 200), **data},
        timestamp=timestamp,
        seq_num=seq_num,
    )


class TestPatternDetectorEmptyInput:
    def test_empty_operations_returns_empty(self):
        detector = PatternDetector()
        result = detector.detect_patterns([])
        assert result == []

    def test_single_operation_returns_empty(self):
        ops = [_make_norm("mouse_click", 1000, 0)]
        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        assert result == []

    def test_insufficient_for_min_support_returns_empty(self):
        ops = [_make_norm("mouse_click", 1000 + i * 500, i) for i in range(MIN_PATTERN_LENGTH)]
        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        assert result == []


class TestPatternDetectorRepeatingPatterns:
    def test_detects_exact_repeat_pattern(self):
        ops = []
        base_ts = 1000
        for cycle in range(4):
            offset = cycle * 3000
            ops.append(_make_norm("mouse_click", base_ts + offset, len(ops)))
            ops.append(_make_norm("type_text", base_ts + offset + 500, len(ops)))
            ops.append(_make_norm("mouse_click", base_ts + offset + 1500, len(ops)))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        assert len(result) > 0
        assert result[0].pattern_type == "exact_repeat"
        assert result[0].support >= MIN_SUPPORT

    def test_pattern_sequence_contains_step_types(self):
        ops = []
        base_ts = 1000
        for cycle in range(3):
            offset = cycle * 3000
            ops.append(_make_norm("mouse_click", base_ts + offset, len(ops)))
            ops.append(_make_norm("type_text", base_ts + offset + 500, len(ops)))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        if result:
            for step in result[0].pattern_sequence:
                assert isinstance(step, StepType)

    def test_pattern_confidence_is_calculated(self):
        ops = []
        base_ts = 1000
        for cycle in range(5):
            offset = cycle * 2000
            ops.append(_make_norm("mouse_click", base_ts + offset, len(ops)))
            ops.append(_make_norm("type_text", base_ts + offset + 500, len(ops)))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        if result:
            assert 0.0 <= result[0].confidence <= 1.0

    def test_returns_at_most_10_patterns(self):
        ops = []
        base_ts = 1000
        for i in range(50):
            ops.append(_make_norm("mouse_click", base_ts + i * 200, i))
            ops.append(_make_norm("type_text", base_ts + i * 200 + 100, i + 50))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        assert len(result) <= 10

    def test_overlapping_subsequences_collapse_to_longest_pattern(self):
        ops = []
        base_ts = 1000
        sequence = [
            "switch_window",
            "mouse_click",
            "switch_window",
            "mouse_click",
            "switch_window",
            "mouse_click",
            "switch_window",
        ]
        for index, op_type in enumerate(sequence):
            ops.append(_make_norm(op_type, base_ts + index * 500, index))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)

        assert len(result) == 2
        for pattern in result:
            for other in result:
                if pattern.id == other.id:
                    continue
                assert not detector._is_subsequence(pattern.pattern_sequence, other.pattern_sequence)


class TestPatternDetectorTimeConstraints:
    def test_patterns_with_large_time_gap_filtered(self):
        ops = []
        ops.append(_make_norm("mouse_click", 1000, 0))
        ops.append(_make_norm("type_text", 2000, 1))
        ops.append(_make_norm("mouse_click", 100000, 2))
        ops.append(_make_norm("type_text", 101000, 3))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        for pattern in result:
            for _instance in pattern.instances:
                assert pattern.avg_duration_ms <= 10000 or len(pattern.instances) < MIN_SUPPORT

    def test_pattern_avg_duration_calculated(self):
        ops = []
        base_ts = 1000
        for cycle in range(3):
            offset = cycle * 3000
            ops.append(_make_norm("mouse_click", base_ts + offset, len(ops)))
            ops.append(_make_norm("type_text", base_ts + offset + 500, len(ops)))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        if result:
            assert result[0].avg_duration_ms > 0


class TestPatternDetectorValidation:
    def test_all_click_subsequence_filtered(self):
        ops = []
        base_ts = 1000
        for cycle in range(3):
            offset = cycle * 2000
            for j in range(4):
                ops.append(_make_norm("mouse_click", base_ts + offset + j * 100, len(ops)))

        detector = PatternDetector()
        result = detector.detect_patterns(ops)
        for pattern in result:
            unique_types = len(set(pattern.pattern_sequence))
            if unique_types == 1 and len(pattern.pattern_sequence) < 2:
                assert False, "Single-type subsequence of length 1 should be filtered"

    def test_to_step_sequence_maps_correctly(self):
        ops = [
            _make_norm("mouse_click", 1000, 0),
            _make_norm("type_text", 2000, 1),
            _make_norm("hotkey", 3000, 2),
            _make_norm("switch_window", 4000, 3),
        ]
        detector = PatternDetector()
        sequence = detector._to_step_sequence(ops)
        assert sequence[0] == StepType.CLICK
        assert sequence[1] == StepType.TYPE
        assert sequence[2] == StepType.HOTKEY
        assert sequence[3] == StepType.SWITCH_WINDOW
