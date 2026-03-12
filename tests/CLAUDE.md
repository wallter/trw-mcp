# trw-mcp Test Suite Guide

This guide is for AI agents writing or modifying tests in the `trw-mcp/tests/` directory.

## Quick Reference

```bash
# During implementation — run ONLY the file you changed:
cd trw-mcp
.venv/bin/python -m pytest tests/test_specific_file.py -v

# Run a single test:
.venv/bin/python -m pytest tests/test_scoring.py::test_compute_score_positive -v

# Unit tests only (fast):
.venv/bin/python -m pytest tests/ -m unit -v

# Skip slow tests:
.venv/bin/python -m pytest tests/ -m "not slow" -v

# Parallel full suite (delivery only):
.venv/bin/python -m pytest tests/ -n auto --dist worksteal -v

# Full suite with coverage (delivery only):
.venv/bin/python -m pytest tests/ --cov=trw_mcp --cov-report=term-missing
```

**NEVER run the full suite during implementation. It takes 8-15 minutes and blocks all other agents.**

## Test Count & Performance

- **3,799 tests** across 112 files
- **Collection**: ~38s (dominated by import overhead)
- **Unit tier** (`-m unit`): ~784 tests, target <60s
- **Full suite**: ~8-15min sequential, ~3-5min parallel
- **Coverage threshold**: 80% (fail_under)
- **Per-test timeout**: 120s

## Test Tiers

| Tier | Marker | Count | When to Run |
|------|--------|-------|-------------|
| `unit` | `-m unit` | ~784 | During implementation |
| `integration` | `-m integration` | ~3,000 | Before delivery |
| `e2e` | `-m e2e` | 0 (planned) | At delivery |
| `slow` | `-m slow` | ~15 | Only in full suite |

## Conftest Fixtures

### Autouse (run for EVERY test)

| Fixture | Purpose | Performance Note |
|---------|---------|-----------------|
| `_reset_config_singleton` | Resets `TRWConfig` between tests | Fast (~1ms) |
| `_reset_run_pin` | Clears `_pinned_runs` dict | Fast (~1ms) |
| `_reset_memory_backend` | Joins deferred thread + resets SQLite | Slow (~5-50ms) |

### Named Fixtures

| Fixture | Scope | Creates Filesystem? | Use In |
|---------|-------|-------------------|--------|
| `tmp_project` | function | Yes (`.trw/` structure) | Integration tests |
| `config` | function | No (just TRWConfig obj) | Any test |
| `reader` | function | No | Tests reading state |
| `writer` | function | No | Tests writing state |
| `event_logger` | function | No | Tests logging events |
| `sample_run_dir` | function | Yes (run directory) | Integration tests |

### Test Data Factories (`_factories.py`)

```python
from _factories import make_entry_data, write_entry, make_merge_scenario, make_run_dir

# Create minimal valid entry data:
data = make_entry_data(summary="test", tags=["unit"])

# Write an entry to disk (integration tests only):
write_entry(tmp_path, "test-entry", summary="test", tags=["integration"])
```

## Marker Classification

Tests are auto-assigned markers in `conftest.py`:

- Files in `_UNIT_FILES` frozenset → `@pytest.mark.unit`
- Files in `_SLOW_FILES` frozenset → `@pytest.mark.slow`
- Everything else → `@pytest.mark.integration` (default)

**To classify a new test as unit**: Add the filename to `_UNIT_FILES` in `conftest.py` (line 77-90). The test must NOT use `tmp_path`, `tmp_project`, or do any filesystem I/O.

**To classify a new test as slow**: Add the filename to `_SLOW_FILES` in `conftest.py` (line 92-96). Only for tests that individually take >5s.

## Known Gotchas

### Module-Level Imports
If the code under test has `_config = get_config()` at module level, patching `get_config` in tests won't work — the module already called it at import time. Solution: patch the module-level variable directly, or refactor to call `get_config()` inside function bodies.

```python
# Patch the already-resolved module-level config:
monkeypatch.setattr("trw_mcp.tools.ceremony._config", mock_config)

# Or patch get_config to return your mock (works if called at runtime):
monkeypatch.setattr("trw_mcp.tools.ceremony.get_config", lambda: mock_config)
```

### Singleton Reset
The `_reset_memory_backend` fixture calls `reset_backend()` which closes and reopens the SQLite connection. If your test creates a background thread that holds a reference to the old connection, you'll get segfaults. Always join threads in teardown.

### Deferred Delivery Thread
`trw_deliver()` spawns a background thread. The `_join_and_reset_deferred()` helper in conftest handles joining it between tests. If your test calls `trw_deliver()`, the thread is joined automatically — but if your test manually spawns threads that use the memory backend, you must join them yourself.

### asyncio Tests
`asyncio_mode = "auto"` is set — async test functions run automatically without `@pytest.mark.asyncio`. Just write `async def test_foo():` and it works.

### FastMCP Server Testing
Use the shared server/tool factories from conftest to avoid repeating the 3-step `FastMCP("test") + register + get_tools_sync` pattern:

```python
from tests.conftest import make_test_server, extract_tool_fn, get_tools_sync

# Create a server with specific tool groups registered:
server = make_test_server("ceremony", "checkpoint")
tools = get_tools_sync(server)

# Or extract a specific tool function directly:
deliver_fn = extract_tool_fn(make_test_server("ceremony"), "trw_deliver")

# Available tool groups: build, ceremony, ceremony_feedback, checkpoint,
# knowledge, learning, orchestration, report, requirements, review, usage
```

For existing helpers, `get_resources_sync` and `get_prompts_sync` are also available in conftest.

### structlog
`event` is a reserved keyword in structlog. Never use `event=` as a kwarg in log calls — use an alternative name.

## File Organization

Current structure is flat — all 112 test files in `tests/`. A migration to tiered directories is planned:

```
tests/
├── unit/           # Pure logic, no I/O
├── integration/    # Uses tmp_path, multi-tool
├── e2e/            # Full phase sequences
├── slow/           # Bootstrap, consolidation
├── _factories.py   # Shared factories
└── conftest.py     # Root conftest
```

Until the migration, use the frozenset-based classification system.

## Coverage Exclusions

The following are excluded from coverage and don't need test coverage:
- `server/__main__.py`, `_proxy.py`, `_transport.py`
- `TYPE_CHECKING` blocks
- `def main()` functions
- Lines with `...` (ellipsis, e.g., Protocol stubs)
- Lines with `pragma: no cover` comments
