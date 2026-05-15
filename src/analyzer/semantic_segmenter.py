from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from src.analyzer.preprocessor import NormalizedOperation

logger = logging.getLogger(__name__)

MIN_SEGMENT_LENGTH = 2
DEFAULT_WINDOW_SIZE = 30
DEFAULT_STRIDE = 5
DENSITY_EPS = 15.0
DENSITY_MIN_SAMPLES = 2
SPATIAL_THRESHOLD_PX = 200
DENSITY_WINDOW_SIZE = 5
DENSITY_SHORT_GAP_MS = 2000
DENSITY_MIN_DIFF = 2

HDBSCAN_MIN_CLUSTER = 3
HDBSCAN_MIN_SAMPLES = 2
ATTENTION_DEFAULT_WEIGHTS = {
    "time_gap": 0.30,
    "app_change": 0.25,
    "window_change": 0.15,
    "spatial_shift": 0.10,
    "type_change": 0.10,
    "density_change": 0.10,
}
CROSS_APP_MERGE_THRESHOLD = 0.6


class SegmentBoundary(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass
class SemanticSegment:
    segment_id: str
    start_idx: int
    end_idx: int
    operations: list[NormalizedOperation]
    label: str = ""
    boundary_type: SegmentBoundary = SegmentBoundary.HARD
    confidence: float = 0.0
    features: dict = field(default_factory=dict)
    sub_segments: list = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end_idx - self.start_idx + 1

    @property
    def duration_ms(self) -> int:
        if not self.operations:
            return 0
        return self.operations[-1].timestamp - self.operations[0].timestamp


@dataclass
class BoundaryCandidate:
    index: int
    score: float
    sources: dict[str, float] = field(default_factory=dict)


@dataclass
class AppTransition:
    from_app: str
    to_app: str
    index: int
    confidence: float


class AttentionBoundaryDetector:
    def __init__(self, weights: dict[str, float] | None = None, threshold: float = 0.45):
        self._weights = weights or ATTENTION_DEFAULT_WEIGHTS.copy()
        self._threshold = threshold
        self._weight_history: list[dict[str, float]] = []

    def detect(self, features: list[dict]) -> list[BoundaryCandidate]:
        candidates = []
        for i, feat in enumerate(features):
            if i == 0:
                continue

            signals = self._compute_signals(feat, features, i)
            weighted_score = sum(
                self._weights.get(name, 0.0) * value
                for name, value in signals.items()
            )

            if weighted_score >= self._threshold:
                candidates.append(BoundaryCandidate(
                    index=i,
                    score=weighted_score,
                    sources=signals,
                ))

        return candidates

    def adapt_weights(self, feedback: dict[str, float]) -> None:
        total = sum(feedback.values())
        if total <= 0:
            return
        normalized = {k: v / total for k, v in feedback.items()}
        for key in self._weights:
            if key in normalized:
                self._weights[key] = 0.7 * self._weights[key] + 0.3 * normalized[key]
        self._normalize_weights()
        self._weight_history.append(self._weights.copy())
        if len(self._weight_history) > 100:
            self._weight_history = self._weight_history[-100:]

    def _compute_signals(self, feat: dict, features: list[dict], idx: int) -> dict[str, float]:
        signals: dict[str, float] = {}

        time_gap = feat.get("time_gap", 0)
        all_gaps = [f["time_gap"] for f in features if f["time_gap"] > 0]
        if all_gaps and time_gap > 0:
            sorted_gaps = sorted(all_gaps)
            p75 = sorted_gaps[int(len(sorted_gaps) * 0.75)]
            p90 = sorted_gaps[int(len(sorted_gaps) * 0.90)]
            gap_threshold = max(p90 * 1.5, p75 * 4, 3000)
            signals["time_gap"] = min(time_gap / max(gap_threshold, 1), 1.0)
        else:
            signals["time_gap"] = 0.0

        signals["app_change"] = 1.0 if feat.get("app_change") else 0.0
        signals["window_change"] = 0.7 if feat.get("window_change") and not feat.get("app_change") else 0.0

        spatial = feat.get("spatial_shift", 0.0)
        signals["spatial_shift"] = min(spatial / (SPATIAL_THRESHOLD_PX * 2), 1.0)

        signals["type_change"] = 0.5 if feat.get("type_change") and spatial > SPATIAL_THRESHOLD_PX else 0.0

        density_score = self._compute_density_signal(features, idx)
        signals["density_change"] = density_score

        return signals

    def _compute_density_signal(self, features: list[dict], idx: int) -> float:
        if idx < 2 or idx >= len(features) - 2:
            return 0.0

        before = sum(
            1 for f in features[max(0, idx - DENSITY_WINDOW_SIZE):idx]
            if f["time_gap"] > 0 and f["time_gap"] < DENSITY_SHORT_GAP_MS
        )
        after = sum(
            1 for f in features[idx:min(len(features), idx + DENSITY_WINDOW_SIZE)]
            if f["time_gap"] > 0 and f["time_gap"] < DENSITY_SHORT_GAP_MS
        )

        diff = abs(before - after)
        if diff >= DENSITY_MIN_DIFF:
            return min(diff / (DENSITY_MIN_DIFF * 2), 1.0)
        return 0.0

    def _normalize_weights(self) -> None:
        total = sum(self._weights.values())
        if total > 0:
            for key in self._weights:
                self._weights[key] /= total


class SimplifiedHDBSCAN:
    def __init__(self, min_cluster_size: int = HDBSCAN_MIN_CLUSTER, min_samples: int = HDBSCAN_MIN_SAMPLES):
        self._min_cluster_size = min_cluster_size
        self._min_samples = min_samples

    def fit_predict(self, feature_vectors: list[list[float]]) -> list[int]:
        n = len(feature_vectors)
        if n < self._min_cluster_size:
            return [-1] * n

        core_distances = self._compute_core_distances(feature_vectors)
        mutual_reachability = self._compute_mutual_reachability(feature_vectors, core_distances)
        sorted_edges = self._compute_mst_edges(mutual_reachability, n)
        sorted_edges.sort(key=lambda e: e[2])

        parent = list(range(n))
        rank = [0] * n

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if rank[ra] < rank[rb]:
                ra, rb = rb, ra
            parent[rb] = ra
            if rank[ra] == rank[rb]:
                rank[ra] += 1

        for i, j, _d in sorted_edges:
            union(i, j)

        cluster_map: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            root = find(i)
            cluster_map[root].append(i)

        labels = [-1] * n
        cluster_id = 0
        for _root, members in cluster_map.items():
            if len(members) >= self._min_cluster_size:
                for m in members:
                    labels[m] = cluster_id
                cluster_id += 1

        return labels

    def _compute_core_distances(self, vectors: list[list[float]]) -> list[float]:
        n = len(vectors)
        core_distances = [0.0] * n
        k = min(self._min_samples, n - 1)

        for i in range(n):
            distances = []
            for j in range(n):
                if i != j:
                    d = self._euclidean(vectors[i], vectors[j])
                    distances.append(d)
            distances.sort()
            core_distances[i] = distances[k - 1] if k > 0 and len(distances) >= k else 0.0

        return core_distances

    def _compute_mutual_reachability(
        self, vectors: list[list[float]], core_distances: list[float]
    ) -> list[list[float]]:
        n = len(vectors)
        mr = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = self._euclidean(vectors[i], vectors[j])
                mr_val = max(core_distances[i], core_distances[j], d)
                mr[i][j] = mr_val
                mr[j][i] = mr_val
        return mr

    def _compute_mst_edges(self, mr: list[list[float]], n: int) -> list[tuple[int, int, float]]:
        edges = []
        for i in range(n):
            for j in range(i + 1, n):
                edges.append((i, j, mr[i][j]))
        return edges

    @staticmethod
    def _euclidean(a: list[float], b: list[float]) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=False)))


