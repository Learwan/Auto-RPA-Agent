# Contributing to Auto-Agent Workflow

Thank you for your interest in contributing! This guide covers the development workflow and coding standards.

## Development Setup

```bash
# Clone and enter the project
git clone https://github.com/your-org/auto-agent-workflow.git
cd auto-agent-workflow

# Install with dev dependencies (requires uv)
uv sync --extra dev

# macOS platform dependencies
uv sync --extra macos

# Install pre-commit hooks
pre-commit install
```

## Branch Naming

| Branch Type   | Format              | Example                  |
|---------------|---------------------|--------------------------|
| Feature       | `feature/<scope>`   | `feature/bt-generator`   |
| Bug Fix       | `bugfix/<scope>`    | `bugfix/step-verifier`   |
| Hotfix        | `hotfix/<scope>`    | `hotfix/db-connection`   |
| Release       | `release/<version>` | `release/0.2.0`          |

## Commit Convention

Format: `<type>(<scope>): <description>`

| Type       | Usage                              |
|------------|------------------------------------|
| `feat`     | New feature                        |
| `fix`      | Bug fix                            |
| `docs`     | Documentation only                 |
| `style`    | Formatting, no code change         |
| `refactor` | Code restructuring                 |
| `perf`     | Performance improvement            |
| `test`     | Adding or updating tests           |
| `build`    | Build system or dependencies       |
| `ci`       | CI/CD configuration                |
| `chore`    | Other changes                      |

Examples:
```
feat(analyzer): add HDBSCAN clustering for semantic segmentation
fix(executor): resolve retry loop in step executor
test(api): add integration tests for auth routes
```

## Code Style

- **Python 3.12+** with type hints
- **Indentation**: 4 spaces
- **Line length**: ≤ 120 characters
- **Line endings**: LF
- **Encoding**: UTF-8
- **Formatting**: `ruff format`
- **Linting**: `ruff check`
- **Type checking**: `mypy --strict`

### Naming Conventions

| Element      | Convention          | Example                |
|--------------|---------------------|------------------------|
| Project      | kebab-case          | `auto-agent-workflow`  |
| Files        | snake_case          | `semantic_segmenter.py`|
| Classes      | PascalCase          | `BehaviorTreeGenerator`|
| Functions    | snake_case          | `generate_flow`        |
| Constants    | UPPER_SNAKE_CASE    | `MIN_SEGMENT_LENGTH`   |
| Private      | _prefix             | `_internal_state`      |
| Interfaces   | I-prefix / -able    | `IExecutor` / `Runnable`|

### Documentation

All exported functions and classes must have docstrings with:
- Purpose description
- Parameters (name, type, description)
- Return value
- Exceptions raised

```python
async def locate(self, target: StepTarget) -> LocatedElement | None:
    """Locate a UI element using the specified target strategy.

    Args:
        target: The target specification including strategy and criteria.

    Returns:
        A LocatedElement if found, None otherwise.

    Raises:
        RuntimeError: If the platform adapter is not initialized.
    """
```

## Testing

### Test Structure

```
tests/
├── unit/           # Unit tests (majority)
├── integration/    # Integration tests (moderate)
└── e2e/            # End-to-end tests (few)
```

### Coverage Requirements

| Module          | Minimum Coverage |
|-----------------|-----------------|
| Core (src/db, src/executor) | ≥ 90% |
| Tools (src/analyzer, src/llm) | ≥ 80% |
| New features    | 100% |

### Test Naming

Format: `test_{module}_{scenario}_{result}`

```python
def test_semantic_segmenter_window_change_boundary_produces_segment():
    ...
```

### Writing Tests

Follow the Given-When-Then pattern:

```python
def test_element_locator_accessibility_id_finds_element():
    # Given
    target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn-save")

    # When
    result = await locator.locate(target)

    # Then
    assert result is not None
    assert result.strategy_used == LocateStrategy.ACCESSIBILITY_ID
```

### Running Tests

```bash
# All tests
uv run pytest

# Specific module
uv run pytest tests/unit/test_bt_generator.py

# With coverage
uv run pytest --cov=src --cov-report=term-missing

# Lint and type check
uv run ruff check .
uv run mypy src/
```

## Pull Request Process

1. Create a feature branch from `develop`
2. Make changes with proper tests
3. Ensure all checks pass:
   - `ruff check .` — no errors
   - `mypy src/` — no errors
   - `pytest` — all tests pass
   - Coverage meets requirements
4. Submit PR using the PR template
5. Address review feedback
6. Squash merge after approval

## Pre-commit Hooks

The project uses pre-commit to enforce code quality:

```bash
pre-commit install
```

Hooks run automatically on commit:
- `ruff format` — auto-format
- `ruff check --fix` — lint and auto-fix
- `mypy` — type checking (manual only)

## Questions?

Open an issue using the appropriate template:
- Bug Report: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Feature Request: `.github/ISSUE_TEMPLATE/feature_request.yml`
