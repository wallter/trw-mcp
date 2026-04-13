# Parent facade: bootstrap/_utils.py
"""MCP JSON config helpers — extracted from ``_utils.py`` for module-size compliance.

Handles ``.mcp.json`` generation, smart-merge, and pip package reinstall.
The ``_trw_mcp_server_entry`` function stays in ``_utils.py`` because tests
patch ``trw_mcp.bootstrap._utils.shutil`` to control its behavior.
All public names are re-exported from ``_utils.py`` so existing import
paths are preserved.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import structlog

from ._file_ops import ProgressCallback, _result_action_key

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# MCP JSON merge / generation
# ---------------------------------------------------------------------------


def _is_user_customized_trw_entry(existing: object) -> bool:
    """Decide whether to preserve a pre-existing ``trw`` MCP server entry.

    Returns True when the entry shows signs of intentional user customization:
      - ``command`` is an absolute path AND that path exists on disk
        (typical dev-repo pattern: pin to a specific venv's binary)
      - the entry has fields beyond ``command`` and ``args`` (e.g. ``env``,
        ``cwd`` — user has added them)

    Returns False for default-shaped entries (``command="trw-mcp"`` with
    just ``args=["--debug"]``) — those are TRW-managed and safe to refresh.

    Conservative heuristic: when in doubt, prefer preservation over rewrite
    so we never silently break a working dev configuration.
    """
    if not isinstance(existing, dict):
        return False
    cmd = existing.get("command")
    if isinstance(cmd, str) and cmd.startswith("/") and Path(cmd).is_file():
        return True
    # Lists (e.g. [python, -m, trw_mcp.server]) with absolute-path interpreter
    if isinstance(cmd, list) and cmd and isinstance(cmd[0], str) and cmd[0].startswith("/") and Path(cmd[0]).is_file():
        return True
    # Extra keys beyond the canonical {command, args} → user added something
    extra_keys = set(existing.keys()) - {"command", "args"}
    return bool(extra_keys)


def _merge_mcp_json(
    target_dir: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Ensure ``.mcp.json`` has the ``trw`` server entry.

    Reads existing .mcp.json, merges the ``trw`` key into ``mcpServers``
    while preserving all other user-configured servers, and writes back.
    Creates the file from scratch if it doesn't exist.

    User-customized ``trw`` entries are preserved (PRD-FIX-076 follow-up):
    if the existing ``command`` is an absolute path to an extant file, or
    the entry has fields beyond ``command`` and ``args``, the entry is left
    alone. This matches the dev-repo pattern where the user pins
    ``command`` to a specific venv binary (e.g.
    ``/path/to/repo/trw-mcp/.venv/bin/trw-mcp``) so the right interpreter is
    always used regardless of PATH ordering.

    Always generates stdio format entries (PRD-CORE-070-FR04). HTTP
    transport is handled internally by the server's auto-start + proxy.
    """
    # Deferred import to avoid circular dependency with _utils.py
    from ._utils import _trw_mcp_server_entry

    mcp_path = target_dir / ".mcp.json"
    trw_entry = _trw_mcp_server_entry()

    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            servers = {}
        existed = "trw" in servers
        existing_entry = servers.get("trw")
        if existed and _is_user_customized_trw_entry(existing_entry):
            # Preserve the user's pinned/customized entry — log and return.
            # Always use the explicit "preserved" key (not _result_action_key)
            # so the dispatcher classifies the action as preservation, not
            # an update. Falls back to "updated" only if "preserved" is
            # absent from the result dict (legacy callers).
            preservation_key = "preserved" if "preserved" in result else _result_action_key(result)
            result[preservation_key].append(f"{mcp_path} (preserved user-customized trw entry)")
            if on_progress:
                on_progress("Preserved", str(mcp_path))
            logger.info(
                "mcp_config_preserved",
                reason="user_customized_command",
                tool="trw",
                config_path=str(mcp_path),
                existing_command=existing_entry.get("command") if isinstance(existing_entry, dict) else None,
            )
            return
        servers["trw"] = trw_entry
        data["mcpServers"] = servers
        try:
            mcp_path.write_text(
                json.dumps(data, indent=2) + "\n",
                encoding="utf-8",
            )
            key = _result_action_key(result)
            if existed:
                result[key].append(str(mcp_path))
                if on_progress:
                    on_progress("Updated", str(mcp_path))
                logger.info(
                    "mcp_config_updated",
                    reason="default_entry_refreshed",
                    tool="trw",
                    config_path=str(mcp_path),
                )
            else:
                result[key].append(f"{mcp_path} (added trw entry)")
                if on_progress:
                    on_progress("Created", str(mcp_path))
                logger.info(
                    "mcp_config_updated",
                    reason="entry_added",
                    tool="trw",
                    config_path=str(mcp_path),
                )
        except OSError as exc:
            logger.warning("mcp_config_merge_failed", error=str(exc), path=str(mcp_path))
            result["errors"].append(f"Failed to write {mcp_path}: {exc}")
            if on_progress:
                on_progress("Error", str(mcp_path))
    else:
        content = (
            json.dumps(
                {"mcpServers": {"trw": trw_entry}},
                indent=2,
            )
            + "\n"
        )
        try:
            mcp_path.write_text(content, encoding="utf-8")
            result["created"].append(str(mcp_path))
            if on_progress:
                on_progress("Created", str(mcp_path))
            logger.info("mcp_config_updated", tool="trw", config_path=str(mcp_path))
        except OSError as exc:
            logger.warning("mcp_config_merge_failed", error=str(exc), path=str(mcp_path))
            result["errors"].append(f"Failed to write {mcp_path}: {exc}")
            if on_progress:
                on_progress("Error", str(mcp_path))


def _generate_mcp_json() -> str:
    """Generate ``.mcp.json`` pointing to installed trw-mcp.

    Legacy helper kept for backward compatibility. New code uses
    ``_merge_mcp_json()`` which does smart merging.
    """
    # Deferred import to avoid circular dependency with _utils.py
    from ._utils import _trw_mcp_server_entry

    entry = _trw_mcp_server_entry()
    return json.dumps({"mcpServers": {"trw": entry}}, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Package reinstall helper
# ---------------------------------------------------------------------------


def _pip_install_package(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Reinstall trw-mcp package from the source tree.

    Uses the trw-mcp directory that contains the bundled data, ensuring
    the installed package matches the source version.
    """
    # Look up _DATA_DIR through the package module so that
    # patch("trw_mcp.bootstrap._DATA_DIR", ...) in tests works.
    _data_dir = sys.modules["trw_mcp.bootstrap"]._DATA_DIR

    # The package source is the parent of the data directory
    # _data_dir = .../trw-mcp/src/trw_mcp/data -> .parent x3 = trw-mcp/
    package_dir = _data_dir.parent.parent.parent
    if not (package_dir / "pyproject.toml").exists():
        result["errors"].append(
            "Cannot find trw-mcp pyproject.toml for pip install. Manually run: pip install -e /path/to/trw-mcp[dev]"
        )
        return

    try:
        proc = subprocess.run(  # noqa: S603 -- shell=False (default); cmd uses sys.executable (fully-qualified) and a static package_dir path
            [sys.executable, "-m", "pip", "install", "-e", f"{package_dir}[dev]"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode == 0:
            result["updated"].append("pip install trw-mcp (reinstalled)")
        else:
            result["errors"].append(f"pip install failed (exit {proc.returncode}): {proc.stderr[:200]}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        result["errors"].append(f"pip install failed: {exc}")
