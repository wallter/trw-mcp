# Prompts & Messaging — Sub-CLAUDE.md

**PRD**: [PRD-INFRA-012](../../../../docs/requirements-aare-f/prds/PRD-INFRA-012.md) — Centralized AI-Facing Messaging Registry

## What This Directory Is

Two subsystems for communicating with AI models and users:

1. **messaging.py** — Centralized message registry for all AI-facing strings (server instructions, ceremony warnings)
2. **aaref.py** — AARE-F requirements engineering prompts registered as MCP slash commands

## Directory Structure

| File | Purpose | PRD |
|------|---------|-----|
| `messaging.py` | Python API for `data/messages/messages.yaml` | PRD-INFRA-012 |
| `aaref.py` | 5 MCP prompts for AARE-F workflows | PRD-CORE-001 |
| `__init__.py` | Re-exports `get_message`, `get_message_or_default` | — |

### Related Data Files

| Path | Purpose |
|------|---------|
| `data/messages/messages.yaml` | Centralized message registry (single source of truth) |
| `data/prompts/*.md` | 5 AARE-F template files consumed by `aaref.py` |

## Messaging API (`messaging.py`)

```python
from trw_mcp.prompts import get_message, get_message_or_default

# Load message with optional format substitution
msg = get_message("server_instructions")
msg = get_message("ceremony_warning")

# Fallback if key missing (for backward compat)
msg = get_message_or_default("my_key", "fallback text")

# List-type messages
lines = get_message_lines("my_list_key")
```

### Consumers
- `server.py` — loads `server_instructions` for `FastMCP(instructions=...)`
- `middleware/ceremony.py` — loads `ceremony_warning` for unceremonied sessions
- Shell hooks — read deployed `.trw/context/messages.yaml` via grep

### Adding New Messages

1. Add the key to `data/messages/messages.yaml`
2. Use `get_message("your_key")` or `get_message_or_default("your_key", "fallback")` in Python
3. Add a test in `tests/test_prompts_messaging.py`

## Value-Oriented Framing Principle

All messages follow value-oriented framing (PRD-INFRA-012):

| Do | Don't |
|----|-------|
| "Call X to load your prior learnings" | "You MUST call X" |
| "Without it, you're working without context" | "CRITICAL: Skipping means you WILL fail" |
| "Running it now preserves your learnings" | "TRW BLOCK: Execute X before stopping" |

**Research basis**: Anthropic Claude 4.6 best practices show that emphatic language (CRITICAL/ALWAYS/NEVER) causes overtriggering on advanced models. Context and motivation enable generalization to novel situations (Constitutional AI).

**Exception**: Hard safety boundaries may still use direct language — but explain consequences rather than threaten.

## Key Conventions

- **No circular imports**: `messaging.py` imports nothing from `trw_mcp` modules (only stdlib + ruamel.yaml)
- **LRU caching**: `_load_messages()` is cached — YAML parsed once per process
- **Fallback pattern**: All consumers use `get_message_or_default()` with inline `_DEFAULT_*` constants
- **Simple YAML strings**: No block scalars (`>` or `|`) — keeps messages grep-able from shell
- **`{placeholder}` substitution**: Use `str.format()` syntax for runtime values

## Tests

```bash
# Messaging tests (run from trw-mcp/)
../.venv/bin/python -m pytest tests/test_prompts_messaging.py -v

# Ceremony warning framing tests
../.venv/bin/python -m pytest tests/test_middleware_ceremony.py::TestCeremonyWarningText -v
```
