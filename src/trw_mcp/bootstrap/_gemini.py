"""Gemini CLI-specific bootstrap configuration.

Generates and smart-merges repo-scoped Gemini CLI artifacts:
- GEMINI.md  (repo-wide instructions with TRW ceremony protocol)
- .gemini/settings.json  (MCP server config, JSON deep-merge)
- .gemini/agents/trw-*.md  (TRW role-based subagent definitions)

Based on research: docs/research/providers/gemini/integration-research.md
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from ._file_ops import _new_result, _record_write

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_GEMINI_SETTINGS_PATH = ".gemini/settings.json"
_GEMINI_AGENTS_DIR = ".gemini/agents"
_GEMINI_MD_PATH = "GEMINI.md"

# ---------------------------------------------------------------------------
# Marker constants
# ---------------------------------------------------------------------------

_GEMINI_TRW_START_MARKER = "<!-- trw:gemini:start -->"
_GEMINI_TRW_END_MARKER = "<!-- trw:gemini:end -->"

# ---------------------------------------------------------------------------
# Instructions content
# ---------------------------------------------------------------------------


def _gemini_instructions_content() -> str:
    """Generate GEMINI.md TRW ceremony section."""
    return f"""{_GEMINI_TRW_START_MARKER}
<!-- TRW AUTO-GENERATED — do not edit between markers -->

## TRW Framework Integration

This project uses the [TRW Framework](https://trwframework.com) for structured
AI-assisted development. TRW gives your Gemini CLI sessions persistent engineering
memory — patterns, gotchas, and project knowledge accumulate across sessions.

### Session Protocol

| Tool | When | Why |
|------|------|-----|
| `trw_session_start()` | First action | Loads prior learnings |
| `trw_learn(summary, detail)` | On discoveries | Saves findings for future sessions |
| `trw_checkpoint(message)` | After milestones | Resume point if context compacts |
| `trw_deliver()` | Last action | Persists session work |

### MCP Tools

All TRW tools are available via MCP as `mcp_trw_<tool_name>`.
Call `mcp_trw_trw_session_start` first in every session.

Key tools: `trw_session_start`, `trw_learn`, `trw_checkpoint`, `trw_deliver`,
`trw_init`, `trw_status`, `trw_recall`, `trw_build_check`, `trw_review`,
`trw_prd_create`, `trw_prd_validate`.

### Subagents

TRW provides specialized agents in `.gemini/agents/`:
- `@trw-explorer` — Fast codebase search and analysis (read-only)
- `@trw-implementer` — TDD implementation with full tool access
- `@trw-reviewer` — Code review specialist (read-only)
- `@trw-lead` — Orchestration and delegation

### Memory Routing

- Code patterns, gotchas, build tricks → `mcp_trw_trw_learn()`
- User preferences → Gemini's built-in `/memory add`

### Conventions

- Run tests after each change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)

