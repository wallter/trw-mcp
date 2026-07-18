"""Claude Code CC-05 channel: trw-distill-explorer subagent installer.

Belongs to the ``channels/claude_code`` package (PRD-DIST-2405 FR37-FR40).

Installs ``.claude/agents/trw-distill-explorer.md`` at ``init-project``
and ``update-project`` time for the Claude Code client.

The subagent is:
- Read-only: no Write, Edit, Bash, trw_learn, trw_checkpoint, trw_deliver, Agent
- Restricted to risk-analysis MCP tools
- haiku model with 20-turn limit and 600-token output cap
- Context-isolated: only invoked for codebase risk analysis delegation

Anti-example embedded: "Do NOT use for single-file pre-edit hints — use the
PreToolUse hook instead."

PRD-DIST-2405 FR37-FR40.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "EXPLORER_AGENT_RELPATH",
    "EXPLORER_MODEL_ENV_VAR",
    "EXPLORER_QUOTA_BYTES",
    "get_explorer_agent_content",
    "install_cc05_subagent",
]

EXPLORER_AGENT_RELPATH: str = ".claude/agents/trw-distill-explorer.md"
EXPLORER_QUOTA_BYTES: int = 8192

# PRD-CORE-210 FR07 (FUTURE-WORK §3a): operator override for the CC-05 model,
# resolved at template-render time. Allowlisted so a typo or a disallowed
# tier (fable is main-loop only by operator rule) can never land in the
# generated agent file.
EXPLORER_MODEL_ENV_VAR: str = "CLAUDE_CODE_EXPLORER_MODEL"
_EXPLORER_DEFAULT_MODEL: str = "haiku"
_EXPLORER_ALLOWED_MODELS: frozenset[str] = frozenset({"haiku", "sonnet"})

_EXPLORER_CONTENT = """\
---
name: trw-distill-explorer
description: >
  Read-only codebase intelligence specialist powered by trw-distill.
  Use when you need: full codebase risk analysis, entity risk map, ordering comparison,
  top-N hotspot ranking, and convention summaries.
  Do NOT use for single-file pre-edit hints — use the PreToolUse hook instead.
model: {model}
maxTurns: 20
effort: medium
memory: project
permissionMode: default
tools:
  - Read
  - Glob
  - Grep
  - mcp__trw__trw_before_edit_hint
  - mcp__trw__trw_before_edit_hint_batch
  - mcp__trw__trw_codebase_risk_report
  - mcp__trw__trw_entity_risk_map
  - mcp__trw__trw_code_search
  - mcp__trw__trw_code_symbol
  - mcp__trw__trw_recall
disallowedTools:
  - Bash
  - Write
  - Edit
  - MultiEdit
  - mcp__trw__trw_learn
  - mcp__trw__trw_checkpoint
  - mcp__trw__trw_deliver
  - mcp__trw__trw_init
  - Agent
color: cyan
---

# TRW Distill Explorer

You are a **read-only codebase intelligence specialist**. Your role is to surface
trw-distill risk data via MCP tools and return structured Markdown reports.

## Trigger Phrases

Invoke this subagent when asked for:
- **Full codebase risk analysis** — use `trw_codebase_risk_report`
- **Entity risk map** — use `trw_entity_risk_map`
- **Ordering comparison** — compare risk scores across files
- **Hotspot ranking** — top-N files by risk score
- **Convention summaries** — use `trw_recall` for code patterns

## Rules

- Do NOT suggest edits.
- Do NOT run bash commands.
- Do NOT write or modify any files.
- Do NOT call `trw_learn`, `trw_checkpoint`, `trw_deliver`, or `trw_init`.
- Remain focused on one scope per invocation.
- Return structured Markdown ONLY.
- Maximum output: **600 tokens**.
- Respect a maximum of **20 turns** per invocation.

## Tool Usage Protocol

1. Read the user's risk-analysis request.
2. Call the most specific MCP tool (e.g., `trw_entity_risk_map` for entity maps).
3. If the sidecar is missing, surface the action from `distill_action` field.
4. Format the response using the return format below.
5. Never expand scope beyond what was requested.
6. Stop after returning the report — no follow-up actions.

## Return Format

Always structure your response with these sections:

```
## TOP RISK FILES
| File | Risk Score | Notes |
|------|-----------|-------|
...

## ACTIONABLE RECOMMENDATIONS
1. ...
2. ...

## DATA PROVENANCE
Sidecar SHA: <sha8> | Tier: <tier> | Generated: <date>
```

Maximum 600 tokens total. Truncate if needed with "... (truncated for brevity)".
"""


def _resolve_explorer_model() -> str:
    """Resolve the explorer model at render time (PRD-CORE-210 FR07).

    ``CLAUDE_CODE_EXPLORER_MODEL`` may name an allowlisted model alias;
    anything else (including ``fable`` — main-loop only by operator rule)
    logs a warning and falls back to the haiku default.
    """
    requested = os.environ.get(EXPLORER_MODEL_ENV_VAR, "").strip().lower()
    if not requested:
        return _EXPLORER_DEFAULT_MODEL
    if requested in _EXPLORER_ALLOWED_MODELS:
        return requested
    log.warning(
        "cc05_explorer_model_rejected",
        requested=requested,
        allowed=sorted(_EXPLORER_ALLOWED_MODELS),
        fallback=_EXPLORER_DEFAULT_MODEL,
    )
    return _EXPLORER_DEFAULT_MODEL


def get_explorer_agent_content() -> str:
    """Return the content for ``trw-distill-explorer.md`` (render-time model)."""
    return _EXPLORER_CONTENT.format(model=_resolve_explorer_model())


def install_cc05_subagent(repo_root: Path) -> bool:
    """Install the CC-05 subagent file to ``.claude/agents/``.

    Idempotent: if the file already exists with the same content,
    no write occurs.

    Args:
        repo_root: Repository root directory.

    Returns:
        True if the file was written (new or updated); False if unchanged.
    """
    target = repo_root / EXPLORER_AGENT_RELPATH
    content = get_explorer_agent_content()

    if len(content.encode("utf-8")) > EXPLORER_QUOTA_BYTES:
        log.warning(
            "cc05_subagent_quota_exceeded",
            bytes=len(content.encode("utf-8")),
            quota=EXPLORER_QUOTA_BYTES,
        )

    # Idempotency check
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
            if existing == content:
                log.debug("cc05_subagent_unchanged", path=str(target))
                return False
        except OSError:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    log.debug("cc05_subagent_written", path=str(target))
    return True
