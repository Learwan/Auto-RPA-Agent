# Auto Agent Workflow

Auto Agent Workflow is a cross-platform desktop and web automation system for recording user operations, analyzing repeatable patterns, authoring automation flows, and executing them with runtime feedback, with a growing emphasis on personal AI copilot assistance during workflow extraction.

This repository currently ships a FastAPI backend, Typer CLI, static web consoles, optional LLM and vision augmentation, and a growing set of operational modules for collaboration, scheduling, credentials, marketplace templates, notifications, and session fusion.

## Documentation Map

- `README.md`: product overview, installation, usage, and capability boundaries
- `docs/API_REFERENCE.md`: current REST and WebSocket surface by module
- `docs/ENGINEERING_DOCUMENT.md`: current architecture and module ownership
- `docs/AUDIT_REPORT.md`: documentation consistency audit and revision summary
- `docs/PRODUCT_DIRECTION_REEVALUATION.md`: refreshed product direction assessment
- `docs/INTERNAL_INTERFACE_REVIEW.md`: internal feature invocation and interface review

## Current Product Scope

### 1. Recording and capture

- Desktop and browser session recording
- Mouse, keyboard, window, clipboard, and filesystem event capture
- Desktop state inspection, snapshots, screenshots, and periodic capture
- Session event streaming and realtime analysis channels

### 2. Analysis and flow generation

- Operation normalization and semantic segmentation
- Pattern detection and direct-flow fallback generation
- Confidence scoring with reliability suggestions
- Optional LLM-assisted flow naming and flow analysis when LLM is configured

### 3. Flow authoring and execution

- Automation CRUD, step editing, branching, export, scoring, and execution
- Dry-run execution for non-destructive validation
- Runtime controls for pause, resume, stop, skip-step, and step-over
- WebSocket execution feedback for live status updates
- Visual verification, screenshot comparison, and multi-strategy element location

### 4. AI and vision augmentation

- LLM chat, flow analysis, operation explanation, and script enhancement
- Vision status, screenshot analysis, action grounding, UI extraction, and verification
- Agent endpoints for chat, workflow analysis, execution debugging, and process mapping

### 5. Operations and governance

- Collaboration rooms and realtime analyzer endpoints
- User settings and hotkey management
- Basic auth and RBAC administration endpoints
- Credential vault, schedules, template marketplace, notifications, and session fusion

## Architecture Snapshot

```
Recorder / Desktop Capture
  |
  v
Analysis Service ----> Flow Generation ----> Automation API / Flow Editor
  |                                         |
  v                                         v
Optional LLM Enhancement                    Execution Service
                |
                v
            Step Executor / Element Locator / Platform Adapter
                |
                v
         Runtime Feedback / Knowledge Capture / Evolution Hooks
```

Key runtime modules:

- `src/api`: FastAPI application, REST routes, and WebSocket endpoints
- `src/recorder`: session lifecycle and event capture
- `src/analyzer`: operation preprocessing, segmentation, pattern detection, scoring
- `src/executor`: execution engine, error handling, locator, verification, adaptive hooks
- `src/llm`: cloud LLM, local model, multimodal analysis, grounding
- `src/platform`: macOS, Windows, Linux, and Web adapters
- `src/db`: async SQLAlchemy persistence and migrations

## Project Structure

```
auto-agent-workflow/
├── src/
│   ├── api/              FastAPI app, REST routes, WebSocket endpoints
│   ├── analyzer/         Flow generation, scoring, semantic processing
│   ├── agent/            Agent engine, tools, and vision agent helpers
│   ├── collab/           Collaboration services
│   ├── db/               Database models, repository, session factory
│   ├── executor/         Execution engine, locator, verification, recovery
│   ├── llm/              LLM and multimodal services
│   ├── monitoring/       Metrics and middleware
│   ├── notification/     Notification services
│   ├── platform/         Desktop and browser adapters
│   ├── recorder/         Recording services and event recorders
│   ├── scheduler/        Scheduling services
│   ├── security/         Input validation and rate limiting
│   ├── vault/            Credential storage services
│   ├── cli.py            Typer CLI entry point
│   └── main.py           Application entry point
├── static/               Web console, flow editor, collaboration, marketplace pages
├── docs/                 Architecture, API, audit, and strategy documents
├── migrations/           Alembic migrations
├── scripts/              Utilities such as model download helpers
├── tests/                Unit, integration, and simulation tests
└── benchmarks/           Benchmark and capability assessment scripts
```

## Getting Started

### Prerequisites

- Python 3.12 to 3.14
- macOS desktop recording requires Accessibility and Screen Recording permissions
- Windows desktop automation requires the `.[windows]` extra
- Linux desktop automation requires `wmctrl`, `xprop`, and AT-SPI support
- Web automation requires the `.[web]` extra and a Playwright browser install

