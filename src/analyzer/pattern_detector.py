import uuid
from collections import defaultdict

from src.analyzer.preprocessor import NormalizedOperation
from src.models.automation import DetectedPattern, StepType

MIN_PATTERN_LENGTH = 2
MAX_PATTERN_LENGTH = 20
MIN_SUPPORT = 2
MAX_INSTANCES_PER_PATTERN = 100
POSITION_CLUSTER_RADIUS = 30
WINDOW_CONTEXT_WEIGHT = 0.15
TEMPORAL_CONSISTENCY_WEIGHT = 0.15
SUPPORT_WEIGHT = 0.4
LENGTH_WEIGHT = 0.15
SEMANTIC_COHESION_WEIGHT = 0.15

OP_TYPE_TO_STEP_TYPE = {
    "mouse_click": StepType.CLICK,
    "mouse_right_click": StepType.CLICK,
    "mouse_double_click": StepType.CLICK,
    "mouse_scroll": StepType.SCROLL,
    "mouse_drag_end": StepType.DRAG,
    "type_text": StepType.TYPE,
    "hotkey": StepType.HOTKEY,
    "switch_window": StepType.SWITCH_WINDOW,
    "navigation": StepType.NAVIGATE,
    "upload_file": StepType.UPLOAD_FILE,
    "download_file": StepType.DOWNLOAD_FILE,
    "file_op": StepType.FILE_OP,
    "clipboard": StepType.CLICK,
}

INVALID_SUBSEQUENCE_SETS = [
    {StepType.CLICK},
    {StepType.WAIT},
    {StepType.SCROLL},
    {StepType.CLICK, StepType.WAIT},
]