class CrossAppSegmentMerger:
    def __init__(self, merge_threshold: float = CROSS_APP_MERGE_THRESHOLD):
        self._merge_threshold = merge_threshold

    def detect_transitions(self, features: list[dict]) -> list[AppTransition]:
        transitions = []
        for i, feat in enumerate(features):
            if feat.get("app_change") and feat.get("app_name"):
                prev_app = ""
                if i > 0:
                    prev_app = features[i - 1].get("app_name", "")
                transitions.append(AppTransition(
                    from_app=prev_app,
                    to_app=feat["app_name"],
                    index=i,
                    confidence=0.8 if feat.get("time_gap", 0) > 3000 else 0.5,
                ))
        return transitions

    def merge_related_segments(
        self, segments: list[SemanticSegment], features: list[dict]
    ) -> list[SemanticSegment]:
        if len(segments) <= 1:
            return segments

        transitions = self.detect_transitions(features)
        if not transitions:
            return segments

        transition_indices = {t.index for t in transitions}

        merged = []
        skip = set()

        for i, seg in enumerate(segments):
            if i in skip:
                continue

            current = seg
            j = i + 1
            while j < len(segments):
                next_seg = segments[j]
                boundary_idx = current.end_idx + 1
                if boundary_idx not in transition_indices:
                    break

                similarity = self._compute_segment_similarity(current, next_seg)
                if similarity >= self._merge_threshold:
                    current = self._merge_two(current, next_seg)
                    skip.add(j)
                    j += 1
                else:
                    break

            merged.append(current)

        return merged

    def _compute_segment_similarity(self, seg_a: SemanticSegment, seg_b: SemanticSegment) -> float:
        type_overlap = self._type_overlap(seg_a, seg_b)
        time_gap = self._time_gap_score(seg_a, seg_b)
        return type_overlap * 0.6 + time_gap * 0.4

    def _type_overlap(self, seg_a: SemanticSegment, seg_b: SemanticSegment) -> float:
        types_a = set(op.op_type for op in seg_a.operations)
        types_b = set(op.op_type for op in seg_b.operations)
        if not types_a or not types_b:
            return 0.0
        intersection = types_a & types_b
        union = types_a | types_b
        return len(intersection) / len(union)

    def _time_gap_score(self, seg_a: SemanticSegment, seg_b: SemanticSegment) -> float:
        if not seg_a.operations or not seg_b.operations:
            return 0.0
        gap = seg_b.operations[0].timestamp - seg_a.operations[-1].timestamp
        if gap < 1000:
            return 1.0
        if gap < 5000:
            return 0.7
        if gap < 10000:
            return 0.3
        return 0.0

    def _merge_two(self, seg_a: SemanticSegment, seg_b: SemanticSegment) -> SemanticSegment:
        return SemanticSegment(
            segment_id=str(uuid.uuid4()),
            start_idx=seg_a.start_idx,
            end_idx=seg_b.end_idx,
            operations=seg_a.operations + seg_b.operations,
            boundary_type=seg_a.boundary_type,
            confidence=min(seg_a.confidence, seg_b.confidence),
            features={"merged": True, "source_segments": [seg_a.segment_id, seg_b.segment_id]},
            sub_segments=seg_a.sub_segments + seg_b.sub_segments,
        )


