"""C3: .vscode/mcp.json json_key_merge setup for Copilot.

Merges servers.trw into .vscode/mcp.json using the VS Code Copilot Chat
MCP specification. Root key is 'servers' (NOT 'mcpServers').

json.dumps(sort_keys=True) ensures byte-identical output for unchanged
content (FR12, P2-12).

Extracted from _copilot.py (which is already over the 350 effective-LOC gate)
per NFR03.

PRD-DIST-2406.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._telemetry import append_channel_event

log = structlog.get_logger(__name__)

__all__ = [
    "generate_vscode_mcp_config",
]

# ---------------------------------------------------------------------------
# TRW MCP server entry for .vscode/mcp.json
# Root key is 'servers' (VS Code Copilot Chat MCP spec, NOT 'mcpServers')
# ---------------------------------------------------------------------------

_TRW_MCP_SERVER_ENTRY: dict[str, object] = {
    "args": [],
    "command": "trw-mcp",
    "type": "stdio",
}

_VSCODE_MCP_CHANNEL_ID = "copilot-vscode-mcp-config"
_CLIENT = "copilot"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_vscode_mcp_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Write or merge the TRW MCP server entry into .vscode/mcp.json.

    Merges 'servers.trw' key only; preserves all other servers.* keys.
    Uses json.dumps(sort_keys=True) for byte-stable idempotent output (P2-12).
    Creates .vscode/ directory if absent.

    If servers.trw already matches _TRW_MCP_SERVER_ENTRY and force=False,
    returns {"preserved": ["servers.trw"], ...} without writing.
    If user has manually modified servers.trw, logs WARNING and skips
    unless force=True.

    Args:
        target_dir: Repository root or project directory.
        force: Overwrite even if servers.trw was user-modified.

    Returns:
        Dict with keys 'created', 'updated', 'preserved', 'errors'.
        Each value is a list of path strings describing what happened.
    """
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }

    vscode_dir = target_dir / ".vscode"
    mcp_json_path = vscode_dir / "mcp.json"

    # Acquire lock (NFR05)
    lock_path = target_dir / ".trw" / "channels" / "copilot-vscode-mcp-config.lock"
    try:
        lock = ChannelLock(lock_path)
        lock.__enter__()
    except ChannelLockSkip:
        log.debug(
            "copilot_vscode_mcp_lock_skip",
            outcome="skipped_lock",
        )
        result["errors"].append("skipped_lock")
        return result

    try:
        return _generate_under_lock(
            target_dir=target_dir,
            vscode_dir=vscode_dir,
            mcp_json_path=mcp_json_path,
            force=force,
            result=result,
        )
    except Exception as exc:
        log.debug(
            "copilot_vscode_mcp_error",
            error=str(exc),
            outcome="error",
        )
        result["errors"].append(str(exc))
        return result
    finally:
        try:
            lock.__exit__(None, None, None)
        except Exception:
            pass


def _generate_under_lock(
    *,
    target_dir: Path,
    vscode_dir: Path,
    mcp_json_path: Path,
    force: bool,
    result: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Execute the merge logic while the channel lock is held."""
    vscode_dir.mkdir(parents=True, exist_ok=True)

    # Read existing file if present
    if mcp_json_path.exists():
        try:
            existing_text = mcp_json_path.read_text(encoding="utf-8")
            existing_data: dict[str, object] = json.loads(existing_text)
        except (json.JSONDecodeError, OSError):
            existing_data = {}
    else:
        existing_data = {}

    # Get existing servers
    existing_servers = existing_data.get("servers", {})
    servers: dict[str, object] = dict(existing_servers) if isinstance(existing_servers, dict) else {}
    current_trw = servers.get("trw")

    # Check idempotency
    if current_trw is not None:
        if current_trw == _TRW_MCP_SERVER_ENTRY:
            if not force:
                result["preserved"].append("servers.trw")
                log.debug(
                    "copilot_vscode_mcp_idempotent",
                    outcome="preserved",
                )
                return result
        else:
            # User modified servers.trw
            if not force:
                log.warning(
                    "copilot_vscode_mcp_user_modified",
                    current_entry=str(current_trw),
                    outcome="skip_user_modified",
                )
                result["preserved"].append("servers.trw (user-modified, use force=True to overwrite)")
                return result

    # Merge and write
    servers["trw"] = _TRW_MCP_SERVER_ENTRY
    new_data = dict(existing_data)
    new_data["servers"] = servers

    # sort_keys=True for byte-stable output (P2-12)
    new_text = json.dumps(new_data, indent=2, sort_keys=True) + "\n"

    mcp_json_path.write_text(new_text, encoding="utf-8")

    _emit_event("push_write", "written")

    if current_trw is None:
        result["created"].append(".vscode/mcp.json:servers.trw")
        log.debug(
            "copilot_vscode_mcp_created",
            path=str(mcp_json_path),
            outcome="created",
        )
    else:
        result["updated"].append(".vscode/mcp.json:servers.trw")
        log.debug(
            "copilot_vscode_mcp_updated",
            path=str(mcp_json_path),
            outcome="updated",
        )

    return result


def _emit_event(event_type: str, outcome: str) -> None:
    """Fail-open telemetry wrapper (NFR06)."""
    try:
        append_channel_event(
            channel_id=_VSCODE_MCP_CHANNEL_ID,
            client=_CLIENT,
            event_type=event_type,
            extra={"outcome": outcome},
        )
    except Exception:
        pass
