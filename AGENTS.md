# AGENTS.md

## Cursor Cloud specific instructions

### Project Overview
Auto Agent Workflow is a FastAPI + Typer CLI desktop/web automation system. See `README.md` for full architecture.

### Running the service
```bash
uv run uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```
Health check: `GET /api/health` → `{"status":"ok"}`. OpenAPI docs at `/docs`.

### Running tests
```bash
uv run pytest -q                   # full suite
uv run pytest tests/unit -q        # unit tests only (fastest)
uv run pytest tests/simulation -q  # simulation/integration tests
```

### Linting
```bash
uv run ruff check src/             # lint (existing codebase has ~170 warnings)
uv run ruff check src/models/      # lint model files only
```

### Known test failures (pre-existing, not caused by model reconstruction)
- `tests/simulation/test_api_simulation.py` (5 tests): DB table creation not triggered during TestClient setup
- `tests/simulation/test_full_pipeline.py` (3 tests): `suggest_flow_name` not wired in `AnalysisService`
- `tests/unit/test_recorder_configuration.py` (1 test): `/tmp/` prefix in `IGNORED_PREFIXES` conflicts with pytest's temp dir

### Important caveats
- The `src/models/` package was reconstructed from usage analysis. The `.gitignore` pattern was changed from `models/` to `/models/` to only exclude the top-level binary models directory.
- Enums use `(str, Enum)` style (not `StrEnum`) to match the rest of the codebase. Ruff's `UP042` warnings are expected.
- `StepTarget.position` accepts both `dict` and `Point` objects via a Pydantic model validator that coerces dicts to `Point`.
- `AutomationStep.locator_requires_confirmation()` considers both metadata flags and target locator strength.
- The DB uses async SQLite via `aiosqlite`. Tables are auto-created on app startup via `init_db()`.
