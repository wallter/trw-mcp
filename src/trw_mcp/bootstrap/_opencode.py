"""OpenCode-specific bootstrap configuration.

FR11: OpenCode Bootstrap Configuration (PRD-CORE-074)
FR16: opencode.json Smart Merge (PRD-CORE-074)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts._opencode import (
    OpencodeConfig,
    OpencodeServerEntry,
    OpencodeTemplateDict,
)

logger = structlog.get_logger(__name__)

_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"

_DEFAULT_PERMISSIONS: dict[str, str] = {
    "bash": "ask",
    "write": "ask",
    "edit": "ask",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_trw_mcp_entry() -> OpencodeServerEntry:
    """Return the TRW MCP server entry for opencode.json.

    Uses local stdio transport (one trw-mcp process per instance).
    Falls back to absolute Python path if trw-mcp not on PATH.
    """
    if shutil.which("trw-mcp"):
        command: list[str] = ["trw-mcp"]
    else:
        command = [sys.executable, "-m", "trw_mcp.server"]

    return {
        "type": "local",
        "command": command,
        "args": ["--debug"],
        "enabled": True,
    }


def _parse_jsonc(content: str) -> OpencodeConfig:
    """Parse JSONC (JSON with comments) by stripping comments.

    Handles // line comments and /* block comments */.
    Returns parsed dict. Raises json.JSONDecodeError on invalid JSON.
    """
    # Remove block comments /* ... */ (including multi-line)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    # Remove line comments // ... (but not inside strings)
    # Simple approach: remove // comments that are on their own segment
    # Uses a regex that skips strings
    result_parts: list[str] = []
    i = 0
    in_string = False
    escape_next = False
    while i < len(content):
        ch = content[i]
        if escape_next:
            result_parts.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            result_parts.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result_parts.append(ch)
            i += 1
            continue
        if not in_string and ch == "/" and i + 1 < len(content) and content[i + 1] == "/":
            # Skip to end of line
            while i < len(content) and content[i] != "\n":
                i += 1
            continue
        result_parts.append(ch)
        i += 1
    stripped = "".join(result_parts)
    result: OpencodeConfig = json.loads(stripped)
    return result


# ---------------------------------------------------------------------------
# Smart merge (FR16)
# ---------------------------------------------------------------------------


def merge_opencode_json(
    existing: OpencodeConfig,
    trw_entry: OpencodeServerEntry,
) -> OpencodeConfig:
    """Smart merge TRW config into existing opencode.json (FR16).

    Rules:
    - Add/update "trw" entry under "mcp" without removing other servers.
    - Add "permission" defaults only if "permission" key doesn't exist.
    - NEVER overwrite user's "model", "small_model", "agent", "instructions".
    - Preserve all other keys.
    """
    # Start from existing config via unpacking — preserves all user keys
    # (model, small_model, agent, instructions) without dynamic key access.
    result: OpencodeConfig = {**existing}

    # Update "mcp" section: add/update "trw" key, preserve others
    mcp: dict[str, OpencodeServerEntry] = dict(result.get("mcp", {}))
    mcp["trw"] = trw_entry
    result["mcp"] = mcp

    # Add default permissions only when the key is absent
    if "permission" not in result:
        result["permission"] = dict(_DEFAULT_PERMISSIONS)

    return result


# ---------------------------------------------------------------------------
# opencode.json generation (FR11)
# ---------------------------------------------------------------------------


def generate_opencode_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate opencode.json with TRW MCP server config.

    If opencode.json already exists, performs smart merge (FR16).
    If not, writes full template.

    Returns dict with 'created', 'updated', 'preserved', 'errors' lists.
    """
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }
    config_path = target_dir / "opencode.json"
    trw_entry = _get_trw_mcp_entry()

    if config_path.exists() and not force:
        # Smart merge path (FR16)
        try:
            raw = config_path.read_text(encoding="utf-8")
            existing = _parse_jsonc(raw)
        except (json.JSONDecodeError, OSError) as exc:
            result["errors"].append(f"Failed to read/parse {config_path}: {exc}")
            return result

        merged = merge_opencode_json(existing, trw_entry)
        try:
            config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            result["updated"].append(str(config_path.name))
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path}: {exc}")
    else:
        # Fresh install: write full template
        template: OpencodeTemplateDict = {
            "$schema": "https://opencode.ai/config.json",
            "instructions": ["AGENTS.md"],
            "permission": dict(_DEFAULT_PERMISSIONS),
            "tools": {"trw*": True},
            "mcp": {"trw": trw_entry},
        }
        try:
            config_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
            result["created"].append(str(config_path.name))
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path}: {exc}")

    logger.debug(
        "generate_opencode_config",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# AGENTS.md generation (FR11)
# ---------------------------------------------------------------------------


def generate_agents_md(
    target_dir: Path,
    trw_section: str,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate or update AGENTS.md with TRW auto-generated section.

    Uses same <!-- trw:start --> / <!-- trw:end --> markers as CLAUDE.md.
    If file exists, replaces only the section between markers.
    If not, creates new file with the section.
    """
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }
    agents_md_path = target_dir / "AGENTS.md"

    new_block = f"{_TRW_HEADER}\n{_TRW_START_MARKER}\n{trw_section}\n{_TRW_END_MARKER}\n"

    if agents_md_path.exists() and not force:
        content = agents_md_path.read_text(encoding="utf-8")
        start_idx = content.find(_TRW_START_MARKER)
        end_idx = content.find(_TRW_END_MARKER)

        if start_idx != -1 and end_idx != -1:
            # Replace existing TRW section, preserve surrounding content
            end_pos = end_idx + len(_TRW_END_MARKER)
            # Capture optional header line before trw:start
            header_idx = content.rfind(_TRW_HEADER, 0, start_idx)
            replace_start = header_idx if header_idx != -1 else start_idx
            updated = content[:replace_start] + new_block + content[end_pos:]
            try:
                agents_md_path.write_text(updated, encoding="utf-8")
                result["updated"].append(str(agents_md_path.name))
            except OSError as exc:
                result["errors"].append(f"Failed to update {agents_md_path}: {exc}")
        elif start_idx == -1:
            # No TRW section yet — append it
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + new_block
            try:
                agents_md_path.write_text(content, encoding="utf-8")
                result["updated"].append(str(agents_md_path.name))
            except OSError as exc:
                result["errors"].append(f"Failed to update {agents_md_path}: {exc}")
        else:
            result["errors"].append("AGENTS.md has malformed TRW markers — found start but not end")
    else:
        # Create new file
        try:
            agents_md_path.write_text(new_block, encoding="utf-8")
            result["created"].append(str(agents_md_path.name))
        except OSError as exc:
            result["errors"].append(f"Failed to write {agents_md_path}: {exc}")

    logger.debug(
        "generate_agents_md",
        created=result["created"],
        updated=result["updated"],
    )
    return result
