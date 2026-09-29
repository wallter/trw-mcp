"""The ``shared_mcp`` doctor row: env health plus whether ``.mcp.json`` launches the shared proxy.

Belongs to the ``shared_server`` package; split out of ``_ops`` (which keeps the operator verbs swap, env
create and status) so each module stays under the 350 effective-LOC ratchet.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server._ops import _memory_dir
from trw_mcp.shared_server._records import (
    STABLE,
    SharedPaths,
    SharedServerError,
    env_python,
    read_live_record,
)


def doctor_row(paths: SharedPaths, config: SharedMcpConfig) -> tuple[Literal["SKIP", "PASS", "WARN"], str]:
    """(``SKIP``|``PASS``|``WARN``, message): the ``shared_mcp`` row of ``trw-mcp doctor``."""
    if not config.enabled:
        return "SKIP", "shared trw-mcp off (opt-in: shared_mcp.enabled: true in .trw/config.yaml)"
    problems = []
    for env in paths.envs():
        try:
            env_python(paths, env)
            if env != STABLE and not _memory_dir(paths, env).is_dir():
                problems.append(f"{env}: no memory dir (run `trw-mcp env create {env}`)")
        except SharedServerError as exc:
            problems.append(str(exc))
    if paths.token.exists() and paths.token.stat().st_mode & 0o077:
        problems.append(f"{paths.token} is readable by other users; chmod 600 it")
    if problems:
        return "WARN", "; ".join(problems)
    serving = [env for env in paths.envs() if read_live_record(paths, env) is not None]
    return "PASS", f"shared trw-mcp on; serving envs: {', '.join(serving) or 'none (each starts on first use)'}"


def check_shared_mcp(target: Path, config: Any) -> Any:
    """Doctor-registry entry point (imported by name into ``_subcommands_doctor``'s globals)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    paths = SharedPaths.resolve(target / config.trw_dir, config.shared_mcp)
    status, message = doctor_row(paths, config.shared_mcp)
    if config.shared_mcp.enabled:
        launch_problem = _mcpjson_launch_problem(target)
        if launch_problem:
            status, message = "WARN", launch_problem if status != "WARN" else f"{message}; {launch_problem}"
    return CheckResult("shared_mcp", status, message)


_PROXY_BIN = "trw-mcp-proxy"
_PROXY_MODULE = "trw_mcp.shared_server"
_FIX = "edit it to run `trw-mcp-proxy`, or re-run `trw-mcp update-project` on a clean tree"


def _basename(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1]
    return name.removesuffix(".exe")


def _launches_proxy(command: str, args: list[str]) -> bool:
    """True when the server entry runs the proxy binary or ``-m trw_mcp.shared_server`` (token match, no substrings)."""
    if _basename(command) == _PROXY_BIN or any(_basename(a) == _PROXY_BIN for a in args):
        return True
    return any(a == "-m" and b == _PROXY_MODULE for a, b in pairwise(args))


def _mcpjson_launch_problem(target: Path) -> str:
    """Why ``.mcp.json`` does not launch the shared proxy, or ``""`` when it does."""
    path = target / ".mcp.json"
    if not path.is_file():
        return "`.mcp.json` is missing (create it to run `trw-mcp-proxy`, or run `trw-mcp update-project`)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "`.mcp.json` is unparseable (fix the JSON, then run `trw-mcp-proxy`)"
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return "`.mcp.json` has no `mcpServers` object (add a trw server that runs `trw-mcp-proxy`)"
    candidates = {k: v for k, v in servers.items() if isinstance(v, dict) and (k == "trw" or "trw" in k.lower())}
    if not candidates:
        return "`.mcp.json` has no trw server entry (add one that runs `trw-mcp-proxy`)"
    for entry in candidates.values():
        command, args = entry.get("command"), entry.get("args", [])
        if not isinstance(command, str) or not isinstance(args, list):
            continue
        if all(isinstance(a, str) for a in args) and _launches_proxy(command, args):
            return ""
    keys = ", ".join(f"`{k}`" for k in ("trw",) if k in candidates) or ", ".join(f"`{k}`" for k in candidates)
    return f"`.mcp.json` launches stdio, not the proxy (server {keys}; {_FIX})"
