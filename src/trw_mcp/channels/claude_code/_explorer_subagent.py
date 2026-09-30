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

import hashlib
import os
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "EXPLORER_AGENT_RELPATH",
    "EXPLORER_MODEL_ENV_VAR",
    "EXPLORER_QUOTA_BYTES",
    "cc05_explorer_user_edited",
    "get_explorer_agent_content",
    "install_cc05_subagent",
    "withdraw_cc05_subagent_if_unedited",
]

EXPLORER_AGENT_RELPATH: str = ".claude/agents/trw-distill-explorer.md"
EXPLORER_QUOTA_BYTES: int = 8192

# PRD-CORE-210 FR07 (FUTURE-WORK §3a): operator override for the CC-05 model,
# resolved at template-render time. Allowlisted so a typo or a disallowed
# tier (fable is main-loop only by operator rule) can never land in the
# generated agent file. Since 2026-09-22 the default MAIN-loop model is Opus
# 5.5 and Fable 5.1 is the explicit escalation tier; neither changes this
# allowlist. A read-only explorer is the cheapest lane in the system, and
# ``opus`` is deliberately absent too: an Opus-priced explorer defeats the
# point of delegating the sweep (see tier_resolver's policy note).
EXPLORER_MODEL_ENV_VAR: str = "CLAUDE_CODE_EXPLORER_MODEL"
_EXPLORER_DEFAULT_MODEL: str = "haiku"
_EXPLORER_ALLOWED_MODELS: frozenset[str] = frozenset({"haiku", "sonnet"})

