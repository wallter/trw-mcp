"""The trw MCP server entry for the GitHub Copilot CLI: ``.github/mcp.json``.

The Copilot CLI reads project MCP servers from ``.mcp.json`` and ``.github/mcp.json`` and
does NOT read ``.vscode/mcp.json``, which is the only file the copilot install used to
write. CLI sessions therefore had no trw tools. ``.github/mcp.json`` is the Copilot-owned,
committed location, so the install merges ``mcpServers.trw`` there, in the documented
local-server shape (``type: "local"``, ``command``, ``args``, ``tools``), next to any
servers the user already has.

Belongs to the copilot install path (``_copilot_distill_channels.install_copilot_distill_channels``,
which runs on both ``init-project`` and ``update-project``).
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from trw_memory.safe_fs import write_beneath

from ._utils import _PROJECT_VENV_LAUNCHERS, resolve_trw_mcp_launcher

log = structlog.get_logger(__name__)

__all__ = ["COPILOT_CLI_MCP_PATH", "copilot_cli_trw_entry", "generate_copilot_cli_mcp_config"]

#: Repo-relative path the Copilot CLI reads for committed, shared MCP servers.
COPILOT_CLI_MCP_PATH = ".github/mcp.json"


def copilot_cli_trw_entry(target_dir: Path) -> dict[str, object]:
    """The ``mcpServers.trw`` entry TRW writes: the same launcher every client uses (N17).

    The path stays project-relative (no variable prefix): the Copilot CLI starts in the
    project, and the file is committed, so it must never hold a machine-absolute path.
    """
    command, args = resolve_trw_mcp_launcher(target_dir)
    return {"type": "local", "command": command, "args": args, "tools": ["*"]}


def generate_copilot_cli_mcp_config(target_dir: Path, *, force: bool = False) -> dict[str, list[str]]:
    """Merge ``mcpServers.trw`` into ``.github/mcp.json``; every other key is preserved.

    A ``trw`` entry the user changed is kept unless *force*. A file that is not a JSON
    object is never overwritten (that would destroy the user's servers); it is reported
    under ``errors`` instead.
    """
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
    path = target_dir / COPILOT_CLI_MCP_PATH
    data: dict[str, object] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            loaded = exc
        if not isinstance(loaded, dict):
            log.warning("copilot_cli_mcp_unreadable", path=str(path), outcome="left_untouched")
            result["errors"].append(f"{COPILOT_CLI_MCP_PATH}: not a JSON object; left untouched")
            return result
        data = loaded

    raw_servers = data.get("mcpServers")
    servers: dict[str, object] = dict(raw_servers) if isinstance(raw_servers, dict) else {}
    wanted = copilot_cli_trw_entry(target_dir)
    current = servers.get("trw")
    label = f"{COPILOT_CLI_MCP_PATH}:mcpServers.trw"
    if current == wanted and not force:
        result["preserved"].append(label)
        return result
    if current is not None and current != wanted and not force and not _is_trw_managed(current):
        log.warning("copilot_cli_mcp_user_modified", path=str(path), outcome="skip_user_modified")
        result["preserved"].append(f"{label} (user-modified, use force=True to overwrite)")
        return result

    servers["trw"] = wanted
    rendered = json.dumps({**data, "mcpServers": servers}, indent=2, sort_keys=True) + "\n"
    write_beneath(target_dir, COPILOT_CLI_MCP_PATH, rendered.encode("utf-8"), mode=0o644)
    result["created" if current is None else "updated"].append(label)
    log.info("copilot_cli_mcp_written", path=str(path), outcome="created" if current is None else "updated")
    return result


def _is_trw_managed(entry: object) -> bool:
    """True only for an entry TRW itself could have written, compared in full (args included).

    Any launcher ``resolve_trw_mcp_launcher`` can pick counts, so a project that gains or
    loses its venv is refreshed; anything else, such as custom args, is the user's.
    """
    launchers: list[tuple[str, list[str]]] = [("trw-mcp", []), ("python3", ["-m", "trw_mcp.server"])]
    launchers += [(rel, []) for rel in _PROJECT_VENV_LAUNCHERS]
    return any(
        entry == {"type": "local", "command": command, "args": args, "tools": ["*"]} for command, args in launchers
    )
