# TRW Codebase Conventions

Reference for the TRW code simplifier. These patterns are load-bearing and must be preserved.

## Pydantic v2

- `model_config = ConfigDict(use_enum_values=True)` - Required for YAML round-trip serialization on models with enum fields
- `model_config = ConfigDict(populate_by_name=True)` - Required when using `Field(alias=...)`
- `model_config = ConfigDict(frozen=True)` - Used for immutable value objects
- Field named `validate` conflicts with `BaseSettings` - use an alias if needed
- `dict[str, object]` values need `str()` cast for mypy --strict compliance

## Atomic Persistence

- `lock_for_rmw(path)` context manager for read-modify-write cycles
- Temp-file-then-rename pattern: write to `{path}.tmp`, then `os.rename` to `{path}`
- These prevent data corruption on concurrent writes and crashes

## structlog

- `event` is a RESERVED keyword in structlog - never use it as a kwarg name
- Use alternative names like `action`, `operation`, `msg` instead
- Logging calls follow pattern: `logger.info("event_name", key=value)`

## PRD Traceability

- Comments like `# PRD-QUAL-010-FR02` trace code to requirements
- Comments like `# PRD-CORE-001: Base MCP tool suite` provide module context
- These comments MUST be preserved for traceability audits

## Test Patterns

- `asyncio_mode = "auto"` in pyproject.toml - async tests run automatically
- Fixtures: `tmp_project`, `config`, `sample_run_dir`, `reader`, `writer`, `event_logger`
- Coverage excludes `server.py`, `TYPE_CHECKING` blocks, and `main()` functions
- Markers: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`

## Configuration

- `TRWConfig(BaseSettings)` with `env_prefix="TRW_"` - all fields overridable via env vars
- Singleton pattern via `get_config()` / `_reset_config()` for tests
- Config fields use snake_case, env vars use `TRW_UPPER_SNAKE_CASE`
