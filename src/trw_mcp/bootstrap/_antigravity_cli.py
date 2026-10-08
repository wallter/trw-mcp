"""Antigravity CLI-specific bootstrap configuration.

Generates and smart-merges Antigravity CLI artifacts:
- ANTIGRAVITY.md               (repo-scoped instructions with TRW ceremony protocol)
- ~/.gemini/config/mcp_config.json  (MCP server config, GLOBAL — see PRD-FIX-133)
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import NamedTuple

import structlog

from ._file_ops import (
    _new_result,
    _record_write,
    read_settings_for_merge,
    write_instruction_file_with_merge,
)

logger = structlog.get_logger(__name__)


#: Keys TRW owns on ``mcpServers.trw`` and overwrites on every run; ``url`` so a leftover
#: HTTP entry cannot sit beside stdio ``command``. Everything else stays with the user.
_TRW_MANAGED_SERVER_KEYS: frozenset[str] = frozenset({"command", "args", "url"})


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
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Deep-merge the TRW MCP server entry into Antigravity's GLOBAL config.

    ``target_dir`` is unused — kept so this still structurally matches the
    shared ``_CopilotInstaller`` Protocol every other client bootstrap
    function implements — because the destination is not project-scoped (see
    :func:`_antigravity_global_mcp_config_path`).

    Only touches ``mcpServers.trw`` — preserves all other settings and
    servers. Hardened (via the shared :func:`read_settings_for_merge` seam)
    against pre-existing files written by the Antigravity CLI itself or other
    tooling: non-UTF-8 bytes, malformed JSON, or a non-object top level never
    crash it. This file is machine-global, so such a file is NEVER replaced: a
    warning names it and nothing is backed up or written.

    Every write appends an explicit ``warnings`` entry naming the file as
    GLOBAL and cross-project — this is state outside the project tree, and a
    bootstrap run must never mutate it silently (PRD-FIX-133-FR03).
    """
    result = _new_result()
    settings_path = _antigravity_global_mcp_config_path()
    from ._rerender import publish_with_backup, read_optional

    try:
        # Home dotfiles deliberately follow links; all subsequent operations use the
        # resolved target and the usual no-follow, compare-and-publish primitives.
        settings_path = settings_path.resolve()
        home = Path.home().resolve()
        storage_root = home if settings_path.is_relative_to(home) else settings_path.parent
        while True:
            try:
                storage_root.stat()
                break
            except FileNotFoundError:
                storage_root = storage_root.parent
        old = read_optional(storage_root, settings_path.relative_to(storage_root).as_posix())
    except OSError as exc:
        result["errors"].append(f"Failed to read {settings_path}: {exc}")
        return result
    existed = old is not None

    existing = read_settings_for_merge(
        settings_path, rel_path=_ANTIGRAVITY_GLOBAL_MCP_DISPLAY, result=result, recover=False
    )
    if existing is None:
        # Unreadable, or unparseable and shared by every project: recorded, and never replaced.
        return result

    mcp_servers = existing.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        # Shared by every project, and TRW cannot tell what the user meant by it: keep it as it is, like an unparseable file.
        result.setdefault("warnings", []).append(
            f"{_ANTIGRAVITY_GLOBAL_MCP_DISPLAY}: 'mcpServers' is not an object; left untouched "
            "(fix or remove it, then re-run to add the 'trw' entry)"
        )
        return result

    cmd, args = _resolve_trw_mcp_command()
    # No "trust" key: agy's own schema (vendor doc + `agy mcp add` output) is
    # command/args/env/disabled only — a key it does not read is dead weight.
    trw_entry: dict[str, object] = {"command": cmd, "args": args}
    # TRW owns command/args (and a leftover url); the user's env, cwd and disabled flag
    # survive, as they do for codex and grok. This file is machine-global, so dropping a
    # user's env here broke every project on the machine.
    previous = mcp_servers.get("trw")
    if isinstance(previous, dict):
        trw_entry = {**{k: v for k, v in previous.items() if k not in _TRW_MANAGED_SERVER_KEYS}, **trw_entry}

    # Idempotent write
    new_payload = dict(existing)
    new_servers = dict(mcp_servers)
    new_servers["trw"] = trw_entry
    new_payload["mcpServers"] = new_servers
    new_text = json.dumps(new_payload, indent=2) + "\n"

    if old == new_text.encode("utf-8"):
        result.setdefault("preserved", []).append(_ANTIGRAVITY_GLOBAL_MCP_DISPLAY)
        return result
    if dry_run:
        result.setdefault("warnings", []).append(
            f"Would write GLOBAL {_ANTIGRAVITY_GLOBAL_MCP_DISPLAY}; "
            f"existing bytes would be backed up under {storage_root / '.trw/trash'}"
        )
        return result

    try:
        backups: list[Path] = []
        if not publish_with_backup(
            storage_root,
            settings_path.relative_to(storage_root).as_posix(),
            old,
            new_text.encode("utf-8"),
            result,
            backups=backups,
        ):
            return result
        if (writes := _GLOBAL_WRITES.get()) is not None:
            writes.append(
                _GlobalWrite(storage_root, settings_path, new_text.encode("utf-8"), backups[0] if backups else None)
            )
        _record_write(result, _ANTIGRAVITY_GLOBAL_MCP_DISPLAY, existed=existed)
        result.setdefault("warnings", []).append(
            f"Antigravity CLI only loads MCP servers from the GLOBAL "
            f"{_ANTIGRAVITY_GLOBAL_MCP_DISPLAY} (shared by every project on this "
            f"machine) — {'updated' if existed else 'created'} the 'trw' entry there."
        )
    except OSError as exc:
        result["errors"].append(f"Failed to write {settings_path}: {exc}")

    return result


class _GlobalWrite(NamedTuple):
    root: Path
    path: Path
    written: bytes
    backup: Path | None


_GLOBAL_WRITES: ContextVar[list[_GlobalWrite] | None] = ContextVar("antigravity_global_writes", default=None)


@contextmanager
def global_config_transaction(result: dict[str, list[str]]) -> Iterator[None]:
    """Restore global targets from durable backups when the project transaction fails."""
    writes: list[_GlobalWrite] = []
    token = _GLOBAL_WRITES.set(writes)
    completed = False
    try:
        yield
        completed = not result["errors"]
    finally:
        _GLOBAL_WRITES.reset(token)
        if not completed:
            for receipt in reversed(writes):
                _restore_global_config(receipt, result)


def _restore_global_config(receipt: _GlobalWrite, result: dict[str, list[str]]) -> None:
    from ._proven_replace import replace_proven
    from ._trash import remove_if_hash

    root, path, written, backup = receipt
    try:
        if backup is None:
            removed = remove_if_hash(path, root, hashlib.sha256(written).hexdigest())
            restored = removed.status in ("removed", "absent")
            detail = removed.reason
        else:
            outcome = replace_proven(path, root, written, backup.read_bytes(), mode=stat.S_IMODE(backup.stat().st_mode))
            restored, detail = outcome.status == "replaced", outcome.reason
        if restored:
            result["warnings"].append(f"GLOBAL {path}: restored pre-update state after rollback")
        else:
            result["errors"].append(f"GLOBAL {path}: rollback refused ({detail}); recovery backup: {backup}")
    except OSError as exc:
        result["errors"].append(f"GLOBAL {path}: rollback failed ({exc}); recovery backup: {backup}")
