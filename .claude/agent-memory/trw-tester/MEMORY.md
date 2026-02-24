# TRW Tester Agent Memory

## Project Context
- Working in: `/mnt/c/Users/Tyler/Desktop/trw_framework/trw-mcp`
- Run tests from the `trw-mcp/` directory using absolute paths
- asyncio_mode = "auto" — async tests run automatically
- Global coverage threshold: 80% (currently at 98%)
- Test count as of 2026-02-22: ~2003 tests

## Key Test Commands
```bash
# Full suite
cd /mnt/c/Users/Tyler/Desktop/trw_framework/trw-mcp && .venv/bin/python -m pytest tests/ -v --cov=trw_mcp --cov-report=term-missing

# Targeted module coverage
.venv/bin/python -m pytest tests/test_X.py --cov=trw_mcp.module_name --cov-report=term-missing
```

## Confirmed Patterns

### Patching module-level singletons in scoring.py
`scoring.py` has `_reader = FileStateReader()` and `_config = get_config()` at module level.
- Patch methods directly: `scoring_mod._reader.read_yaml = my_fn` (restore in try/finally)
- Override Pydantic BaseSettings fields: `object.__setattr__(cfg, "field", value)` — bypasses validation

### Pydantic BaseSettings field override in tests
```python
old_config = scoring_mod._config
try:
    cfg = scoring_mod._config.__class__()
    object.__setattr__(cfg, "field_name", value)
    scoring_mod._config = cfg
    # ... test ...
finally:
    scoring_mod._config = old_config
```

### Writing corrupt YAML to trigger exception paths
Write `{invalid yaml[` to a .yaml file — FileStateReader will raise StateError.
The tested code's `except (StateError, ValueError, TypeError): continue` will skip it.

### Testing future timestamps for elapsed_secs < 0 branches
```python
future_ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
```

### Fixtures to use
- `tmp_path` — built-in pytest temp directory
- `writer` from conftest — `FileStateWriter()` instance
- `reader` from conftest — `FileStateReader()` instance
- `monkeypatch` — for patching module-level functions

## Patching Gotchas

### Function-local imports cannot be patched at consumer module
When a function uses a local import inside its body (not at module level):
- `from importlib.resources import files as pkg_files` → patch `importlib.resources.files`
- `from trw_mcp.state.validation import validate_prd_quality_v2` → patch `trw_mcp.state.validation.validate_prd_quality_v2`
- Patching `trw_mcp.audit.pkg_files` fails with AttributeError — the name doesn't exist at module scope.

### YAML `": invalid\n"` is valid, not corrupt
`": invalid\n"` parses as `{None: 'invalid'}`, NOT a parse error. To trigger exception handlers in YAML readers, use a mock: `mock_reader.read_yaml.side_effect = StateError("failed")`.

## YAML in Test Fixtures — Critical Rule
Summaries with colons (e.g. "Success: foo", "Error: bar") MUST be double-quoted in test write_text() helpers:
- Use: `f'summary: "{escaped_summary}"'` where `escaped_summary = summary.replace('"', '\\"')`
- Without quotes: ruamel.yaml raises ScannerError on the colon

## Exception Handler Coverage — Reachability Rule
`except Exception: pass` blocks only count as covered if the exception path is taken.
If the function has an early-return guard (e.g. `if not task_root.exists(): return ...`),
you must bypass that guard by creating the prerequisite data before triggering the exception.

## Covered Modules (100%)
- `state/recall_search.py` — 100% (2026-02-22)
- `scoring.py` — 100% (2026-02-22)
- `state/report.py` — 100% (2026-02-22)
- `audit.py` — 100% (2026-02-22)
- `state/analytics.py` — 100% (2026-02-22)
- `state/analytics_report.py` — 100% (2026-02-22)
- `state/reflection.py` — 100% (2026-02-22, via test_final_coverage_push.py)
- `tools/ceremony.py` — 100% (2026-02-22, via test_final_coverage_push.py)
- `tools/learning.py` — 100% (2026-02-22, via test_final_coverage_push.py)
- `tools/requirements.py` — 100% (2026-02-22, via test_final_coverage_push.py)
- `tools/telemetry.py` — 100% (2026-02-22, via test_final_coverage_push.py)

