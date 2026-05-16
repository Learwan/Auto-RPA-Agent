# AGENTS.md

## Cursor Cloud specific instructions

### Project Overview
Auto Agent Workflow is a FastAPI + Typer CLI desktop/web automation system. See `README.md` for full architecture and `CONTRIBUTING.md` for code style and branch conventions.

### System dependencies (pre-installed in VM snapshot)
- `python3.12-dev` — required for building `evdev` (transitive dep of `pynput`)
- `python3-tk` — required for `mouseinfo` (transitive dep of `pyautogui`)
- `uv` — package manager (`~/.local/bin/uv`)

### Running the service
```bash
uv run uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```
Health check: `GET /api/health`. OpenAPI docs at `/docs`.

### Running tests
```bash
uv run pytest -q                   # full suite (484 pass, 4 pre-existing failures)
uv run pytest tests/unit -q        # unit tests only (fastest)
uv run pytest tests/simulation -q  # simulation tests
```

### Linting
```bash
uv run ruff check src/             # lint
uv run mypy src/                   # type check (strict mode, many pre-existing errors)
```

### Environment for headless/CI
Disable native recorders in `.env` to avoid OS permission errors:
```
AUTO_AGENT_RECORD_ENABLE_MOUSE=false
AUTO_AGENT_RECORD_ENABLE_KEYBOARD=false
AUTO_AGENT_RECORD_ENABLE_WINDOW=false
AUTO_AGENT_RECORD_ENABLE_CLIPBOARD=false
AUTO_AGENT_RECORD_ENABLE_FILESYSTEM=false
```

### Known pre-existing test failures
- `tests/simulation/test_full_pipeline.py` (3 tests): `suggest_flow_name` not wired in `AnalysisService`
- `tests/unit/test_recorder_configuration.py` (1 test): `/tmp/` prefix in `IGNORED_PREFIXES` conflicts with pytest temp dirs

### Important caveats
- The `src/models/` package was reconstructed from usage analysis because `.gitignore` had `models/` which excluded it. The gitignore was changed to `/models/` to only exclude top-level ML model binaries.
- The DB uses async SQLite via `aiosqlite`. Tables are auto-created on app startup via `init_db()`.
- Enums use `(str, Enum)` style matching codebase conventions; Ruff UP042 warnings are expected.
