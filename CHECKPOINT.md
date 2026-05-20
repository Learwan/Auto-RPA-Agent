# Checkpoint: unified main (2026-05-20)

Git tag: `checkpoint/2026-05-20-unified`

## Merged into main (local)

| PR | Branch | Summary |
|----|--------|---------|
| #8 | `cursor/research-driven-core-optimization-a15b` | Pattern mining, confidence scoring, self-healing, step verifier |
| #7 | `cursor/llm-vision-closed-loop-sim-a15b` | LLM vision closed-loop simulation tests |
| #6 | `copilot-minicpm-vision-fallback-20260519` | MiniCPM vision fallback, UI theme/vendor assets, legacy LLM compat |
| (prior) | — | Replay fidelity tests, analysis pipeline fixes (`9a88cf7`) |

## Not merged

- **PR #5** (draft): `copilot/optimize-core-capability-process` — left open as draft.
- **`auto-agent-workflow/`**: nested workspace with ~2.9GB `models/`; added to `.gitignore`, not pushed.

## Excluded from git (by design)

- `/models/` — root-level binary model artifacts
- `auto-agent-workflow/` — duplicate nested project + local model weights

## Verification (local)

```bash
uv run pytest tests/simulation/ tests/unit/test_analysis_service.py -q
# 99 passed (2026-05-20)
```