_EXPLORER_CONTENT = """\
---
name: trw-distill-explorer
description: >
  Read-only codebase intelligence specialist powered by trw-distill.
  Use when needing file-set risk hints, hotspots from an operator-run
  `trw-mcp code risk` report, or conventions.
  Do NOT use for single-file pre-edit hints — use the PreToolUse hook instead.
model: {model}
maxTurns: 20
effort: medium
permissionMode: default
tools:
  - Read
  - Glob
  - Grep
  - mcp__trw__trw_code
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

trw-distill is an optional, proprietary tier: on a project without it, `trw_code`
reports `distill_status: "unavailable"` and no sidecar/hotspot data exists yet —
that means the free tier is running as designed, not that something is broken.
Say so plainly, point at `distill_action` (or `trw-mcp code risk` for an operator
to run) for how to get the data, and never call it a failure.

## Trigger Phrases

Invoke this subagent when asked for:
- **Per-file risk hints** — use `trw_code(mode="hint", files=...)`
- **Hotspot ranking** — top-N files by risk score (full report: an operator
  runs `trw-mcp code risk` from a shell; this read-only, no-shell subagent
  cannot run it itself)
- **Convention summaries** — use `trw_recall` for code patterns

## trw-distill CLI (operator-run; this subagent has no Bash tool)

This subagent cannot run these itself — name the command for the operator
to run from a shell. All support `--json` for machine-readable output.

- `trw-distill query callers|uses|def <symbol>`, `trw-distill query callees <target>`,
  `trw-distill query deps|importers|tests <path>` — codebase relationships, e.g.
  `trw-distill query deps app/billing.py`.
- `trw-distill rca trace <traceback-file>` (`-` reads a piped traceback),
  `trw-distill rca raises <exception-name>`, `trw-distill rca history <path>` — root-cause
  helpers, e.g. `trw-distill rca trace crash.txt`.
- **A failing test**: suggest `trw-distill query deps <path>` on the test file FIRST — it names what the
  test touches before `rca trace` ranks where a saved traceback points.

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
2. Call the most specific MCP tool (e.g., `trw_code(mode="hint", files=[...])`
   for a named set of files). A repo-wide ranking needs `trw-mcp code risk`, an
   operator CLI command this subagent has no shell access to run.
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


def _framework_explorer_hashes() -> set[str]:
    """sha256 of every TRW rendering of the explorer agent (any allowlisted ``model:``)."""
    return {
        hashlib.sha256(_EXPLORER_CONTENT.format(model=model).encode("utf-8")).hexdigest()
        for model in sorted(_EXPLORER_ALLOWED_MODELS)
    } | {hashlib.sha256(get_explorer_agent_content().encode("utf-8")).hexdigest()}


def cc05_explorer_user_edited(repo_root: Path, manifest_hashes: dict[str, str] | None) -> bool:
    """True when an existing explorer agent is bytes TRW cannot prove it wrote (HB-2: keep it).

    Uses the shared managed-artifact guard (``artifact_user_edited_against``): matching the recorded
    manifest hash or any framework render is TRW's own; anything else, or an unreadable file, is kept.
    """
    from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited_against

    target = repo_root / EXPLORER_AGENT_RELPATH
    if not target.is_file():
        return False
    try:
        return artifact_user_edited_against(
            target, EXPLORER_AGENT_RELPATH, _framework_explorer_hashes(), manifest_hashes
        )
    except OSError:
        return True


def install_cc05_subagent(repo_root: Path, manifest_hashes: dict[str, str] | None = None) -> bool:
    """Install the CC-05 subagent file to ``.claude/agents/``.

    Idempotent: an identical file is not rewritten. A file TRW cannot prove it wrote (see
    :func:`cc05_explorer_user_edited`) is never overwritten.

    Args:
        repo_root: Repository root directory.
        manifest_hashes: ``content_hashes`` of the manifest as it stood before this run, or None.

    Returns:
        True if the file was written (new or refreshed); False if unchanged or kept.
    """
    target = repo_root / EXPLORER_AGENT_RELPATH
    content = get_explorer_agent_content()

    if len(content.encode("utf-8")) > EXPLORER_QUOTA_BYTES:
        log.warning(
            "cc05_subagent_quota_exceeded",
            bytes=len(content.encode("utf-8")),
            quota=EXPLORER_QUOTA_BYTES,
        )

    if cc05_explorer_user_edited(repo_root, manifest_hashes):
        log.debug("cc05_subagent_kept_user_edited", path=str(target))
        return False

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


def withdraw_cc05_subagent_if_unedited(repo_root: Path, manifest_hashes: dict[str, str] | None) -> bool:
    """Remove ``.claude/agents/trw-distill-explorer.md`` when distill is no longer entitled.

    2026-09-27 audit (touchpoint #6): ``update-project`` on an unentitled
    project never wrote this file, but never removed one either — a project
    that lost its licence (or copied the checkout to a distill-free machine)
    kept a "powered by trw-distill" agent describing tools it could no longer
    answer for. Removes it only when it still matches a TRW-rendered baseline
    (any allowlisted ``model:``, per :func:`_resolve_explorer_model`, since
    both are legitimate framework renderings — mirrors
    ``_managed_client_artifacts.artifact_user_edited_against``'s agent-file
    contract) or the recorded manifest hash; a genuine user edit is preserved,
    exactly like a normal update would preserve it.

    Returns True when the file was removed.
    """
    from trw_mcp.bootstrap._safe_remove import remove_if_hash

    target = repo_root / EXPLORER_AGENT_RELPATH
    if not target.is_file():
        return False
    # One verdict for install and withdraw (W3's cc05_explorer_user_edited), so they can never disagree.
    if cc05_explorer_user_edited(repo_root, manifest_hashes):
        log.debug("cc05_subagent_withdraw_skipped_user_edited", path=str(target))
        return False
    try:
        current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        # trw-fail-silent-allow: an unreadable file is preserved, the safe side; the DEBUG line is the record
        log.debug("cc05_subagent_withdraw_skipped_unreadable", path=str(target))
        return False
    # The hash handed to remove_if_hash must itself be a proven TRW render or the recorded hash: an edit landing
    # between the verdict above and this read would otherwise be withdrawn as if it were TRW's.
    if current_hash not in _framework_explorer_hashes() | {(manifest_hashes or {}).get(EXPLORER_AGENT_RELPATH)}:
        log.debug("cc05_subagent_withdraw_skipped_changed", path=str(target))
        return False
    # remove_if_hash re-verifies the captured bytes against this hash and links them back on any change, so an
    # edit saved after this check keeps its bytes (HB-2); the unedited file stays in .trw/trash.
    outcome = remove_if_hash(target, repo_root, current_hash, key=EXPLORER_AGENT_RELPATH)
    if outcome.status != "removed":
        log.debug("cc05_subagent_withdraw_kept", path=str(target), reason=outcome.reason)
        return False
    log.debug("cc05_subagent_withdrawn", path=str(target))
    return True