class SemanticSegmenter:
    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        stride: int = DEFAULT_STRIDE,
        density_eps: float = DENSITY_EPS,
        density_min_samples: int = DENSITY_MIN_SAMPLES,
        use_hdbscan: bool = True,
        use_attention: bool = True,
        use_cross_app_merge: bool = True,
    ):
        self._window_size = window_size
        self._stride = stride
        self._density_eps = density_eps
        self._density_min_samples = density_min_samples
        self._use_hdbscan = use_hdbscan
        self._use_attention = use_attention
        self._use_cross_app_merge = use_cross_app_merge
        self._attention_detector = AttentionBoundaryDetector()
        self._hdbscan = SimplifiedHDBSCAN()
        self._cross_app_merger = CrossAppSegmentMerger()

    def segment(self, operations: list[NormalizedOperation]) -> list[SemanticSegment]:
        if len(operations) < MIN_SEGMENT_LENGTH:
            return []

        features = self._extract_features(operations)

        heuristic_boundaries = self._detect_coarse_boundaries(features, operations)

        enhanced_boundaries = self._enhance_boundaries(features, operations, heuristic_boundaries)

        segments = self._create_segments(operations, enhanced_boundaries)

        if self._use_cross_app_merge:
            segments = self._cross_app_merger.merge_related_segments(segments, features)

        for seg in segments:
            if seg.length > 10:
                sub_features = self._extract_features(seg.operations)
                fine_boundaries = self._detect_fine_boundaries(sub_features, seg.operations)
                if fine_boundaries:
                    seg.sub_segments = self._create_segments(seg.operations, fine_boundaries)

        self._label_segments(segments)
        return segments

    def adapt_attention_weights(self, feedback: dict[str, float]) -> None:
        self._attention_detector.adapt_weights(feedback)

    def _extract_features(self, operations: list[NormalizedOperation]) -> list[dict]:
        features = []
        for i, op in enumerate(operations):
            ctx = op.context or {}
            active_window = ctx.get("active_window") or {}
            feature = {
                "idx": i,
                "op_type": op.op_type,
                "timestamp": op.timestamp,
                "x": op.data.get("x", 0) if op.data else 0,
                "y": op.data.get("y", 0) if op.data else 0,
                "window_title": active_window.get("title", ""),
                "app_name": active_window.get("app_name", ""),
                "time_gap": 0,
                "spatial_shift": 0.0,
                "type_change": False,
                "window_change": False,
                "app_change": False,
            }
            if i > 0:
                prev = operations[i - 1]
                feature["time_gap"] = op.timestamp - prev.timestamp
                prev_x = prev.data.get("x", 0) if prev.data else 0
                prev_y = prev.data.get("y", 0) if prev.data else 0
                feature["spatial_shift"] = ((feature["x"] - prev_x) ** 2 + (feature["y"] - prev_y) ** 2) ** 0.5
                feature["type_change"] = op.op_type != prev.op_type
                prev_ctx = prev.context or {}
                prev_active_window = prev_ctx.get("active_window") or {}
                prev_window = prev_active_window.get("title", "")
                prev_app = prev_active_window.get("app_name", "")
                feature["window_change"] = feature["window_title"] != prev_window and bool(feature["window_title"])
                feature["app_change"] = feature["app_name"] != prev_app and bool(feature["app_name"])
            features.append(feature)
        return features

    def _build_feature_vectors(self, features: list[dict]) -> list[list[float]]:
        time_gaps = [f["time_gap"] for f in features]
        max_gap = max(time_gaps) if time_gaps else 1.0
        if max_gap <= 0:
            max_gap = 1.0

        op_type_set: set[str] = set()
        for f in features:
            op_type_set.add(f.get("op_type", ""))
        op_type_list = sorted(op_type_set)
        op_type_map = {t: i for i, t in enumerate(op_type_list)}

        vectors = []
        for feat in features:
            normalized_gap = feat["time_gap"] / max_gap
            normalized_spatial = min(feat["spatial_shift"] / (SPATIAL_THRESHOLD_PX * 5), 1.0)
            app_change = 1.0 if feat["app_change"] else 0.0
            window_change = 1.0 if feat["window_change"] else 0.0
            type_change = 1.0 if feat["type_change"] else 0.0

            one_hot_type = [0.0] * len(op_type_map)
            idx = op_type_map.get(feat.get("op_type", ""), -1)
            if idx >= 0:
                one_hot_type[idx] = 1.0

            vector = [normalized_gap, normalized_spatial, app_change, window_change, type_change] + one_hot_type
            vectors.append(vector)

        return vectors

    def _enhance_boundaries(
        self, features: list[dict], operations: list[NormalizedOperation], heuristic_boundaries: list[int]
    ) -> list[int]:
        if not self._use_hdbscan and not self._use_attention:
            return heuristic_boundaries

        boundary_set = set(heuristic_boundaries)

        if self._use_hdbscan and len(features) >= HDBSCAN_MIN_CLUSTER * 2:
            cluster_boundaries = self._detect_cluster_boundaries(features)
            for b in cluster_boundaries:
                boundary_set.add(b)

        if self._use_attention:
            attention_candidates = self._attention_detector.detect(features)
            for candidate in attention_candidates:
                if candidate.score >= 0.55:
                    boundary_set.add(candidate.index)
                elif candidate.score >= 0.45:
                    nearby = any(abs(candidate.index - b) <= 2 for b in boundary_set)
                    if not nearby:
                        boundary_set.add(candidate.index)

        return sorted(boundary_set)

    def _detect_cluster_boundaries(self, features: list[dict]) -> list[int]:
        vectors = self._build_feature_vectors(features)
        if not vectors:
            return []

        labels = self._hdbscan.fit_predict(vectors)

        boundaries = []
        for i in range(1, len(labels)):
            if labels[i] != labels[i - 1] and (labels[i] != -1 or labels[i - 1] != -1):
                    boundaries.append(i)

        return boundaries

    def _detect_coarse_boundaries(self, features: list[dict], operations: list[NormalizedOperation]) -> list[int]:
        boundaries = []
        time_gaps = [f["time_gap"] for f in features if f["time_gap"] > 0]
        if not time_gaps:
            return boundaries

        time_gaps_sorted = sorted(time_gaps)
        p75 = time_gaps_sorted[int(len(time_gaps_sorted) * 0.75)]
        p90 = time_gaps_sorted[int(len(time_gaps_sorted) * 0.90)]
        gap_threshold = max(p90 * 1.5, p75 * 4, 3000)

        for i, feat in enumerate(features):
            is_boundary = False
            boundary_score = 0.0

            if feat["time_gap"] > gap_threshold:
                is_boundary = True
                boundary_score += 0.4

            if feat["app_change"]:
                is_boundary = True
                boundary_score += 0.35

            if feat["window_change"] and not feat["app_change"]:
                is_boundary = True
                boundary_score += 0.2

            if feat["type_change"] and feat["spatial_shift"] > SPATIAL_THRESHOLD_PX:
                is_boundary = True
                boundary_score += 0.15

            if i > 0 and self._is_density_boundary(features, i):
                is_boundary = True
                boundary_score += 0.2

            if is_boundary and i not in boundaries:
                boundaries.append(i)

        return sorted(boundaries)

    def _detect_fine_boundaries(self, features: list[dict], operations: list[NormalizedOperation]) -> list[int]:
        boundaries = []
        time_gaps = [f["time_gap"] for f in features if f["time_gap"] > 0]
        if not time_gaps:
            return boundaries

        time_gaps_sorted = sorted(time_gaps)
        p90 = time_gaps_sorted[int(len(time_gaps_sorted) * 0.90)] if time_gaps_sorted else 3000
        gap_threshold = max(p90 * 1.5, 2000)

        for i, feat in enumerate(features):
            is_boundary = False

            if feat["time_gap"] > gap_threshold:
                is_boundary = True

            if feat["window_change"]:
                is_boundary = True

            if feat["type_change"] and feat["spatial_shift"] > SPATIAL_THRESHOLD_PX * 1.5:
                is_boundary = True

            if is_boundary and i not in boundaries:
                boundaries.append(i)

        return sorted(boundaries)

    def _is_density_boundary(self, features: list[dict], idx: int) -> bool:
        if idx < 2 or idx >= len(features) - 2:
            return False

        before_density = sum(
            1
            for f in features[max(0, idx - DENSITY_WINDOW_SIZE) : idx]
            if f["time_gap"] > 0 and f["time_gap"] < DENSITY_SHORT_GAP_MS
        )
        after_density = sum(
            1
            for f in features[idx : min(len(features), idx + DENSITY_WINDOW_SIZE)]
            if f["time_gap"] > 0 and f["time_gap"] < DENSITY_SHORT_GAP_MS
        )

        return abs(before_density - after_density) >= DENSITY_MIN_DIFF

    def _create_segments(self, operations: list[NormalizedOperation], boundaries: list[int]) -> list[SemanticSegment]:
        if not boundaries:
            return [
                SemanticSegment(
                    segment_id=str(uuid.uuid4()),
                    start_idx=0,
                    end_idx=len(operations) - 1,
                    operations=operations,
                    boundary_type=SegmentBoundary.SOFT,
                )
            ]

        segments = []
        start = 0
        for _idx, boundary in enumerate(boundaries):
            if boundary > start:
                seg_ops = operations[start:boundary]
                if len(seg_ops) >= MIN_SEGMENT_LENGTH:
                    segments.append(
                        SemanticSegment(
                            segment_id=str(uuid.uuid4()),
                            start_idx=start,
                            end_idx=boundary - 1,
                            operations=seg_ops,
                            boundary_type=SegmentBoundary.HARD,
                        )
                    )
                start = boundary

        if start < len(operations):
            seg_ops = operations[start:]
            if len(seg_ops) >= MIN_SEGMENT_LENGTH:
                segments.append(
                    SemanticSegment(
                        segment_id=str(uuid.uuid4()),
                        start_idx=start,
                        end_idx=len(operations) - 1,
                        operations=seg_ops,
                        boundary_type=SegmentBoundary.SOFT,
                    )
                )

        if not segments and operations:
            segments.append(
                SemanticSegment(
                    segment_id=str(uuid.uuid4()),
                    start_idx=0,
                    end_idx=len(operations) - 1,
                    operations=operations,
                    boundary_type=SegmentBoundary.SOFT,
                )
            )

        return segments

    def _label_segments(self, segments: list[SemanticSegment]) -> None:
        for seg in segments:
            op_types = [op.op_type for op in seg.operations]
            type_counts: dict[str, int] = {}
            for t in op_types:
                type_counts[t] = type_counts.get(t, 0) + 1

            dominant_type = max(type_counts, key=type_counts.get) if type_counts else "unknown"

            window_titles = set()
            app_names = set()
            for op in seg.operations:
                ctx = op.context or {}
                aw = ctx.get("active_window") or {}
                wt = aw.get("title", "")
                an = aw.get("app_name", "")
                if wt:
                    window_titles.add(wt)
                if an:
                    app_names.add(an)

            label_parts = []
            if app_names:
                label_parts.append(next(iter(app_names)))
            elif window_titles:
                label_parts.append(next(iter(window_titles)))
            label_parts.append(self._type_to_label(dominant_type, len(op_types)))
            seg.label = " - ".join(label_parts)

            seg.confidence = self._compute_segment_confidence(seg)

            if seg.sub_segments:
                for sub in seg.sub_segments:
                    sub_ops = [op.op_type for op in sub.operations]
                    sub_type_counts: dict[str, int] = {}
                    for t in sub_ops:
                        sub_type_counts[t] = sub_type_counts.get(t, 0) + 1
                    sub_dominant = max(sub_type_counts, key=sub_type_counts.get) if sub_type_counts else "unknown"
                    sub.label = self._type_to_label(sub_dominant, len(sub_ops))
                    sub.confidence = self._compute_segment_confidence(sub)

    def _type_to_label(self, op_type: str, count: int) -> str:
        label_map = {
            "mouse_click": "点击操作",
            "type_text": "文本输入",
            "hotkey": "快捷键操作",
            "mouse_scroll": "滚动浏览",
            "mouse_drag": "拖拽操作",
            "switch_window": "窗口切换",
            "navigation": "页面导航",
            "clipboard": "剪贴板操作",
            "upload_file": "文件上传",
            "download_file": "文件下载",
        }
        base_label = label_map.get(op_type, op_type)
        if count > 1:
            return f"{base_label}x{count}"
        return base_label

    def _compute_segment_confidence(self, segment: SemanticSegment) -> float:
        if segment.length < MIN_SEGMENT_LENGTH:
            return 0.3

        type_uniformity = 0.0
        if segment.operations:
            type_counts: dict[str, int] = {}
            for op in segment.operations:
                type_counts[op.op_type] = type_counts.get(op.op_type, 0) + 1
            max_count = max(type_counts.values())
            type_uniformity = max_count / len(segment.operations)

        time_consistency = 0.5
        if len(segment.operations) > 1:
            gaps = [
                segment.operations[i + 1].timestamp - segment.operations[i].timestamp
                for i in range(len(segment.operations) - 1)
            ]
            if gaps:
                avg_gap = sum(gaps) / len(gaps)
                if avg_gap > 0:
                    variance = sum((g - avg_gap) ** 2 for g in gaps) / len(gaps)
                    std_dev = variance**0.5
                    cv = std_dev / avg_gap
                    time_consistency = max(0.0, min(1.0, 1.0 - cv))

        window_consistency = 0.5
        if segment.operations:
            app_names = set()
            for op in segment.operations:
                an = ((op.context or {}).get("active_window") or {}).get("app_name", "")
                if an:
                    app_names.add(an)
            if len(app_names) <= 1:
                window_consistency = 1.0
            elif len(app_names) == 2:
                window_consistency = 0.6
            else:
                window_consistency = 0.3

        spatial_coherence = 0.5
        if len(segment.operations) > 1:
            shifts = []
            for i in range(1, len(segment.operations)):
                prev = segment.operations[i - 1]
                curr = segment.operations[i]
                px = prev.data.get("x", 0) if prev.data else 0
                py = prev.data.get("y", 0) if prev.data else 0
                cx = curr.data.get("x", 0) if curr.data else 0
                cy = curr.data.get("y", 0) if curr.data else 0
                shifts.append(((cx - px) ** 2 + (cy - py) ** 2) ** 0.5)
            if shifts:
                avg_shift = sum(shifts) / len(shifts)
                if avg_shift < SPATIAL_THRESHOLD_PX:
                    spatial_coherence = 1.0
                elif avg_shift < SPATIAL_THRESHOLD_PX * 3:
                    spatial_coherence = 0.6
                else:
                    spatial_coherence = 0.3

        confidence = (
            type_uniformity * 0.30
            + time_consistency * 0.20
            + window_consistency * 0.25
            + spatial_coherence * 0.10
            + 0.15
        )
        return round(min(max(confidence, 0.0), 1.0), 4)
