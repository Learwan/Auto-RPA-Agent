# AGENTS.md

## Cursor Cloud specific instructions

### Project Overview
Auto Agent Workflow is a FastAPI + Typer CLI desktop/web automation system with a closed-loop pipeline: record → analyze → AI enhance → closure certify → execute → feedback. See `README.md` for full architecture, `CONTRIBUTING.md` for code style, and `docs/CLOSED_LOOP_GUARANTEES.md` for the anti-fragile policy.

### System dependencies (pre-installed in VM snapshot)
- `python3.12-dev` — required for building `evdev` (transitive dep of `pynput`)
- `python3-tk` — required for `mouseinfo` (transitive dep of `pyautogui`)
- `uv` — package manager (`~/.local/bin/uv`)

### Running the service
```bash
uv run uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```
Health check: `GET /api/health`. Startup diagnostics: `GET /api/health/startup-check`. OpenAPI docs at `/docs`.

### Running tests
```bash
uv run pytest -q                   # full suite (532 pass, 1 known failure)
uv run pytest tests/unit -q        # unit tests only (fastest)
uv run pytest tests/simulation -q  # simulation tests (includes closed-loop certification)
```

### Linting
```bash
uv run ruff check src/             # lint (170 pre-existing warnings, mostly UP042 enums)
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

### Known pre-existing test failure
- `tests/unit/test_recorder_configuration.py::test_filesystem_recorder_ignores_data_dir_and_sqlite_journal`: `/tmp/` prefix in `IGNORED_PREFIXES` conflicts with pytest temp dirs on Linux

### Closed-loop pipeline architecture
The core pipeline enforces anti-fragile execution:
1. **Record** → `RecordingService` / `SessionManager` captures events with HUD and focus context
2. **Analyze** → `AnalysisService.analyze_session()` preprocesses, segments, detects patterns, generates flows
3. **AI Enhance** → When LLM configured: `analyze_flow_artifacts` (structured) + `suggest_flow_name` (naming)
4. **Closure Assess** → `FlowClosureAssessor.assess()` rejects POSITION-only, missing context/checkpoints
5. **Execute** → `ExecutionService._ensure_flow_ready_for_execution()` gate; `ExecutionEngine` + `StepExecutor`
6. **Feedback** → `WorkflowKnowledgeGraph` + `SelfEvolutionSystem` accumulate patterns

**Important**: `include_ai=False` by default on the analyze API endpoint. Pass `include_ai=true` to enable LLM enhancement.

### Architecture notes
- The DB uses async SQLite via `aiosqlite`. Tables are auto-created on app startup via `init_db()`.
- The `src/models/` package was reconstructed; `.gitignore` uses `/models/` to only exclude top-level ML model binaries.
- Enums use `(str, Enum)` style matching codebase conventions; Ruff UP042 warnings are expected.
- `src/api/routes/mvp.py` exists but is not mounted in `app.py` — browser recording shortcut is unreachable.
- Scheduler (`SchedulerService`) is in-memory CRUD only — no background job runner fires executions.
- Auth/RBAC (`RBACManager`) is in-memory and not enforced as FastAPI middleware on other routes.
- Static UI at `static/` uses vendored Tailwind + Lucide; `ui-theme.css` provides shared design tokens.

### Key test files for closed-loop verification
- `tests/simulation/test_llm_vision_closed_loop.py` — 21 tests: certified flow assessment + LLM-driven pipeline + execution + negative cases (POSITION rejected)
- `tests/simulation/test_replay_fidelity_simulation.py` — 15 tests: web/desktop context switching, coordinate rejection, semantic replay
- `tests/simulation/test_full_pipeline.py` — 6 tests: end-to-end record → analyze → execute with LLM mocks