class PatternDetector:
    def detect_patterns(self, operations: list[NormalizedOperation]) -> list[DetectedPattern]:
        if len(operations) < MIN_PATTERN_LENGTH * MIN_SUPPORT:
            return []
        step_sequence = self._to_step_sequence(operations)
        if len(step_sequence) < MIN_PATTERN_LENGTH * MIN_SUPPORT:
            return []
        time_gap_threshold = self._adaptive_time_gap(operations)
        patterns = self._find_repeating_patterns(step_sequence, operations, time_gap_threshold)
        patterns = self._merge_overlapping_patterns(patterns)
        return self._filter_and_rank(patterns, operations)

    def _adaptive_time_gap(self, operations: list[NormalizedOperation]) -> int:
        if len(operations) < 2:
            return 10000
        gaps = []
        for i in range(1, len(operations)):
            gap = operations[i].timestamp - operations[i - 1].timestamp
            if gap > 0:
                gaps.append(gap)
        if not gaps:
            return 10000
        gaps.sort()
        p75 = gaps[int(len(gaps) * 0.75)]
        p95 = gaps[int(len(gaps) * 0.95)]
        threshold = max(p95 * 2, p75 * 5, 3000)
        return min(threshold, 30000)

    def _to_step_sequence(self, operations: list[NormalizedOperation]) -> list[StepType]:
        result = []
        for op in operations:
            step_type = OP_TYPE_TO_STEP_TYPE.get(op.op_type)
            if step_type:
                result.append(step_type)
        return result

    def _find_repeating_patterns(
        self,
        sequence: list[StepType],
        operations: list[NormalizedOperation],
        time_gap_threshold: int,
    ) -> list[DetectedPattern]:
        patterns: list[DetectedPattern] = []
        n = len(sequence)
        max_len = min(MAX_PATTERN_LENGTH + 1, n // MIN_SUPPORT + 1)

        for length in range(MIN_PATTERN_LENGTH, max_len):
            subsequences: dict[tuple, list[int]] = defaultdict(list)
            for i in range(n - length + 1):
                sub = tuple(sequence[i : i + length])
                if self._is_valid_subsequence(sub):
                    subsequences[sub].append(i)

            for sub, positions in subsequences.items():
                if len(positions) >= MIN_SUPPORT:
                    valid_positions = self._validate_time_constraints(
                        positions,
                        length,
                        operations,
                        time_gap_threshold,
                    )
                    if len(valid_positions) >= MIN_SUPPORT:
                        if len(valid_positions) > MAX_INSTANCES_PER_PATTERN:
                            valid_positions = valid_positions[:MAX_INSTANCES_PER_PATTERN]
                        pattern = DetectedPattern(
                            id=str(uuid.uuid4()),
                            pattern_sequence=list(sub),
                            instances=[{"start_idx": p, "end_idx": p + length - 1} for p in valid_positions],
                            support=len(valid_positions),
                            avg_duration_ms=self._calc_avg_duration(valid_positions, length, operations),
                            confidence=0.0,
                            pattern_type="exact_repeat",
                        )
                        patterns.append(pattern)

        return patterns

    def _is_valid_subsequence(self, sub: tuple) -> bool:
        sub_set = set(sub)
        if len(sub_set) == 1 and len(sub) >= 2:
            return True
        return not any(sub_set <= invalid_set for invalid_set in INVALID_SUBSEQUENCE_SETS)

    def _validate_time_constraints(
        self,
        positions: list[int],
        length: int,
        operations: list[NormalizedOperation],
        threshold: int,
    ) -> list[int]:
        valid = []
        for pos in positions:
            if pos + length - 1 < len(operations):
                start_ts = operations[pos].timestamp
                end_ts = operations[pos + length - 1].timestamp
                if end_ts - start_ts <= threshold:
                    valid.append(pos)
        return valid

    def _calc_avg_duration(self, positions: list[int], length: int, operations: list[NormalizedOperation]) -> int:
        durations = []
        for pos in positions:
            if pos + length - 1 < len(operations):
                d = operations[pos + length - 1].timestamp - operations[pos].timestamp
                durations.append(d)
        return int(sum(durations) / len(durations)) if durations else 0

    def _merge_overlapping_patterns(self, patterns: list[DetectedPattern]) -> list[DetectedPattern]:
        if len(patterns) <= 1:
            return patterns
        patterns.sort(key=lambda p: (len(p.pattern_sequence), p.support), reverse=True)
        merged = []
        used_ids: set[str] = set()
        for pattern in patterns:
            if pattern.id in used_ids:
                continue
            for other in patterns:
                if other.id == pattern.id or other.id in used_ids:
                    continue
                if self._is_subsequence(other.pattern_sequence, pattern.pattern_sequence):
                    used_ids.add(other.id)
            merged.append(pattern)
        return merged

    def _is_subsequence(self, shorter: list, longer: list) -> bool:
        if len(shorter) >= len(longer):
            return False
        return any(longer[i:i + len(shorter)] == shorter for i in range(len(longer) - len(shorter) + 1))

    def _filter_and_rank(
        self, patterns: list[DetectedPattern], operations: list[NormalizedOperation]
    ) -> list[DetectedPattern]:
        if not patterns:
            return []
        for p in patterns:
            p.confidence = self._compute_confidence(p, operations)
        patterns.sort(key=lambda p: p.confidence, reverse=True)
        seen_sequences: set[tuple] = set()
        filtered = []
        for p in patterns:
            key = tuple(p.pattern_sequence)
            if key not in seen_sequences:
                seen_sequences.add(key)
                filtered.append(p)
        return filtered[:10]

    def _compute_confidence(self, pattern: DetectedPattern, operations: list[NormalizedOperation]) -> float:
        support_score = min(pattern.support / 10.0, 1.0)
        length_score = min(len(pattern.pattern_sequence) / 15.0, 1.0)
        temporal_score = self._temporal_consistency(pattern)
        window_score = self._window_consistency(pattern, operations)
        semantic_score = self._semantic_cohesion(pattern)

        confidence = (
            support_score * SUPPORT_WEIGHT
            + length_score * LENGTH_WEIGHT
            + temporal_score * TEMPORAL_CONSISTENCY_WEIGHT
            + window_score * WINDOW_CONTEXT_WEIGHT
            + semantic_score * SEMANTIC_COHESION_WEIGHT
        )
        return round(min(confidence, 1.0), 4)

    def _temporal_consistency(self, pattern: DetectedPattern) -> float:
        if not pattern.instances or pattern.avg_duration_ms == 0:
            return 0.0
        if len(pattern.instances) < 2:
            return 0.5
        return min(1.0, 1.0 / (1.0 + abs(pattern.avg_duration_ms - 5000) / 10000))

    def _window_consistency(self, pattern: DetectedPattern, operations: list[NormalizedOperation]) -> float:
        if not pattern.instances:
            return 0.0
        window_counts: dict[str, int] = defaultdict(int)
        total = 0
        for inst in pattern.instances:
            start_idx = inst["start_idx"]
            if start_idx < len(operations):
                app = operations[start_idx].data.get("app_name", "unknown")
                window_counts[str(app)] += 1
                total += 1
        if total == 0:
            return 0.0
        max_count = max(window_counts.values())
        return max_count / total

    def _semantic_cohesion(self, pattern: DetectedPattern) -> float:
        seq = pattern.pattern_sequence
        if not seq:
            return 0.0
        type_set = set(seq)
        if len(type_set) == 1:
            return 0.3
        if len(type_set) <= 2:
            return 0.6
        if len(type_set) <= 4:
            return 0.8
        return 0.5
