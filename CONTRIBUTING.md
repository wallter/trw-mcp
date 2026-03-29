# Contributing to trw-mcp

Thank you for considering contributing to trw-mcp. This guide covers the development setup, conventions, and process.

## Prerequisites

- Python 3.10+
- git
- A virtual environment manager (venv, uv, etc.)

## Development Setup

```bash
git clone https://github.com/wallter/trw-mcp.git
cd trw-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
# Unit tests (fast, use during development)
pytest tests/ -m unit

# Quick full run with fail-fast
pytest tests/ -x -q

# Type checking (strict mode required)
mypy --strict src/trw_mcp/

# Single test file (preferred during development)
pytest tests/test_specific_file.py -v

# With coverage
pytest tests/ -v --cov=trw_mcp --cov-report=term-missing
```

## Architecture

```
src/trw_mcp/
  tools/         MCP entry points (thin — validation + delegation only)
  state/         Business logic (persistence, analytics, validation, search)
  models/        Pure data (Pydantic v2 models, config, typed dicts)
  server/        FastMCP wiring, CLI, middleware chain, transport
  middleware/    Request processing (ceremony enforcement, observation masking)
  bootstrap/     Project initialization (init-project, update-project)
  data/          Bundled agents, skills, hooks (auto-discovered at runtime)
  telemetry/     Telemetry pipeline (models, sender, anonymizer)
```

**Data flow**: `tools/` (MCP entry points, thin) -> `state/` (business logic) -> `models/` (pure data)

Tools are thin wrappers that validate input and delegate to `state/` modules. Keep tool functions focused on MCP registration and parameter handling.

## Module Size Rule

- **>500 lines**: Flag for review — check for mixed responsibilities
- **>800 lines**: Must decompose before merging. Extract into a sub-package with a public facade that re-exports symbols.

Check before editing large files:
```bash
wc -l src/trw_mcp/state/your_module.py
```

## Error Handling

All `except Exception` blocks require a `# justified:` comment explaining why the broad catch is necessary:

```python
except Exception:  # justified: fail-open, telemetry must not block tool execution
    logger.debug("telemetry_failed", exc_info=True)
```

This convention makes it easy to audit exception handling and prevents silent swallowing.

## Logging

Use structlog throughout. The `event` keyword is reserved by structlog — use alternative names:

```python
import structlog
logger = structlog.get_logger(__name__)

# Good
logger.info("operation_complete", result="success", count=5)

# Bad — do NOT use event= as a keyword argument
logger.info("operation_complete", event="bad")  # structlog reserves 'event'
```

## Commit Format

```bash
git commit -m "feat(scope): short description" -m "WHY: rationale for the change"
```

Scopes: `tools`, `state`, `models`, `server`, `middleware`, `bootstrap`, `telemetry`, `data`, `tests`, `docs`

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

## Pull Request Process

1. Branch from `main`
2. Write tests first (TDD), then implement
3. All tests must pass: `pytest tests/ -x -q`
4. Type checking must pass: `mypy --strict src/trw_mcp/`
5. Keep PRs focused — one concern per PR
6. Include a `WHY:` in your PR description

## Pydantic v2 Conventions

- Use `use_enum_values=True` on models for YAML round-trip serialization
- Use `populate_by_name=True` when using `Field(alias=...)`
- Avoid field names that conflict with BaseSettings methods (e.g., `validate`)

## Testing Conventions

- Fixtures live in `tests/conftest.py`: `tmp_project`, `config`, `reader`, `writer`
- Patch module-level imports at both source and consumer when needed
- Use `reset_backend()` autouse fixture for SQLite singleton isolation
- Coverage target: >=90% for new code

## Questions?

Open an issue on [GitHub](https://github.com/wallter/trw-mcp/issues) or start a discussion.