{_GEMINI_TRW_END_MARKER}
"""


# ---------------------------------------------------------------------------
# Smart merge (mirrors _copilot.py pattern)
# ---------------------------------------------------------------------------


def _smart_merge_instructions(existing: str, trw_content: str) -> str:
    """Merge TRW section into existing GEMINI.md, preserving user content.

    Handles edge cases:
    - Both markers present in correct order -> replace between markers
    - End before start (corrupted) -> treat as no markers, append
    - Only one marker present -> treat as no markers, append
    - Empty existing content -> just the TRW content
    - Identical TRW content already present -> return unchanged (idempotent)
    """
    start_idx = existing.find(_GEMINI_TRW_START_MARKER)
    end_idx = existing.find(_GEMINI_TRW_END_MARKER)

    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        end_idx += len(_GEMINI_TRW_END_MARKER)
        merged = existing[:start_idx] + trw_content.rstrip("\n") + existing[end_idx:]
        if merged == existing:
            return existing
        return merged

    separator = "\n\n" if existing.strip() else ""
    return existing.rstrip() + separator + trw_content + "\n"


# ---------------------------------------------------------------------------
# Public API — Instructions
# ---------------------------------------------------------------------------


def generate_gemini_instructions(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate or smart-merge ``GEMINI.md``."""
    result = _new_result()
    instructions_path = target_dir / _GEMINI_MD_PATH
    existed = instructions_path.exists()
    trw_content = _gemini_instructions_content()

    try:
        if existed and not force:
            existing = instructions_path.read_text(encoding="utf-8")
            merged = _smart_merge_instructions(existing, trw_content)
            if merged == existing:
                result["preserved"].append(_GEMINI_MD_PATH)
                return result
            instructions_path.write_text(merged, encoding="utf-8")
        else:
            instructions_path.write_text(trw_content, encoding="utf-8")
        _record_write(result, _GEMINI_MD_PATH, existed=existed)
    except OSError as exc:
        result["errors"].append(f"Failed to write {instructions_path}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Public API — MCP config
# ---------------------------------------------------------------------------


def generate_gemini_mcp_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Deep-merge TRW MCP server entry into ``.gemini/settings.json``.

    Only touches ``mcpServers.trw`` — preserves all other settings and servers.
    """
    result = _new_result()
    settings_path = target_dir / _GEMINI_SETTINGS_PATH
    existed = settings_path.exists()

    existing: dict[str, object] = {}
    if existed:
        try:
            raw = settings_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                existing = parsed
        except (json.JSONDecodeError, OSError) as exc:
            result["errors"].append(f"Failed to read {_GEMINI_SETTINGS_PATH}: {exc}")
            return result

    mcp_servers = existing.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
    existing["mcpServers"] = mcp_servers

    trw_entry: dict[str, object] = {
        "command": "trw-mcp",
        "args": ["serve"],
        "trust": True,
    }
    mcp_servers["trw"] = trw_entry

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        _record_write(result, _GEMINI_SETTINGS_PATH, existed=existed)
    except OSError as exc:
        result["errors"].append(f"Failed to write {settings_path}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Agents — .gemini/agents/*.md with YAML frontmatter
# ---------------------------------------------------------------------------

_GEMINI_AGENT_TEMPLATES: dict[str, str] = {
    "trw-explorer.md": """\
---
name: trw-explorer
description: >
  Read-only codebase explorer for gathering evidence before edits.
  Fast search and targeted reads over broad scans.
tools:
  - read_file
  - read_many_files
  - glob
  - grep_search
  - list_directory
  - mcp_trw_*
model: gemini-2.5-flash
temperature: 0.1
max_turns: 15
timeout_mins: 10
---

Stay in exploration mode.
Trace the real execution path, cite files and symbols, and avoid proposing
fixes unless asked. Prefer fast search and targeted reads over broad scans.

Use `mcp_trw_trw_recall(query)` to check if the topic has been investigated before.
""",
    "trw-implementer.md": """\
---
name: trw-implementer
description: >
  Implementation-focused agent for bounded code changes.
  Writes tests first, then production code.
tools:
  - "*"
model: gemini-2.5-pro
temperature: 0.2
max_turns: 30
timeout_mins: 30
---

Own the requested fix or feature slice.
Make the smallest defensible change, keep unrelated files untouched, and
validate the behavior you changed.

Use `mcp_trw_trw_checkpoint(message)` after each working milestone.
Run tests after each change — fix failures before moving on.
Use `mcp_trw_trw_learn(summary, detail)` for any discoveries.
""",
    "trw-reviewer.md": """\
---
name: trw-reviewer
description: >
  Read-only reviewer focused on correctness, regressions, security,
  and missing tests.
tools:
  - read_file
  - read_many_files
  - glob
  - grep_search
  - list_directory
  - mcp_trw_*
model: gemini-2.5-pro
temperature: 0.1
max_turns: 20
timeout_mins: 15
---

Review like an owner.
Lead with concrete findings, prioritize correctness and missing tests, and
avoid style-only feedback unless it hides a real defect.

Use `mcp_trw_trw_learn(summary, detail)` to record any patterns or gotchas.
""",
    "trw-lead.md": """\
---
name: trw-lead
description: >
  Orchestration lead that plans work and delegates to specialists.
  Does NOT write production code — stays in delegate mode.
tools:
  - read_file
  - glob
  - grep_search
  - mcp_trw_*
model: gemini-2.5-pro
temperature: 0.3
max_turns: 20
timeout_mins: 20
---

Plan work, delegate to @trw-explorer, @trw-implementer, and @trw-reviewer.
Track progress via `mcp_trw_trw_status()`.

Use `mcp_trw_trw_checkpoint(message)` after each milestone.
Use `mcp_trw_trw_learn(summary, detail)` for discoveries.
Call `mcp_trw_trw_deliver()` when complete.
""",
}


def generate_gemini_agents(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate ``.gemini/agents/trw-*.md`` subagent definitions.

    Only writes TRW-managed agents (``trw-*`` prefix).
    User-created agents are never touched.
    """
    result = _new_result()
    agents_dir = target_dir / _GEMINI_AGENTS_DIR
    agents_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in _GEMINI_AGENT_TEMPLATES.items():
        path = agents_dir / filename
        existed = path.exists()
        rel_path = f"{_GEMINI_AGENTS_DIR}/{filename}"

        if existed and not force:
            result["preserved"].append(rel_path)
            continue

        try:
            path.write_text(content, encoding="utf-8")
            _record_write(result, rel_path, existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {path}: {exc}")

    return result
