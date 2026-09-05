"""Antigravity CLI-specific bootstrap configuration.

Generates and smart-merges Antigravity CLI artifacts:
- ANTIGRAVITY.md               (repo-scoped instructions with TRW ceremony protocol)
- ~/.gemini/config/mcp_config.json  (MCP server config, GLOBAL — see PRD-FIX-133)
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from ._file_ops import (
    _new_result,
    _record_write,
    read_settings_for_merge,
    write_instruction_file_with_merge,
)

logger = structlog.get_logger(__name__)


def _resolve_trw_mcp_command() -> tuple[str, list[str]]:
    """Resolve the ``trw-mcp`` command and args for the antigravity entry.

    Delegates to the single hardened builder in ``_utils`` rather than carrying
    a sixth hand-copy. The copy this replaced had both defects PRD-SEC-006 fixed
    for the other five clients, and one of its own:

    * PATH hit returned the ABSOLUTE ``shutil.which()`` result, so the committed
      ``.antigravitycli/settings.json`` carried the build machine's binary path
      and was broken for every teammate who cloned the repo;
    * PATH miss returned ``sys.executable`` — the same machine-absolute leak;
    * and its module target was ``-m trw_mcp``, which cannot execute at all.
      There is no ``trw_mcp/__main__.py``, so the entry died with "No module
      named trw_mcp.__main__" and the antigravity MCP server never started.
      Every sibling uses ``-m trw_mcp.server``.

    Returns:
        Tuple of (command, args) for the MCP server entry.
    """
    from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

    entry = _trw_mcp_server_entry()
    command = str(entry["command"])
    args = entry["args"]
    return command, [str(a) for a in args] if isinstance(args, list) else []


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

#: The directory TRW used to write subagents to. Antigravity documents
#: ``.agents/agents`` (the same ``.agents`` tree its workspace rules use), so
#: the bundled specialists now land there via the shared agent installer. This
#: constant survives only so ``update-project`` can sweep the relocated stubs.
_LEGACY_ANTIGRAVITY_AGENTS_DIR = ".antigravitycli/agents"
_ANTIGRAVITY_MD_PATH = "ANTIGRAVITY.md"

#: The workspace-rules folder Antigravity documents. `.agent/rules` (singular)
#: is the legacy name it still supports; new installs get the current one.
#: Source: antigravity.google/docs/rules-workflows
_ANTIGRAVITY_RULES_DIR = ".agents/rules"
_ANTIGRAVITY_RULE_FILENAME = "trw-ceremony.md"

#: "Rules files are limited to 12,000 characters each" (same source). Enforced
#: rather than assumed: over the limit the tail is dropped, and the deliver gate
#: renders last.
_ANTIGRAVITY_RULE_MAX_CHARS = 12_000

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
    """Generate the workspace rule, and smart-merge ``ANTIGRAVITY.md``."""
    result = _new_result()
    rendered = _antigravity_instructions_content()
    _write_antigravity_workspace_rule(target_dir, rendered, result, force=force)
    write_instruction_file_with_merge(
        target_path=target_dir / _ANTIGRAVITY_MD_PATH,
        rel_path=_ANTIGRAVITY_MD_PATH,
        trw_section=rendered,
        start_marker=_ANTIGRAVITY_TRW_START_MARKER,
        end_marker=_ANTIGRAVITY_TRW_END_MARKER,
        force=force,
        result=result,
    )
    return result


def _write_antigravity_workspace_rule(
    target_dir: Path,
    rendered: str,
    result: dict[str, list[str]],
    *,
    force: bool = False,
) -> None:
    """Write the protocol to the path Antigravity DOCUMENTS reading.

    ``ANTIGRAVITY.md`` appears in no Antigravity primary source. Its own
    rules documentation names exactly two locations — ``~/.gemini/GEMINI.md``
    globally and ``.agents/rules/`` per workspace ("Workspace rules live in the
    .agents/rules folder of your workspace or git root", with ``.agent/rules``
    kept for backward compatibility). TRW was writing its protocol to a
    filename the vendor never documents loading, which is the worst outcome
    available: an artifact that exists, reports success, and reaches no model.

    So the rule file is now the carrier. ``ANTIGRAVITY.md`` is still written —
    removing it is a separate call, and if some undocumented path does read it,
    dropping it would cost the protocol. Belt and braces beats a guess in
    either direction.

    This file is TRW-owned, so it is a *generated artifact* rather than
    injection into a file the user authored — the same shape as opencode's and
    codex's dedicated instruction files.

    Antigravity caps rule files at 12,000 characters; the rendered protocol is
    an order of magnitude under that, but the guard is explicit because a
    silent truncation would strip the deliver gate off the end.
    """
    rule_path = target_dir / _ANTIGRAVITY_RULES_DIR / _ANTIGRAVITY_RULE_FILENAME
    rel_path = f"{_ANTIGRAVITY_RULES_DIR}/{_ANTIGRAVITY_RULE_FILENAME}"

    if len(rendered) > _ANTIGRAVITY_RULE_MAX_CHARS:
        result.setdefault("errors", []).append(
            f"{rel_path}: rendered protocol is {len(rendered)} chars, over Antigravity's "
            f"{_ANTIGRAVITY_RULE_MAX_CHARS}-char rule limit — it would be truncated"
        )
        return

    write_instruction_file_with_merge(
        target_path=rule_path,
        rel_path=rel_path,
        trw_section=rendered,
        start_marker=_ANTIGRAVITY_TRW_START_MARKER,
        end_marker=_ANTIGRAVITY_TRW_END_MARKER,
        force=force,
        result=result,
    )


# ---------------------------------------------------------------------------
# Public API — MCP config
# ---------------------------------------------------------------------------

#: Display label used in warnings/errors — deliberately ``~``-relative rather
#: than the resolved absolute path, so messages never leak a machine-specific
#: home directory.
_ANTIGRAVITY_GLOBAL_MCP_DISPLAY = "~/.gemini/config/mcp_config.json"


def _antigravity_global_mcp_config_path() -> Path:
    """Resolve Antigravity CLI's GLOBAL MCP config file (PRD-FIX-133).

    Confirmed 2026-09-04 against the installed ``agy`` 1.1.26 binary two ways:
    its bundled vendor doc (``~/.gemini/antigravity-cli/builtin/skills/
    agy-customizations/docs/mcp_servers.md``) documents exactly two locations
    — a global file applied to every session, or a per-plugin file TRW ships no
    plugin for — and ``agy mcp add`` against a scratch ``HOME`` wrote only this
    path. There is no project-scoped MCP config file for this client at all;
    the previous ``.antigravitycli/settings.json`` write was unread by
    anything in the binary, its docs, or its builtin skills.

    Reads ``$HOME`` via :func:`Path.home`, so tests isolate it with
    ``monkeypatch.setenv("HOME", ...)`` rather than a path parameter — the same
    shape ``_isolate_trw_user_dir`` already uses for ``~/.trw``.
    """
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def generate_antigravity_mcp_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Deep-merge the TRW MCP server entry into Antigravity's GLOBAL config.

    ``target_dir`` is unused — kept so this still structurally matches the
    shared ``_CopilotInstaller`` Protocol every other client bootstrap
    function implements — because the destination is not project-scoped (see
    :func:`_antigravity_global_mcp_config_path`).

    Only touches ``mcpServers.trw`` — preserves all other settings and
    servers. Hardened (via the shared :func:`read_settings_for_merge` seam)
    against pre-existing files written by the Antigravity CLI itself or other
    tooling: non-UTF-8 bytes, malformed JSON, or a non-object top level fall
    back to a fresh document rather than crashing or corrupting the file
    silently. The previous file is preserved alongside as a ``.bak`` sibling.

    Every write appends an explicit ``warnings`` entry naming the file as
    GLOBAL and cross-project — this is state outside the project tree, and a
    bootstrap run must never mutate it silently (PRD-FIX-133-FR03).
    """
    result = _new_result()
    settings_path = _antigravity_global_mcp_config_path()
    existed = settings_path.exists()

    existing = read_settings_for_merge(settings_path, rel_path=_ANTIGRAVITY_GLOBAL_MCP_DISPLAY, result=result)
    if existing is None:
        # Unrecoverable read error (e.g. permission denied) — already recorded.
        return result

    mcp_servers = existing.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    cmd, args = _resolve_trw_mcp_command()
    # No "trust" key: agy's own schema (vendor doc + `agy mcp add` output) is
    # command/args/env/disabled only — a key it does not read is dead weight.
    trw_entry: dict[str, object] = {"command": cmd, "args": args}

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
            result.setdefault("preserved", []).append(_ANTIGRAVITY_GLOBAL_MCP_DISPLAY)
            return result

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(new_text, encoding="utf-8")
        _record_write(result, _ANTIGRAVITY_GLOBAL_MCP_DISPLAY, existed=existed)
        result.setdefault("warnings", []).append(
            f"Antigravity CLI only loads MCP servers from the GLOBAL "
            f"{_ANTIGRAVITY_GLOBAL_MCP_DISPLAY} (shared by every project on this "
            f"machine) — {'updated' if existed else 'created'} the 'trw' entry there."
        )
    except OSError as exc:
        result["errors"].append(f"Failed to write {settings_path}: {exc}")

    return result
