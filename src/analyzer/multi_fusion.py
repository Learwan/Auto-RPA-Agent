from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from src.analyzer.preprocessor import NormalizedOperation

logger = logging.getLogger(__name__)

ALIGNMENT_EPS = 0.5
ALIGNMENT_MIN_SAMPLES = 2


@dataclass
class AlignedTrace:
    trace_id: str
    session_id: str
    operations: list[NormalizedOperation]
    op_types: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.op_types:
            self.op_types = [op.op_type for op in self.operations]


@dataclass
class FusedBranch:
    branch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    condition: str = ""
    trace_ids: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)


@dataclass
class FusedFlow:
    flow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    common_steps: list[dict] = field(default_factory=list)
    branches: list[FusedBranch] = field(default_factory=list)
    loop_patterns: list[dict] = field(default_factory=list)
    confidence: float = 0.0


class MultiRecordingFusion:
    def fuse(self, traces: list[AlignedTrace]) -> FusedFlow | None:
        if len(traces) < 2:
            return None

        aligned = self._align_traces(traces)
        common, divergences = self._find_common_and_divergences(aligned)
        branches = self._create_branches(divergences, aligned)
        loops = self._detect_loops(aligned)

        confidence = self._compute_confidence(common, branches, len(traces))

        return FusedFlow(
            common_steps=common,
            branches=branches,
            loop_patterns=loops,
            confidence=confidence,
        )

    def _align_traces(self, traces: list[AlignedTrace]) -> list[AlignedTrace]:
        if not traces:
            return []

        min_len = min(len(t.op_types) for t in traces)
        aligned = []
        for trace in traces:
            trimmed_ops = trace.operations[:min_len]
            aligned.append(
                AlignedTrace(
                    trace_id=trace.trace_id,
                    session_id=trace.session_id,
                    operations=trimmed_ops,
                    op_types=trace.op_types[:min_len],
                )
            )
        return aligned

    def _find_common_and_divergences(self, aligned: list[AlignedTrace]) -> tuple[list[dict], list[dict]]:
        if not aligned:
            return [], []

        common = []
        divergences = []

        min_len = min(len(a.op_types) for a in aligned)
        for i in range(min_len):
            types_at_i = set(a.op_types[i] for a in aligned)
            if len(types_at_i) == 1:
                common.append(
                    {
                        "index": i,
                        "op_type": next(iter(types_at_i)),
                        "unanimous": True,
                    }
                )
            else:
                divergences.append(
                    {
                        "index": i,
                        "op_types": list(types_at_i),
                        "trace_map": {a.trace_id: a.op_types[i] for a in aligned},
                    }
                )

        return common, divergences

    def _create_branches(self, divergences: list[dict], aligned: list[AlignedTrace]) -> list[FusedBranch]:
        branches = []
        for div in divergences:
            type_groups: dict[str, list[str]] = {}
            for trace_id, op_type in div["trace_map"].items():
                type_groups.setdefault(op_type, []).append(trace_id)

            for op_type, trace_ids in type_groups.items():
                branches.append(
                    FusedBranch(
                        condition=f"step_{div['index']}_is_{op_type}",
                        trace_ids=trace_ids,
                        steps=[{"index": div["index"], "op_type": op_type}],
                    )
                )

        return branches

    def _detect_loops(self, aligned: list[AlignedTrace]) -> list[dict]:
        loops = []
        for trace in aligned:
            seen_sequences: dict[str, list[int]] = {}
            seq_len = 3
            for i in range(len(trace.op_types) - seq_len + 1):
                seq = tuple(trace.op_types[i : i + seq_len])
                key = str(seq)
                if key in seen_sequences:
                    seen_sequences[key].append(i)
                else:
                    seen_sequences[key] = [i]

            for seq_key, positions in seen_sequences.items():
                if len(positions) >= 2:
                    loops.append(
                        {
                            "trace_id": trace.trace_id,
                            "sequence": seq_key,
                            "occurrences": len(positions),
                            "positions": positions,
                        }
                    )

        return loops

    def _compute_confidence(
        self,
        common: list[dict],
        branches: list[FusedBranch],
        trace_count: int,
    ) -> float:
        if trace_count < 2:
            return 0.0

        total_steps = len(common) + sum(len(b.steps) for b in branches)
        if total_steps == 0:
            return 0.0

        common_ratio = len(common) / total_steps
        branch_coverage = sum(len(b.trace_ids) for b in branches) / (trace_count * len(branches)) if branches else 1.0

        return min(common_ratio * 0.6 + branch_coverage * 0.4, 1.0)
