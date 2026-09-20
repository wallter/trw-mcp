"""Grok Build CLI bootstrap: project ``.grok/config.toml`` MCP merge.

Project Grok config may only contribute ``[mcp_servers]``, ``[plugins]``,
``[permission]``, and ``[mcp] max_output_bytes`` (vendor config-reference).
This module writes only ``[mcp_servers.trw]`` via
:func:`resolve_trw_mcp_launcher` (N17 / PRD-SEC-006): project-relative
``.venv/bin/trw-mcp`` when present, never a machine-absolute path, never a
bare PATH ``trw-mcp`` when the venv exists.

User-owned keys on that server table (``env``, ``cwd``, timeouts) and every
other top-level table survive merge. Uninstall reuses the ``codex-toml``
stripper (``[mcp_servers.trw]``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.bootstrap._codex_toml import _toml_dumps
from trw_mcp.bootstrap._file_ops import _new_result, _record_write
from trw_mcp.bootstrap._utils import resolve_trw_mcp_launcher
from trw_mcp.models.typed_dicts import BootstrapFileResult

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib

logger = structlog.get_logger(__name__)

GROK_CONFIG_REL = ".grok/config.toml"

#: Keys TRW owns on ``mcp_servers.trw`` and overwrites on every run.
#: ``url`` is included so a leftover HTTP entry cannot sit beside stdio
#: ``command`` (same reason as Codex). ``env`` / ``cwd`` / timeouts stay
#: with the user.
_TRW_MANAGED_SERVER_KEYS: frozenset[str] = frozenset({"command", "args", "url", "enabled"})


def _trw_mcp_server_entry(target_dir: Path) -> dict[str, object]:
    """Build the Grok ``[mcp_servers.trw]`` table from the shared launcher."""
    command, args = resolve_trw_mcp_launcher(target_dir)
    return {"command": command, "args": list(args), "enabled": True}


def merge_grok_config(existing: dict[str, object], *, target_dir: Path) -> dict[str, object]:
    """Merge TRW-owned ``mcp_servers.trw`` keys into an existing Grok config.

    Does not write ``[models]`` or ``[ui]``. Unknown top-level tables (including
    a user ``[permission]`` or other MCP servers) are copied through.
    """
    result = dict(existing)
    raw_servers = result.get("mcp_servers")
    servers: dict[str, object] = dict(raw_servers) if isinstance(raw_servers, dict) else {}
    raw_trw = servers.get("trw")
    existing_trw: dict[str, object] = dict(raw_trw) if isinstance(raw_trw, dict) else {}
    trw = _trw_mcp_server_entry(target_dir)
    for key, value in existing_trw.items():
        if key not in _TRW_MANAGED_SERVER_KEYS:
            trw[key] = value
    servers["trw"] = trw
    result["mcp_servers"] = servers
    return result


def grok_config_names_trw(target_dir: Path) -> bool:
    """True only when ``.grok/config.toml`` actually carries ``[mcp_servers.trw]``.

    NOT a bare ``.grok/`` directory test: grok keeps skills, hooks and its own
    config there, so every grok user has one whether or not TRW is installed, and
    keying on the directory made `update-project` scaffold TRW's grok artifacts
    into projects that never asked for them -- with TRW's own output then serving
    as the next run's evidence. grok's INTEGRATION-GUIDE forbids dir-only
    detection for the same reason. Unreadable or malformed TOML is NOT evidence.
    """
    config = target_dir / ".grok" / "config.toml"
    if not config.is_file():
        return False
    try:
        parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):  # trw-fail-silent-allow: unparseable is not evidence of TRW
        return False
    servers = parsed.get("mcp_servers")
    return isinstance(servers, dict) and isinstance(servers.get("trw"), dict)


def generate_grok_config(target_dir: Path, *, force: bool = False) -> BootstrapFileResult:
    """Create or smart-merge ``.grok/config.toml`` with ``[mcp_servers.trw]``."""
    result: BootstrapFileResult = cast("BootstrapFileResult", _new_result())
    grok_dir = target_dir / ".grok"
    grok_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / GROK_CONFIG_REL
    existed = config_path.exists()
    existing: dict[str, object] = {}
    if existed and not force:
        try:
            existing = dict(tomllib.loads(config_path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            result["errors"].append(f"Failed to read/merge {config_path}: {exc}")
            return result
    elif existed:
        # force rewrites TRW's OWN keys; it is not licence to delete the user's
        # file. Parsing failure used to fall back to ``existing = {}``, so a
        # single typo in [permission] silently truncated every other table --
        # other MCP servers, plugins, permissions -- with no error and no backup.
        try:
            existing = dict(tomllib.loads(config_path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            result["errors"].append(
                f"Failed to read/merge {config_path}: {exc}. Refusing to overwrite a file TRW cannot parse; "
                "fix the TOML (or move it aside) and re-run."
            )
            return result
    try:
        merged = merge_grok_config(existing, target_dir=target_dir)
        # TypeError: _toml_value renders the subset Codex bootstrap needed, so a
        # legitimate TOML value it does not know (a datetime, say) raises rather
        # than emitting bad TOML. Report it against the file instead of letting it
        # escape as an unhandled crash through install/update.
        config_path.write_text(_toml_dumps(merged), encoding="utf-8")
        _record_write(cast("dict[str, list[str]]", result), GROK_CONFIG_REL, existed=existed)
    except (OSError, TypeError) as exc:
        result["errors"].append(f"Failed to write {config_path}: {exc}")
    return result


def generate_grok_agents_md(target_dir: Path, *, force: bool = False) -> dict[str, list[str]]:
    """Write the shared AGENTS.md ceremony block for the grok profile."""
    from trw_mcp.bootstrap._opencode import generate_agents_md
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

    return generate_agents_md(
        target_dir,
        render_agents_trw_section(client_profile=resolve_client_profile("grok")),
        force=force,
        client_id="grok",
    )


def install_grok_artifacts(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    """CLIENT_INTEGRATIONS install callback."""
    try:
        mcp = generate_grok_config(target_dir, force=force)
        result["created"].extend(mcp.get("created", []))
        result["created"].extend(mcp.get("updated", []))
        result.setdefault("preserved", []).extend(mcp.get("preserved", []))
        result["errors"].extend(mcp.get("errors", []))
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"grok MCP config skipped: {exc}")
    try:
        agents = generate_grok_agents_md(target_dir, force=force)
        result["created"].extend(agents.get("created", []))
        result["created"].extend(agents.get("updated", []))
        result.setdefault("preserved", []).extend(agents.get("preserved", []))
        result["errors"].extend(agents.get("errors", []))
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"grok AGENTS.md skipped: {exc}")


def update_grok_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    """CLIENT_INTEGRATIONS update callback."""
    # _update_targets, NOT resolve_ide_targets: the latter falls through to
    # detect_ide, which fires on a bare ``.grok/`` directory -- the very directory
    # THIS function creates. A plain `update-project` in a project that merely has
    # grok installed would scaffold .grok/config.toml and the AGENTS.md block
    # unasked, and its own output would be the next run's "evidence". Every other
    # client's update gates on the recorded target_platforms for this reason.
    from ._ide_targets import _update_targets

    del manifest_hashes
    if "grok" not in _update_targets(target_dir, ide_override):
        return
    try:
        mcp = generate_grok_config(target_dir)
        result["created"].extend(mcp.get("created", []))
        result.setdefault("updated", []).extend(mcp.get("updated", []))
        result.setdefault("preserved", []).extend(mcp.get("preserved", []))
        result["errors"].extend(mcp.get("errors", []))
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"grok MCP config update skipped: {exc}")
    try:
        agents = generate_grok_agents_md(target_dir)
        result["created"].extend(agents.get("created", []))
        result.setdefault("updated", []).extend(agents.get("updated", []))
        result.setdefault("preserved", []).extend(agents.get("preserved", []))
        result["errors"].extend(agents.get("errors", []))
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"grok AGENTS.md update skipped: {exc}")


__all__ = [
    "GROK_CONFIG_REL",
    "generate_grok_agents_md",
    "generate_grok_config",
    "grok_config_names_trw",
    "install_grok_artifacts",
    "merge_grok_config",
    "update_grok_artifacts",
]
