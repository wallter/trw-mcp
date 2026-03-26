---
name: trw-monorepo-sync
model: claude-sonnet-4-6
description: >
  Sync cross-package dependencies in the TRW monorepo. Ensures local
  packages are installed in each other's venvs so tests use current
  source, not stale PyPI versions.
  Use: /trw-monorepo-sync
user-invocable: true
argument-hint: "[package or 'all']"
allowed-tools: Read, Grep, Glob, Bash
---

# Monorepo Dependency Sync

Detect and fix cross-package version drift in the TRW monorepo. Prevents the silent failure mode where tests pass against stale PyPI packages instead of current local source.

## The Problem

The monorepo has interdependent packages:
- `trw-mcp` depends on `trw-memory`
- `trw-eval` depends on `trw-memory`

Each package has its own venv. When you modify `trw-memory` locally but `trw-mcp/.venv` has the old PyPI version installed, `trw-mcp` tests will:
- Import the old `trw-memory` (missing new models/functions)
- Pass or fail for the wrong reasons
- Cause confusion when "working" code breaks in CI

## Dependency Map

| Package | Depends On | venv Location |
|---------|-----------|---------------|
| `trw-mcp` | `trw-memory` | `trw-mcp/.venv/` or system `site-packages` |
| `trw-eval` | `trw-memory` | `trw-eval/.venv/` |
| `trw-memory` | (none) | `trw-memory/.venv/` |

## Workflow

### Step 1: Detect Installed Versions

For each package that has dependencies, check what version is installed:

```bash
# For trw-mcp:
cd trw-mcp && pip show trw-memory 2>&1 | grep -E "Version|Location|Editable"

# For trw-eval:
cd trw-eval && pip show trw-memory 2>&1 | grep -E "Version|Location|Editable"
```

### Step 2: Compare Against Local Source

```bash
grep '^version' trw-memory/pyproject.toml
```

### Step 3: Detect Drift

A package is "drifted" if:
- The installed version differs from the local pyproject.toml version, AND
- It is NOT an editable install pointing to the local source

### Step 4: Report

Show a table:

```
## Monorepo Dependency Health

| Consumer | Dependency | Installed | Local | Status |
|----------|-----------|-----------|-------|--------|
| trw-mcp | trw-memory | 0.3.0 (PyPI) | 0.4.0 | DRIFTED |
| trw-eval | trw-memory | 0.4.0 (editable) | 0.4.0 | OK |
```

### Step 5: Fix (with confirmation)

For each drifted dependency, ask the user to confirm, then:

```bash
cd <consumer-package> && pip install -e "../<dependency>[dev]"
```

If the consumer doesn't have its own venv (uses system Python), use:
```bash
pip install -e "<dependency-path>[dev]"
```

### Step 6: Verify

After fixing, re-run the version check to confirm alignment. Also run a quick import test:

```bash
cd <consumer> && python -c "from <dependency>.models.memory import MemoryEntry; print('OK')"
```

## When to Run

- **Before running cross-package tests** (e.g., trw-mcp tests that import trw-memory)
- **After modifying a dependency's models or API** (new fields, renamed functions)
- **After `pip install` from PyPI** (which overwrites editable installs)
- **At sprint start** if the sprint touches multiple packages

## Automatic Detection Hint

If you see errors like:
- `ImportError: cannot import name 'Assertion' from 'trw_memory.models.memory'`
- `AttributeError: 'LearningParams' object has no attribute 'assertions'`
- `No parameter named "assertions"` in test output

These are almost always caused by stale installed packages. Run `/trw-monorepo-sync` to fix.
