"""opencode custom slash command installer.

Installs three ``.opencode/commands/*.md`` files at ``init-project`` and
``update-project`` time:

- ``trw-before-edit.md``     — calls ``trw_before_edit_hint``
- ``trw-distill-hotspots.md`` — calls ``trw_codebase_risk_report``
- ``trw-distill-conventions.md`` — calls ``trw_recall`` (single call — P2-09)

Each file is capped at 4096 bytes (FR15 / NFR05).

User-modified files are detected via SHA-256 and preserved (FR14).

PRD-DIST-2403 FR10-FR15.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from trw_mcp.channels._telemetry import append_channel_event

log = structlog.get_logger(__name__)

__all__ = [
    "COMMANDS_DIR",
    "COMMAND_QUOTA_BYTES",
    "get_before_edit_content",
    "get_conventions_content",
    "get_hotspots_content",
    "install_custom_commands",
]

COMMANDS_DIR = ".opencode/commands"
COMMAND_QUOTA_BYTES = 4096

_TRUNCATION_FOOTER = "\n\n[...truncated — run trw-distill to regenerate full report]"

# ---------------------------------------------------------------------------
# Command template builders
# ---------------------------------------------------------------------------

_BEFORE_EDIT_CONTENT = """\
---
name: trw-before-edit
description: "Get trw-distill risk intelligence for a file before editing it. Usage: /trw-before-edit <file-path>"
---

# TRW Before-Edit Intelligence

Call `trw_before_edit_hint(file_path="$1")` via MCP and parse the result.

## Result States

When `distill_status == "hint_available"`:
- Surface all five hint fields:
  - **importers**: files that import `$1`
  - **inferred_tests**: likely test files for `$1`
  - **hotspot_warnings**: risk warnings for `$1`
  - **risk_score**: composite risk score (0.0–1.0)
  - **co_change_neighbors**: files that frequently change alongside `$1`
- Also surface the top-3 **learnings** from the result.
- If `risk_score > 0.7`, issue an explicit warning: ⚠️ HIGH RISK FILE

When `distill_status == "sidecar_missing"`:
- Inform the user that no sidecar is available.
- Suggest: `trw-distill self-improve risk-report --repo . --persist-sidecar`

When `distill_status == "stale_sha"`:
- Surface last-known data with a staleness notice.
- Suggest regenerating the sidecar.

When `distill_status == "tier_required"`:
- Note the tier gate and return any available learnings from `trw_recall`.

## Important

This command is **advisory only** — it MUST NOT block the edit. Surface the
intelligence and let the operator decide how to proceed.
"""

_HOTSPOTS_CONTENT = """\
---
name: trw-distill-hotspots
description: "Show the top-20 highest-risk files in this project by composite score."
---

# TRW Distill Hotspots

Call `trw_codebase_risk_report(top_n=20)` via MCP.

Format the results as a ranked Markdown table:

| # | File | Score | Fanin | Churn | Untested |
|---|------|-------|-------|-------|----------|

- Label files with `composite_score > 0.8` as **[HIGH RISK]** with a suggestion
  to run `/trw-before-edit <path>`.
- End with a one-line summary: `N files analyzed, M high-risk (>0.8).`
"""

_CONVENTIONS_CONTENT = """\
---
name: trw-distill-conventions
description: "Surface project conventions, patterns, and known gotchas from trw memory."
---

# TRW Distill Conventions

Call `trw_recall(query="project conventions patterns style rules gotcha error edge case warning")` **exactly once** via MCP.

Group results by tag in the response:
- **Conventions**: coding style, API patterns, architecture rules
- **Gotchas**: known pitfalls, error-prone areas
- **Edge Cases**: boundary conditions to watch

Return a structured Markdown summary.
"""


def get_before_edit_content() -> str:
    """Return the trw-before-edit command content."""
    return _apply_quota(_BEFORE_EDIT_CONTENT)


def get_hotspots_content() -> str:
    """Return the trw-distill-hotspots command content."""
    return _apply_quota(_HOTSPOTS_CONTENT)


def get_conventions_content() -> str:
    """Return the trw-distill-conventions command content."""
    return _apply_quota(_CONVENTIONS_CONTENT)


def _apply_quota(content: str) -> str:
    """Truncate *content* to COMMAND_QUOTA_BYTES if needed (FR15)."""
    encoded = content.encode("utf-8")
    if len(encoded) <= COMMAND_QUOTA_BYTES:
        return content
    # Truncate with footer
    footer_bytes = _TRUNCATION_FOOTER.encode("utf-8")
    max_body = COMMAND_QUOTA_BYTES - len(footer_bytes)
    truncated = encoded[:max_body].decode("utf-8", errors="ignore")
    return truncated + _TRUNCATION_FOOTER


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

_COMMANDS: dict[str, tuple[str, str]] = {
    "trw-before-edit.md": (
        "opencode-custom-cmd-before-edit",
        _BEFORE_EDIT_CONTENT,
    ),
    "trw-distill-hotspots.md": (
        "opencode-custom-cmd-hotspots",
        _HOTSPOTS_CONTENT,
    ),
    "trw-distill-conventions.md": (
        "opencode-custom-cmd-conventions",
        _CONVENTIONS_CONTENT,
    ),
}


def install_custom_commands(
    repo_root: Path,
    *,
    existing_hashes: dict[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    """Write all three custom command files under ``.opencode/commands/``.

    Detects user-modified files via SHA-256 (FR14). Modified files are
    preserved and a ``user_modified`` event is emitted to channel-events.jsonl.

    Args:
        repo_root: Repository root directory.
        existing_hashes: Mapping of ``filename → SHA-256`` from
            ``.trw/managed-artifacts.yaml``, or None for first install.

    Returns:
        Mapping of ``filename → {status, sha256}`` for each command file.
    """
    results: dict[str, dict[str, object]] = {}
    hashes = existing_hashes or {}

    for filename, (channel_id, content) in _COMMANDS.items():
        target = repo_root / COMMANDS_DIR / filename
        final_content = _apply_quota(content)
        content_bytes = final_content.encode("utf-8")
        new_sha = hashlib.sha256(content_bytes).hexdigest()

        try:
            # User-edit detection (FR14)
            if filename in hashes and target.exists():
                on_disk_sha = hashlib.sha256(target.read_bytes()).hexdigest()
                if on_disk_sha != hashes[filename]:
                    log.debug(
                        "opencode_custom_command_user_modified",
                        filename=filename,
                        channel_id=channel_id,
                        outcome="preserved",
                    )
                    try:
                        append_channel_event(
                            channel_id=channel_id,
                            client="opencode",
                            event_type="user_modified",
                            tier=None,
                            extra={"filename": filename},
                        )
                    except Exception:
                        pass
                    results[filename] = {"status": "preserved", "sha256": on_disk_sha}
                    continue

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(final_content, encoding="utf-8")

            log.debug(
                "opencode_custom_command_installed",
                filename=filename,
                size_bytes=len(content_bytes),
                outcome="written",
            )
            results[filename] = {"status": "written", "sha256": new_sha}

        except Exception as exc:
            log.debug(
                "opencode_custom_command_error",
                filename=filename,
                error=str(exc),
                outcome="error",
            )
            results[filename] = {"status": "error", "sha256": new_sha, "error": str(exc)}

    return results
