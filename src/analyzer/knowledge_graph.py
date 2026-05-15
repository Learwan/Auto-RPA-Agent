from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.6
MAX_PATTERNS = 1000


@dataclass
class OperationPattern:
    pattern_id: str
    op_sequence: list[str]
    app_name: str
    window_context: str
    avg_duration_ms: float
    success_rate: float
    occurrence_count: int
    last_seen: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        raw = "|".join(self.op_sequence) + f"|{self.app_name}"
        return hex(hash(raw))[2:12]


@dataclass
class ErrorPattern:
    error_id: str
    error_type: str
    root_cause: str
    app_name: str
    step_context: str
    healing_strategy: str
    healing_success_rate: float
    occurrence_count: int
    last_seen: float = field(default_factory=time.time)


@dataclass
class ExperienceRecord:
    source_app: str
    target_app: str
    source_pattern_id: str
    target_pattern_id: str | None
    transfer_success: bool
    similarity_score: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class SearchResult:
    pattern: OperationPattern | ErrorPattern
    similarity: float
    match_type: str


class OperationPatternStore:
    def __init__(self, max_patterns: int = MAX_PATTERNS):
        self._patterns: dict[str, OperationPattern] = {}
        self._max_patterns = max_patterns
        self._app_index: dict[str, list[str]] = defaultdict(list)
        self._tag_index: dict[str, list[str]] = defaultdict(list)

    def add(self, pattern: OperationPattern) -> str:
        pid = pattern.pattern_id or pattern.fingerprint()
        pattern.pattern_id = pid

        if pid in self._patterns:
            existing = self._patterns[pid]
            existing.occurrence_count += 1
            existing.last_seen = time.time()
            existing.avg_duration_ms = (
                existing.avg_duration_ms * (existing.occurrence_count - 1) + pattern.avg_duration_ms
            ) / existing.occurrence_count
            existing.success_rate = (
                existing.success_rate * (existing.occurrence_count - 1) + pattern.success_rate
            ) / existing.occurrence_count
        else:
            self._patterns[pid] = pattern
            self._app_index[pattern.app_name].append(pid)
            for tag in pattern.tags:
                self._tag_index[tag].append(pid)

        if len(self._patterns) > self._max_patterns:
            self._evict()

        return pid

    def get(self, pattern_id: str) -> OperationPattern | None:
        return self._patterns.get(pattern_id)

    def search_by_app(self, app_name: str) -> list[OperationPattern]:
        pids = self._app_index.get(app_name, [])
        return [self._patterns[pid] for pid in pids if pid in self._patterns]

    def search_by_tags(self, tags: list[str]) -> list[OperationPattern]:
        pid_sets = []
        for tag in tags:
            pids = set(self._tag_index.get(tag, []))
            pid_sets.append(pids)
        if not pid_sets:
            return []
        common = set.intersection(*pid_sets) if pid_sets else set()
        return [self._patterns[pid] for pid in common if pid in self._patterns]

    def search_similar(
        self, op_sequence: list[str], app_name: str = "", top_k: int = 5
    ) -> list[tuple[OperationPattern, float]]:
        candidates = list(self._patterns.values())

        if app_name:
            app_candidates = self.search_by_app(app_name)
            other_candidates = [p for p in candidates if p.app_name != app_name]
            candidates = app_candidates + other_candidates

        scored = []
        for pattern in candidates:
            sim = self._compute_sequence_similarity(op_sequence, pattern.op_sequence)
            if app_name and pattern.app_name == app_name:
                sim = sim * 0.7 + 0.3
            if sim >= SIMILARITY_THRESHOLD:
                scored.append((pattern, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def size(self) -> int:
        return len(self._patterns)

    @staticmethod
    def _compute_sequence_similarity(seq_a: list[str], seq_b: list[str]) -> float:
        if not seq_a or not seq_b:
            return 0.0

        set_a = set(seq_a)
        set_b = set(seq_b)
        intersection = set_a & set_b
        union = set_a | set_b
        jaccard = len(intersection) / len(union) if union else 0.0

        shorter, longer = (seq_a, seq_b) if len(seq_a) <= len(seq_b) else (seq_b, seq_a)
        lcs_len = _lcs_length(shorter, longer)
        sequence_sim = (2 * lcs_len) / (len(seq_a) + len(seq_b)) if (len(seq_a) + len(seq_b)) > 0 else 0.0

        return jaccard * 0.4 + sequence_sim * 0.6

    def _evict(self) -> None:
        if len(self._patterns) <= self._max_patterns:
            return
        sorted_pids = sorted(
            self._patterns.keys(),
            key=lambda pid: self._patterns[pid].last_seen,
        )
        to_remove = sorted_pids[: len(self._patterns) - self._max_patterns]
        for pid in to_remove:
            pattern = self._patterns.pop(pid)
            if pattern.app_name in self._app_index:
                self._app_index[pattern.app_name] = [
                    p for p in self._app_index[pattern.app_name] if p != pid
                ]
            for tag in pattern.tags:
                if tag in self._tag_index:
                    self._tag_index[tag] = [p for p in self._tag_index[tag] if p != pid]


class ErrorPatternStore:
    def __init__(self, max_patterns: int = 500):
        self._patterns: dict[str, ErrorPattern] = {}
        self._max_patterns = max_patterns
        self._app_index: dict[str, list[str]] = defaultdict(list)

    def add(self, pattern: ErrorPattern) -> str:
        eid = pattern.error_id
        self._patterns[eid] = pattern
        self._app_index[pattern.app_name].append(eid)

        if len(self._patterns) > self._max_patterns:
            oldest = min(self._patterns.keys(), key=lambda k: self._patterns[k].last_seen)
            del self._patterns[oldest]

        return eid

    def search_similar(
        self, error_type: str, app_name: str = "", top_k: int = 5
    ) -> list[tuple[ErrorPattern, float]]:
        candidates = list(self._patterns.values())

        if app_name:
            app_pids = self._app_index.get(app_name, [])
            app_patterns = [self._patterns[pid] for pid in app_pids if pid in self._patterns]
            other_patterns = [p for p in candidates if p.app_name != app_name]
            candidates = app_patterns + other_patterns

        scored = []
        for pattern in candidates:
            sim = 0.0
            if pattern.error_type == error_type:
                sim += 0.5
            if pattern.app_name == app_name:
                sim += 0.3
            sim += pattern.healing_success_rate * 0.2
            if sim >= 0.3:
                scored.append((pattern, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


class ExperienceTransfer:
    def __init__(self, pattern_store: OperationPatternStore):
        self._pattern_store = pattern_store
        self._transfer_history: list[ExperienceRecord] = []

    def find_transferable(
        self, target_app: str, target_ops: list[str], top_k: int = 5
    ) -> list[tuple[OperationPattern, float]]:
        similar = self._pattern_store.search_similar(target_ops, top_k=top_k * 2)

        transferable = []
        for pattern, similarity in similar:
            if pattern.app_name == target_app:
                continue
            transferability = similarity * 0.7 + pattern.success_rate * 0.3
            transferable.append((pattern, transferability))

        transferable.sort(key=lambda x: x[1], reverse=True)
        return transferable[:top_k]

    def record_transfer(self, record: ExperienceRecord) -> None:
        self._transfer_history.append(record)
        if len(self._transfer_history) > 500:
            self._transfer_history = self._transfer_history[-500:]

    def get_transfer_success_rate(self, source_app: str = "", target_app: str = "") -> float:
        records = self._transfer_history
        if source_app:
            records = [r for r in records if r.source_app == source_app]
        if target_app:
            records = [r for r in records if r.target_app == target_app]
        if not records:
            return 0.0
        return sum(1 for r in records if r.transfer_success) / len(records)


class WorkflowKnowledgeGraph:
    def __init__(self):
        self._op_store = OperationPatternStore()
        self._error_store = ErrorPatternStore()
        self._experience_transfer = ExperienceTransfer(self._op_store)

    def add_operation_pattern(self, pattern: OperationPattern) -> str:
        return self._op_store.add(pattern)

    def add_error_pattern(self, pattern: ErrorPattern) -> str:
        return self._error_store.add(pattern)

    def search_similar_operations(
        self, op_sequence: list[str], app_name: str = "", top_k: int = 5
    ) -> list[tuple[OperationPattern, float]]:
        return self._op_store.search_similar(op_sequence, app_name, top_k)

    def search_similar_errors(
        self, error_type: str, app_name: str = "", top_k: int = 5
    ) -> list[tuple[ErrorPattern, float]]:
        return self._error_store.search_similar(error_type, app_name, top_k)

    def find_transferable_experience(
        self, target_app: str, target_ops: list[str], top_k: int = 5
    ) -> list[tuple[OperationPattern, float]]:
        return self._experience_transfer.find_transferable(target_app, target_ops, top_k)

    def record_transfer(self, record: ExperienceRecord) -> None:
        self._experience_transfer.record_transfer(record)

    def get_stats(self) -> dict:
        return {
            "operation_patterns": self._op_store.size(),
            "error_patterns": len(self._error_store._patterns),
            "transfer_history": len(self._experience_transfer._transfer_history),
            "transfer_success_rate": self._experience_transfer.get_transfer_success_rate(),
        }


def _lcs_length(a: list[str], b: list[str]) -> int:
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n]
