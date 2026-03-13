"""Cursor IDE-specific bootstrap configuration.

FR05: Cursor Hook Adapter (PRD-CORE-074)
FR06: Cursor Rules Generation (PRD-CORE-074)
FR07: Cursor MCP Configuration (PRD-CORE-074)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_trw_mcp_entry_cursor() -> dict[str, Any]:
    """Return TRW MCP server entry for Cursor's mcp.json format.

    Uses the installed ``trw-mcp`` binary when available; falls back to
    the current Python interpreter invoking the module directly.
    """
    if shutil.which("trw-mcp"):
        command: str | list[str] = "trw-mcp"
    else:
        command = [sys.executable, "-m", "trw_mcp.server"]
    return {"command": command, "args": ["--debug"]}


def _write_fresh_mcp(path: Path, trw_entry: dict[str, Any]) -> None:
    """Write a fresh .cursor/mcp.json with only the TRW server entry."""
    config: dict[str, Any] = {"mcpServers": {"trw": trw_entry}}
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# FR05: Cursor Hook Adapter
# ---------------------------------------------------------------------------


def generate_cursor_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate .cursor/hooks.json with TRW hook configurations (FR05).

    Cursor supports hook events for IDE lifecycle integration. TRW uses 4:
    - beforeMCPExecution: Advisory tool ordering enforcement (non-blocking)
    - beforeSubmitPrompt: Phase-aware protocol injection
    - afterFileEdit: File modification tracking reminder
    - stop: Advisory ceremony warning (non-blocking)

    If the file already exists and ``force`` is False, performs a smart merge:
    preserves user hooks while adding/replacing TRW hooks (identified by
    description prefix "TRW").

    Returns dict with 'created'/'updated'/'preserved' lists.
    """
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
    hooks_dir = target_dir / ".cursor"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = hooks_dir / "hooks.json"

    trw_hooks: list[dict[str, str]] = [
        {
            "event": "beforeMCPExecution",
            "command": "bash -c 'echo \"TRW: Ensure trw_session_start() is called before other trw_* tools for full context.\"'",
            "description": "TRW MCP tool ordering advisory",
        },
        {
            "event": "beforeSubmitPrompt",
            "command": "bash -c 'echo \"TRW: Load prior learnings with trw_session_start() for full context.\"'",
            "description": "TRW ceremony protocol injection",
        },
        {
            "event": "afterFileEdit",
            "command": "bash -c 'echo \"TRW: File modified — checkpoint saves progress.\"'",
            "description": "TRW file modification tracking",
        },
        {
            "event": "stop",
            "command": "bash -c 'echo \"TRW: Call trw_deliver() to persist learnings for future sessions.\"'",
            "description": "TRW delivery reminder (advisory)",
        },
    ]

    if hooks_file.exists() and not force:
        # Smart merge: preserve user hooks, add/replace TRW hooks
        try:
            existing: dict[str, Any] = json.loads(
                hooks_file.read_text(encoding="utf-8")
            )
            existing_hooks: list[dict[str, Any]] = existing.get("hooks", [])
            # Remove existing TRW hooks (identified by description prefix)
            non_trw = [
                h for h in existing_hooks
                if not h.get("description", "").startswith("TRW")
            ]
            existing["hooks"] = non_trw + trw_hooks
            hooks_file.write_text(
                json.dumps(existing, indent=2) + "\n", encoding="utf-8"
            )
        except (json.JSONDecodeError, KeyError):
            # Malformed JSON — overwrite with fresh config
            hooks_file.write_text(
                json.dumps({"hooks": trw_hooks}, indent=2) + "\n", encoding="utf-8"
            )
        result["updated"].append(".cursor/hooks.json")
    else:
        hooks_file.write_text(
            json.dumps({"hooks": trw_hooks}, indent=2) + "\n", encoding="utf-8"
        )
        result["created"].append(".cursor/hooks.json")

    logger.debug(
        "generate_cursor_hooks",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# FR06: Cursor Rules Generation
# ---------------------------------------------------------------------------


def generate_cursor_rules(
    target_dir: Path,
    trw_section: str,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate .cursor/rules/trw-ceremony.mdc with TRW ceremony instructions (FR06).

    Creates an alwaysApply rule file so TRW ceremony instructions are always
    loaded in Cursor sessions.  The file uses the ``.mdc`` format expected by
    Cursor's rule system.

    The generated file is always written (no smart-merge needed for rules files).
    When ``force`` is False and the file already exists, the result still
    reports "updated" since rule content is refreshed on every call.

    Args:
        target_dir: Root of the target git repository.
        trw_section: Content to embed between the frontmatter and end of file.
            Typically extracted from the TRW CLAUDE.md block.
        force: When True, overwrite unconditionally (same as default behaviour
            for rules files).

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
    rules_dir = target_dir / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rules_file = rules_dir / "trw-ceremony.mdc"

    content = (
        "---\n"
        'description: "TRW ceremony enforcement — ensures learnings persist across sessions"\n'
        "globs: []\n"
        "alwaysApply: true\n"
        "---\n\n"
        f"{trw_section}\n"
    )

    existed = rules_file.exists()
    rules_file.write_text(content, encoding="utf-8")

    if existed and not force:
        result["updated"].append(".cursor/rules/trw-ceremony.mdc")
    else:
        result["created"].append(".cursor/rules/trw-ceremony.mdc")

    logger.debug(
        "generate_cursor_rules",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# FR07: Cursor MCP Configuration
# ---------------------------------------------------------------------------


def generate_cursor_mcp_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate .cursor/mcp.json with TRW MCP server entry (FR07).

    Smart merges with an existing config — preserves the user's other MCP
    servers while ensuring the ``trw`` entry is present and up-to-date.

    Args:
        target_dir: Root of the target git repository.
        force: When True, overwrite the file unconditionally.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
    cursor_dir = target_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = cursor_dir / "mcp.json"

    trw_entry = _get_trw_mcp_entry_cursor()

    if mcp_file.exists() and not force:
        # Smart merge: update only the trw key, preserve everything else
        try:
            existing: dict[str, Any] = json.loads(
                mcp_file.read_text(encoding="utf-8")
            )
            servers: dict[str, Any] = existing.get("mcpServers", {})
            servers["trw"] = trw_entry
            existing["mcpServers"] = servers
            mcp_file.write_text(
                json.dumps(existing, indent=2) + "\n", encoding="utf-8"
            )
        except (json.JSONDecodeError, KeyError):
            # Malformed JSON — write fresh config
            _write_fresh_mcp(mcp_file, trw_entry)
        result["updated"].append(".cursor/mcp.json")
    else:
        _write_fresh_mcp(mcp_file, trw_entry)
        result["created"].append(".cursor/mcp.json")

    logger.debug(
        "generate_cursor_mcp_config",
        created=result["created"],
        updated=result["updated"],
    )
    return result