## Covered Modules (99%+)
- `state/prd_utils.py` — 99% (2026-02-22, line 111 is unreachable dead code)
- `state/claude_md.py` — 99% (2026-02-22, lines 99/252/794 are defensive dead code)
- `state/validation.py` — 99% (2026-02-22, lines 1317+1343 are defensive dead code, see below)
- `state/auto_upgrade.py` — 95% (2026-02-22, lines 29-30 are ImportError branches — unreachable when installed)

## Validation Dead Code (validation.py lines 1317, 1343)
- Line 1317: `return False` inside `if stripped.startswith("<!--") and stripped.endswith("-->")` in `_is_substantive_line` — dead because `_PLACEHOLDER_RE` pattern already matches `<!-- ... -->` (first alternative `r'^\s*<!--.*?-->\s*$'`), so line 1314 returns False first.
- Line 1343: `return SectionScore(section_name=section_name)` when `total == 0` — dead because `str.split('\n')` always returns at least `['']`, so `total` is never 0.

## Global Coverage
- 99.84% as of 2026-02-22 (2112 tests)

## Publisher/Telemetry Gotcha
`telemetry/publisher.py` looks at `trw_dir / "learnings" / "entries"` for YAML files.
When mocking `resolve_trw_dir`, the entries_dir must be `tmp_path / "learnings" / "entries"`.
Using `tmp_path / "entries"` directly will result in "no_entries" skipped_reason.

## Test File Locations
- `/mnt/c/Users/Tyler/Desktop/trw_framework/trw-mcp/tests/test_recall_scoring_report.py` — 77 tests covering recall_search, scoring, report gaps
- `/mnt/c/Users/Tyler/Desktop/trw_framework/trw-mcp/tests/test_prd_audit_claudemd.py` — 104 tests covering prd_utils, audit, claude_md gaps
- `/mnt/c/Users/Tyler/Desktop/trw_framework/trw-mcp/tests/test_analytics_coverage.py` — 60 tests covering analytics.py + analytics_report.py gaps
- `/mnt/c/Users/Tyler/Desktop/trw_framework/trw-mcp/tests/test_final_misc_coverage.py` — 46 tests covering bootstrap, llm client, models/run, index_sync, recall_tracking, publisher, auto_upgrade, aaref, messaging, _paths
- `/mnt/c/Users/Tyler/Desktop/trw_framework/trw-mcp/tests/test_final_coverage_push.py` — 52 tests covering ceremony, learning, requirements, telemetry, validation, reflection gaps

## Function-Local Import Patching (validation.py pattern)
validation.py uses function-local imports extensively (not at module level). Must patch at source:
- `discover_governing_prds` → patch `trw_mcp.state.prd_utils.discover_governing_prds`
- `parse_frontmatter` → patch `trw_mcp.state.prd_utils.parse_frontmatter`
- `resolve_project_root` → patch `trw_mcp.state._paths.resolve_project_root`
- `check_transition_guards`, `is_valid_transition`, `update_frontmatter` → patch at `trw_mcp.state.prd_utils.*`
Patching `trw_mcp.state.validation.X` FAILS with AttributeError — the names don't exist at module scope.

## Path.read_text Selective Patching Pattern
When OSError test needs to fail only specific file reads (not all):
```python
original_read_text = Path.read_text
def selective_read_text(self: Path, *args, **kwargs) -> str:
    if self == target_path:
        raise OSError("permission denied")
    return original_read_text(self, *args, **kwargs)
with patch.object(Path, "read_text", selective_read_text):
    result = check_integration(src_dir)
```
