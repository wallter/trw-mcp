# Code Simplifier Agent Memory

## Key Project Patterns

- `json.loads` only raises `ValueError` (and its subclass `JSONDecodeError`) — never `KeyError`. Remove `KeyError` from except clauses in JSON parsing helpers.
- `except (ValueError, KeyError)` is a common dead-catch pattern in LLM response parsers; simplify to `except ValueError`.
- Multi-assignment loops building a list string can become list comprehensions when there is no branching inside the loop body — use implicit string concatenation for long f-strings spanning two lines.
- `pragma: no cover` functions are in `state/llm_helpers.py` — do not remove these markers.
- **Shadow locals aliasing config fields**: locals like `max_entries = _config.recall_receipt_max_entries` used only for guard/slice ops should be renamed to a short descriptive name (e.g. `limit`) and intermediate slice variables inlined. Avoids stale shadow aliases.
- **Preserve named multi-line generators**: `content = "".join(...)` spanning multiple lines should stay named even if single-use — readability outweighs DRY for complex expressions.
- **Preserve DEBT/PRD inline comments**: comments like `# Rewrite the file atomically (DEBT-028)` carry traceability and must never be removed.

## File Map (trw-mcp/src/trw_mcp/)

- `state/llm_helpers.py` — LLM integration helpers; all functions are `pragma: no cover`
- `state/` — persistence, validation, claude_md, analytics, recall_search, reflection, index_sync, prd_utils, llm_helpers
- `tools/` — ceremony (2), learning (3), orchestration (3), requirements (2), build (1)
- `models/` — Pydantic v2 models

## Preservation Rules Observed

- Never remove `# pragma: no cover` markers
- Never modify structlog `.bind()` / `.info()` / `.warning()` calls — `event` is a reserved kwarg
- Never change public function signatures
- Never remove type annotations

## dict[str, object] mypy --strict numeric extraction

Always use `float(str(x))` and `int(str(x))` when extracting numeric values from
`dict[str, object]` — never use `# type: ignore`. This is the established project
pattern (confirmed in scoring.py, validation.py, analytics.py, claude_md.py).

## Conditional expression for flag + list pattern (safe)

In functions returning `{"clean": bool, "failures": list}`, this consolidation is safe:
```python
failures = _extract_failures(output, markers) if not clean else []
return {"clean": clean, "failures": failures}
```

## Redundant self-documenting comments (safe to remove)

Comments that restate what the immediately following code does are safe to remove.
Examples seen in orchestration.py:
- `# Build variables dict` before a dict literal assignment
- `# Write run.yaml` before `_writer.write_yaml(...)`
- `# Initialize events.jsonl` before `_events.log_event(...)`
- `# Read current state` before `_reader.read_yaml(...)`
- `# Append to checkpoints.jsonl` before `_writer.append_jsonl(...)`
- `# Count events` before `events = _reader.read_jsonl(...)`
- `# Read wave manifest if exists` before the wave manifest lookup block
- `# Create .trw/ structure if it doesn't exist` before the subdirs loop
- `# Write default config if missing` before `if not _reader.exists(config_path):`
- `# Create run directory structure` before path assignments
- `# Read shard manifest for status data` before shard_statuses assignment
- `# Count shard statuses for this wave` before counts dict
- `# By-trigger aggregation` before the by_trigger loop
- `# Deploy framework files from bundled data` before framework_files list
- `# Write VERSION.yaml` before version_data dict

## Comments worth keeping in orchestration.py

- `# Generate run ID: timestamp + random suffix for uniqueness` — explains the non-obvious use of `secrets.token_hex`
- `# Write .trw/.gitignore from bundled template (DRY with bootstrap.py)` — cross-file traceability
- `# Deploy frameworks and templates to .trw/` — groups two related calls
- `# Resolve task_root: explicit param > config field > default "docs"` — explains precedence chain
- `# Classification with configurable thresholds` — explains threshold source
- `# Latest reversion` — labels a logical section
- `# Reversion frequency metrics` and `# Stale framework version warning` in trw_status — section labels
- `# Version mismatch — log upgrade event` — explains why the branch fires
- `# Create checkpoint record` — labels start of multi-step construction
- `# Reflection metrics (count only, no need to collect full lists)` — explains deliberate design choice

## Single-use locals safe to inline in orchestration.py

- `run_yaml_path = meta_path / "run.yaml"` in `trw_status` — inline into `_reader.read_yaml(...)` directly
- `current_versions = (fw, aaref, pkg)` tuple in `_deploy_frameworks` — inline into `==` comparison; `existing_versions` is multi-use (comparison + log), keep named
