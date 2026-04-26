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

from ._file_ops import (
    _new_result,
    _record_write,
    smart_merge_marker_section,
    write_instruction_file_with_merge,
)

logger = structlog.get_logger(__name__)


def _resolve_trw_mcp_command() -> tuple[str, list[str]]:
    """Resolve fully-qualified trw-mcp command and args.

    FR02 (PRD-FIX-072): Use ``shutil.which`` to find an absolute path for
    the ``trw-mcp`` executable. Falls back to ``sys.executable -m trw_mcp``
    when the command is not on PATH.

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
    from trw_mcp.models.config._client_profile import ClientProfile
    from trw_mcp.state.claude_md._renderer import ProtocolRenderer

    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="gemini", display_name="gemini"))
    return renderer.render_gemini_instructions()


# ---------------------------------------------------------------------------
# Smart merge (mirrors _copilot.py pattern)
# ---------------------------------------------------------------------------


def _smart_merge_instructions(existing: str, trw_content: str) -> str:
    """Backward-compatible wrapper around the shared marker-merge helper.

    Kept so external callers / tests targeting this private symbol still work.
    New code should call :func:`smart_merge_marker_section` directly.
    """
    return smart_merge_marker_section(
        existing,
        trw_content,
        start_marker=_GEMINI_TRW_START_MARKER,
        end_marker=_GEMINI_TRW_END_MARKER,
    )


# ---------------------------------------------------------------------------
# Public API — Instructions
# ---------------------------------------------------------------------------


def generate_gemini_instructions(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate or smart-merge ``GEMINI.md``.

    Delegates to the shared ``write_instruction_file_with_merge`` helper so
    the merge / idempotency / error-handling contract is identical across
    every per-client instruction file generator.
    """
    result = _new_result()
    write_instruction_file_with_merge(
        target_path=target_dir / _GEMINI_MD_PATH,
        rel_path=_GEMINI_MD_PATH,
        trw_section=_gemini_instructions_content(),
        start_marker=_GEMINI_TRW_START_MARKER,
        end_marker=_GEMINI_TRW_END_MARKER,
        force=force,
        result=result,
    )
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
    Hardened against pre-existing user files written by the Gemini CLI itself
    or other tooling: malformed JSON or schema-incompatible top-level types
    fall back to a fresh document rather than corrupting the file silently.
    The previous file is preserved alongside as ``settings.json.bak`` when
    the parsed root is not a JSON object so the user can recover their
    customizations.
    """
    result = _new_result()
    settings_path = target_dir / _GEMINI_SETTINGS_PATH
    existed = settings_path.exists()

    existing: dict[str, object] = {}
    if existed:
        try:
            raw = settings_path.read_text(encoding="utf-8")
        except OSError as exc:
            result["errors"].append(f"Failed to read {_GEMINI_SETTINGS_PATH}: {exc}")
            return result

        if raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                # Preserve user content so they can recover, then start fresh.
                backup = settings_path.with_suffix(settings_path.suffix + ".bak")
                try:
                    backup.write_text(raw, encoding="utf-8")
                except OSError:
                    pass
                result.setdefault("warnings", []).append(
                    f"{_GEMINI_SETTINGS_PATH} was not valid JSON ({exc.msg}); "
                    f"backed up to {backup.name} and rewriting from scratch"
                )
                parsed = None
            else:
                if not isinstance(parsed, dict):
                    backup = settings_path.with_suffix(settings_path.suffix + ".bak")
                    try:
                        backup.write_text(raw, encoding="utf-8")
                    except OSError:
                        pass
                    result.setdefault("warnings", []).append(
                        f"{_GEMINI_SETTINGS_PATH} top-level was not a JSON object; "
                        f"backed up to {backup.name} and rewriting from scratch"
                    )
                    parsed = None

            if isinstance(parsed, dict):
                existing = parsed

    mcp_servers = existing.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        # User had ``mcpServers`` set to a non-object (list, string, null);
        # don't propagate the bad type — replace with a fresh dict. Other
        # mcpServers entries are lost in this corner case but the alternative
        # is crashing on a write that would produce an invalid Gemini config.
        mcp_servers = {}
    existing["mcpServers"] = mcp_servers

    cmd, args = _resolve_trw_mcp_command()
    trw_entry: dict[str, object] = {
        "command": cmd,
        "args": args,
        "trust": True,
    }

    # Idempotent write: if the document on disk already matches what we'd
    # produce, skip the write so callers can report ``preserved`` cleanly.
    new_payload = dict(existing)
    new_servers = dict(mcp_servers)
    new_servers["trw"] = trw_entry
    new_payload["mcpServers"] = new_servers
    new_text = json.dumps(new_payload, indent=2) + "\n"

    if existed and not force:
        try:
            current_text = settings_path.read_text(encoding="utf-8")
        except OSError:
            current_text = ""
        if current_text == new_text:
            result.setdefault("preserved", []).append(_GEMINI_SETTINGS_PATH)
            return result

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(new_text, encoding="utf-8")
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
