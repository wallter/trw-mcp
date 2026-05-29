"""opencode trw-distill-explorer subagent installer.

Writes ``.opencode/agents/trw-distill-explorer.md`` at ``init-project`` and
``update-project`` time.  The file is a static install-time artifact with
no dynamic content — all intelligence is pulled at invocation time via MCP.

Explorer modes (FR21):
  ``@trw-distill-explorer <file-path>``   → single-file risk report
  ``@trw-distill-explorer hotspots``       → top-20 hotspots table
  ``@trw-distill-explorer conventions``    → grouped learnings (single recall)

Permissions: bash=deny, edit=deny, write=deny, read=allow.
Size cap: 8192 bytes (FR24 / NFR05).

PRD-DIST-2403 FR20-FR24.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "EXPLORER_AGENT_QUOTA_BYTES",
    "EXPLORER_AGENT_RELPATH",
    "get_explorer_agent_content",
    "install_explorer_agent",
]

EXPLORER_AGENT_RELPATH = ".opencode/agents/trw-distill-explorer.md"
EXPLORER_AGENT_QUOTA_BYTES = 8192

_EXPLORER_CONTENT = """\
---
name: trw-distill-explorer
description: "Read-only trw-distill intelligence specialist. Invoke with @trw-distill-explorer <scope> to get codebase risk reports, hotspot rankings, and convention summaries without modifying any files."
mode: subagent
permissions:
  bash: deny
  edit: deny
  write: deny
  read: allow
---

# TRW Distill Explorer

You are a **read-only codebase intelligence specialist**. Your role is to surface
trw-distill risk data via MCP tools and return structured Markdown reports.

## Rules

- Do NOT suggest edits.
- Do NOT run bash commands.
- Do NOT write or modify any files.
- Remain focused on one scope per invocation.
- Return structured Markdown ONLY.

## Invocation Modes

### Mode A — Single-file risk report

When invoked as `@trw-distill-explorer <file-path>`:

1. Call `trw_before_edit_hint(file_path="<file-path>")` via MCP.
2. Call `trw_entity_risk_map(file_path="<file-path>")` via MCP.
3. Return a Markdown report with sections:
   - **Risk Score** (`risk_score`)
   - **Importers** (`importers` list)
   - **Inferred Tests** (`inferred_tests` list)
   - **Co-change Neighbors** (`co_change_neighbors` list)
   - **Hotspot Warnings** (`hotspot_warnings` list)
   - **Learnings** (top-3 from `learnings` field)
4. If `distill_status == "stale_sha"`, include a staleness notice.
5. If `distill_status == "tier_required"`, note the tier gate and return
   only `trw_recall` learnings.

### Mode B — Project hotspots table

When invoked as `@trw-distill-explorer hotspots`:

1. Call `trw_codebase_risk_report(top_n=20)` via MCP.
2. Format results as a ranked Markdown table:
   | # | File | Score | Fanin | Churn | Untested |
   |---|------|-------|-------|-------|----------|
3. Label files with `composite_score > 0.8` as **[HIGH RISK]**.
4. End with: `N files analyzed, M high-risk (>0.8).`

### Mode C — Project conventions

When invoked as `@trw-distill-explorer conventions`:

1. Call `trw_recall(query="project conventions patterns style rules gotcha error edge case warning")` exactly ONCE via MCP.
2. Group results by tag in the response.
3. Return a Markdown summary of conventions and patterns.

## Output Contract

- Structured Markdown only — no prose preamble.
- Include a `> Note: opencode plugin hooks do not reliably intercept MCP tool calls; distill telemetry is recorded server-side.` footer.
"""


def get_explorer_agent_content() -> str:
    """Return the static explorer agent Markdown content.

    Returns:
        Explorer agent Markdown string (always under 8192 bytes).
    """
    return _EXPLORER_CONTENT


def install_explorer_agent(
    repo_root: Path,
    *,
    existing_sha256: str | None = None,
) -> dict[str, object]:
    """Write ``.opencode/agents/trw-distill-explorer.md`` if not user-modified.

    If *existing_sha256* matches the on-disk file's SHA-256, the file is
    considered user-modified and is preserved unchanged.

    Args:
        repo_root: Repository root directory.
        existing_sha256: SHA-256 of the previously-installed content
            (from ``.trw/managed-artifacts.yaml``), or None for first install.

    Returns:
        Dict with keys ``status`` (``"written"`` / ``"preserved"`` / ``"error"``)
        and ``sha256`` of the written (or preserved) content.
    """
    target = repo_root / EXPLORER_AGENT_RELPATH
    content = get_explorer_agent_content()
    content_bytes = content.encode("utf-8")

    # Size guard (FR24)
    if len(content_bytes) > EXPLORER_AGENT_QUOTA_BYTES:
        log.warning(
            "opencode_explorer_agent_quota_exceeded",
            size_bytes=len(content_bytes),
            quota_bytes=EXPLORER_AGENT_QUOTA_BYTES,
            outcome="quota_exceeded",
        )

    new_sha = hashlib.sha256(content_bytes).hexdigest()

    try:
        # Detect user modification (FR23)
        if existing_sha256 and target.exists():
            on_disk_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if on_disk_sha != existing_sha256:
                log.debug(
                    "opencode_explorer_agent_user_modified",
                    path=str(target),
                    outcome="preserved",
                )
                return {"status": "preserved", "sha256": on_disk_sha}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        log.debug(
            "opencode_explorer_agent_installed",
            path=str(target),
            size_bytes=len(content_bytes),
            outcome="written",
        )
        return {"status": "written", "sha256": new_sha}

    except Exception as exc:
        log.debug(
            "opencode_explorer_agent_error",
            error=str(exc),
            outcome="error",
        )
        return {"status": "error", "sha256": new_sha, "error": str(exc)}