### Installation

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade

python -m pip install -e .
python -m pip install -e ".[dev]"

# Optional extras
python -m pip install -e ".[macos]"
python -m pip install -e ".[windows]"
python -m pip install -e ".[web]"
playwright install chromium
python -m pip install -e ".[ocr]"
```

### Configuration

Create a `.env` file and configure the runtime as needed. Typical settings include:

- data directory and database URL
- LLM endpoint, API key, and model
- local model enablement for vision and on-device inference
- recorder toggles for mouse, keyboard, window, clipboard, and filesystem capture
- CORS and browser runtime options

The CLI can target a non-default server by setting one of:

- `AUTO_AGENT_API_BASE`
- `AUTO_AGENT_API_HOST`
- `AUTO_AGENT_API_PORT`
- `AUTO_AGENT_API_SCHEME`

## Running the Service

```bash
source .venv/bin/activate
auto-agent serve

# or
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```

Default entry points:

- Web Console: `http://localhost:8000/`
- Flow Editor: `http://localhost:8000/flow-editor.html`
- Collaboration UI: `http://localhost:8000/collab.html`
- Marketplace UI: `http://localhost:8000/marketplace.html`
- API Health: `http://localhost:8000/api/health`
- OpenAPI Docs: `http://localhost:8000/docs`

## CLI Quick Reference

### Recording

```bash
auto-agent record start --name "demo-session"
auto-agent record stop --session-id <session_id>
auto-agent record list
auto-agent record show <session_id>
```

### Analysis and flows

```bash
auto-agent analyze <session_id> --min-confidence 0.5 --generate
auto-agent flow list
auto-agent flow show <flow_id>
auto-agent flow export <flow_id> --format json --output flow.json
```

### Execution

```bash
auto-agent run <flow_id>
auto-agent run <flow_id> --dry-run
auto-agent executions list
auto-agent executions show <execution_id>
```

### LLM utilities

```bash
auto-agent llm status
auto-agent llm chat "How can I stabilize this workflow?"
auto-agent llm analyze <flow_id>
auto-agent llm explain <session_id>
auto-agent llm enhance <flow_id> --output enhanced.py
```

## API At A Glance

The current backend exposes the following route groups:

- `/api/desktop`: desktop state, windows, processes, screenshots, snapshots
- `/api/sessions`: session lifecycle, operations, analysis, WebSocket event streams
- `/api/sessions/{id}/copilot`: personal workflow copilot summary for single-user recording sessions
- `/api/automations`: flow CRUD, step editing, execution, scoring, export, live execution WebSocket
- `/api/executions`: execution history and runtime controls
- `/api/llm`: LLM status, chat, explain, analyze, enhance
- `/api/agent`: agent chat and workflow support endpoints
- `/api/settings`: user settings and hotkeys
- `/api/collab`: collaboration rooms and realtime analyzer endpoints
- `/api/vision`: screenshot analysis, grounding, comparison, UI extraction
- `/api/auth`: user and role administration
- `/api/vault`: credential management
- `/api/scheduler`: schedule management
- `/api/marketplace`: template marketplace
- `/api/groups`, `/api/fuse`, `/api/results`: session fusion APIs
- `/api/history`, `/api/send`, `/api/webhooks`, `/api/email`: notification APIs

Use the OpenAPI page at `/docs` for schema-level details and `docs/API_REFERENCE.md` for the curated route inventory.

## Capability Notes For This Version

- `AnalysisService` can call LLM naming and analysis helpers when an LLM is configured.
- `ExecutionService` initializes adaptive workflow, intelligent recovery, knowledge graph, cross-platform adaptation, and self-evolution components.
- Not every advanced module is part of the tight inner loop for every request; some are currently used for health exposure, execution feedback collection, or future extension points rather than direct per-step decisioning.
- Earlier audit snapshots in this repository may overstate the amount of dead code. Use the refreshed audit and interface review documents for the current picture.

## Development

```bash
ruff check src/
mypy src/
python -m pytest -q
```

See `docs/BUILD_AND_TEST.md` for a fuller build and test workflow.

## Known Limitations

- Windows and Linux element tree stability depends on target application accessibility support
- Linux window and element capabilities depend on desktop environment and tool availability
- Web automation requires Playwright browser driver installation
- Pattern detection uses heuristic repetition analysis, not full process mining
- No database migration framework — back up data before schema upgrades

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — System architecture and module responsibilities
- [Build & Test](docs/BUILD_AND_TEST.md) — Build instructions and testing guide
- [Automation Recording Survey](docs/advanced-automation-recording-survey-2025.md) — Industry research on automation recording technology

## License

This project is licensed under the MIT License.
