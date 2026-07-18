"""Antigravity CLI-specific bootstrap configuration.

Generates and smart-merges repo-scoped Antigravity CLI artifacts:
- ANTIGRAVITY.md  (repo-wide instructions with TRW ceremony protocol)
- .antigravitycli/settings.json  (MCP server config, JSON deep-merge)
- .antigravitycli/agents/trw-*.md  (TRW role-based subagent definitions)
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from ._file_ops import (
    _new_result,
    _record_write,
    read_settings_for_merge,
    write_agent_templates,
    write_instruction_file_with_merge,
)

logger = structlog.get_logger(__name__)


def _resolve_trw_mcp_command() -> tuple[str, list[str]]:
    """Resolve fully-qualified trw-mcp command and args.

    Returns:
        Tuple of (command, args) for the MCP server entry.
    """
    import shutil
    import sys

    resolved = shutil.which("trw-mcp")
    if resolved is not None:
        return resolved, ["serve"]
    return sys.executable, ["-m", "trw_mcp", "serve"]


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_ANTIGRAVITY_SETTINGS_PATH = ".antigravitycli/settings.json"
_ANTIGRAVITY_AGENTS_DIR = ".antigravitycli/agents"
_ANTIGRAVITY_MD_PATH = "ANTIGRAVITY.md"

# ---------------------------------------------------------------------------
# Marker constants
# ---------------------------------------------------------------------------

_ANTIGRAVITY_TRW_START_MARKER = "<!-- trw:antigravity:start -->"
_ANTIGRAVITY_TRW_END_MARKER = "<!-- trw:antigravity:end -->"

# ---------------------------------------------------------------------------
# Instructions content
# ---------------------------------------------------------------------------


def _antigravity_instructions_content() -> str:
    """Generate ANTIGRAVITY.md TRW ceremony section."""
    from trw_mcp.models.config._client_profile import ClientProfile
    from trw_mcp.state.claude_md._renderer import ProtocolRenderer

    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="antigravity-cli", display_name="antigravity-cli")
    )
    return renderer.render_antigravity_instructions()


# ---------------------------------------------------------------------------
# Public API — Instructions
# ---------------------------------------------------------------------------


def generate_antigravity_instructions(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate or smart-merge ``ANTIGRAVITY.md``."""
    result = _new_result()
    write_instruction_file_with_merge(
        target_path=target_dir / _ANTIGRAVITY_MD_PATH,
        rel_path=_ANTIGRAVITY_MD_PATH,
        trw_section=_antigravity_instructions_content(),
        start_marker=_ANTIGRAVITY_TRW_START_MARKER,
        end_marker=_ANTIGRAVITY_TRW_END_MARKER,
        force=force,
        result=result,
    )
    return result


# ---------------------------------------------------------------------------
# Public API — MCP config
# ---------------------------------------------------------------------------


def generate_antigravity_mcp_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Deep-merge TRW MCP server entry into ``.antigravitycli/settings.json``.

    Only touches ``mcpServers.trw`` — preserves all other settings and servers.
    Hardened (via the shared :func:`read_settings_for_merge` seam) against
    pre-existing user files written by the Antigravity CLI itself or other
    tooling: non-UTF-8 bytes, malformed JSON, or a non-object top level fall
    back to a fresh document rather than crashing or corrupting the file
    silently. The previous file is preserved alongside as ``settings.json.bak``
    so the user can recover their customizations.
    """
    result = _new_result()
    settings_path = target_dir / _ANTIGRAVITY_SETTINGS_PATH
    existed = settings_path.exists()

    existing = read_settings_for_merge(settings_path, rel_path=_ANTIGRAVITY_SETTINGS_PATH, result=result)
    if existing is None:
        # Unrecoverable read error (e.g. permission denied) — already recorded.
        return result

    mcp_servers = existing.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    cmd, args = _resolve_trw_mcp_command()
    trw_entry: dict[str, object] = {
        "command": cmd,
        "args": args,
        "trust": True,
    }

    # Idempotent write
    new_payload = dict(existing)
    new_servers = dict(mcp_servers)
    new_servers["trw"] = trw_entry
    new_payload["mcpServers"] = new_servers
    new_text = json.dumps(new_payload, indent=2) + "\n"

    if existed and not force:
        try:
            current_text = settings_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A corrupt file was recovered above; its bytes can't match the
            # fresh JSON we're about to write, so treat it as a non-match.
            current_text = ""
        if current_text == new_text:
            result.setdefault("preserved", []).append(_ANTIGRAVITY_SETTINGS_PATH)
            return result

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(new_text, encoding="utf-8")
        _record_write(result, _ANTIGRAVITY_SETTINGS_PATH, existed=existed)
    except OSError as exc:
        result["errors"].append(f"Failed to write {settings_path}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Agents — .antigravitycli/agents/*.md with YAML frontmatter
# ---------------------------------------------------------------------------

_ANTIGRAVITY_AGENT_TEMPLATES: dict[str, str] = {
    "trw-explorer.md": """---
name: trw-explorer
description: >
  Read-only codebase explorer for gathering evidence before edits.
  For lightweight code search and file reading. For risk-scored
  exploration with distill intelligence, use @trw-distill-explorer.
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
    "trw-implementer.md": """---
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
    "trw-reviewer.md": """---
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
    "trw-lead.md": """---
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


def generate_antigravity_agents(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate ``.antigravitycli/agents/trw-*.md`` subagent definitions."""
    return write_agent_templates(
        target_dir,
        agents_dir=_ANTIGRAVITY_AGENTS_DIR,
        templates=_ANTIGRAVITY_AGENT_TEMPLATES,
        force=force,
    )
