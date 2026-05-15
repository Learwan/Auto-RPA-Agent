from __future__ import annotations

from src.analyzer.checkpoint_synthesizer import CheckpointSynthesizer
from src.models.automation import AutomationFlow

_checkpoint_synthesizer = CheckpointSynthesizer()


def backfill_flow_checkpoints(flow: AutomationFlow, semantic_context: dict | None = None) -> dict:
    checkpoint_summary = _checkpoint_synthesizer.apply(flow, semantic_context)
    flow.metadata = {
        **(flow.metadata or {}),
        "checkpoint_summary": checkpoint_summary,
    }
    return checkpoint_summary
