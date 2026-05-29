"""Cursor hooks I/O helpers — extracted from _cursor.py for module-size compliance.

Belongs to the ``_cursor.py`` facade. Re-exported there for back-compat with
``_cursor_ide.py`` (which imports all 3 helpers from the parent).

PRD-CORE-136-FR02: hook-script copy + hooks.json builder + smart-merge JSON
upsert. The 3 helpers form a coherent "Cursor hooks I/O" sub-domain — this
extraction keeps the parent _cursor.py focused on rules / mcp.json /
skills-mirror generation.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.bootstrap._cursor_models import CursorHooksV1Config, HookHandlerEntry
from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

logger = structlog.get_logger(__name__)

# Data directory mirror — kept in sync with the parent _cursor.py constant
# to avoid an import-time circular dep.
_DATA_DIR = Path(__file__).parent.parent / "data"
_CURSOR_HOOKS_DATA_DIR = _DATA_DIR / "hooks" / "cursor"


def generate_cursor_hook_scripts(
    target_dir: Path,
    scripts: list[str],
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Copy bundled hook scripts to .cursor/hooks/ with mode 0755 (PRD-CORE-136-FR02).

    Reads scripts from ``data/hooks/cursor/<name>`` and writes them to
    ``.cursor/hooks/<name>``.  Idempotent on repeat calls.

    Args:
        target_dir: Root of the target git repository.
        scripts: List of script file names (e.g. ["trw-session-start.sh"]).
        force: When True, overwrite existing scripts unconditionally.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    hooks_dest = target_dir / ".cursor" / "hooks"
    hooks_dest.mkdir(parents=True, exist_ok=True)

    for name in scripts:
        src = _CURSOR_HOOKS_DATA_DIR / name
        dst = hooks_dest / name
        if not src.is_file():
            logger.warning("cursor_hook_script_missing", script=name, src=str(src))
            continue

        existed = dst.exists()
        if not existed or force:
            shutil.copy2(str(src), str(dst))
            os.chmod(str(dst), 0o755)  # noqa: S103 -- hook scripts must be executable
            rel = f".cursor/hooks/{name}"
            if existed:
                result["updated"].append(rel)
            else:
                result["created"].append(rel)
        else:
            result["preserved"].append(f".cursor/hooks/{name}")

    logger.debug(
        "generate_cursor_hook_scripts",
        scripts=scripts,
        created=result["created"],
        updated=result["updated"],
    )
    return result


def build_cursor_hook_config(
    events_map: dict[str, list[HookHandlerEntry]],
) -> CursorHooksV1Config:
    """Build a Cursor hooks.json document from an events map (PRD-CORE-136-FR02).

    Args:
        events_map: Mapping of event name → list of handler dicts. Each handler
            dict MUST contain a ``command`` key.

    Returns:
        ``{"version": 1, "hooks": events_map}``

    Raises:
        ValueError: If any handler entry is missing the ``command`` key.
    """
    for event, handlers in events_map.items():
        for idx, handler in enumerate(handlers):
            if "command" not in handler:
                msg = (
                    f"Handler entry at events_map[{event!r}][{idx}] is missing "
                    f"required key 'command'. Got keys: {list(handler.keys())}"
                )
                raise ValueError(msg)
    return {"version": 1, "hooks": events_map}


def smart_merge_cursor_json(
    target_path: Path,
    trw_entries: CursorHooksV1Config | dict[str, object],
    identity_prefix: str,
) -> BootstrapFileResult:
    """Idempotent JSON merge for Cursor config files (PRD-CORE-136-FR02).

    Reads the existing JSON document at ``target_path``, removes prior TRW
    entries identified by ``command.startswith(identity_prefix)`` (for hook
    handler lists keyed under ``"hooks"``), then inserts the new TRW entries.
    Everything else is preserved.

    Handles two JSON shapes:
    - **hooks.json** shape: ``{"version": 1, "hooks": {event: [handlers]}}``
      where each handler has a ``"command"`` key.
    - **flat shape** (e.g. used by cli.json): any top-level structure; the
      caller is responsible for passing ``trw_entries`` as the top-level
      object to merge.  For flat shape, pass ``identity_prefix=""`` to skip
      the TRW-entry removal step (or use a dedicated merge helper).

    On malformed JSON, overwrites with ``trw_entries`` and emits a warning.

    Args:
        target_path: Path to the JSON file to merge (created if absent).
        trw_entries: New TRW content to upsert.  For hooks.json shape, pass
            the full ``{"version":1,"hooks":{...}}`` dict.  For flat shape,
            pass the keys to update at the top level.
        identity_prefix: ``command`` prefix that identifies TRW hook entries.
            Prior entries with ``command.startswith(identity_prefix)`` are
            removed before insertion.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    rel = str(target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        try:
            existing: dict[str, Any] = json.loads(target_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning(
                "smart_merge_cursor_json_malformed",
                path=rel,
                action="overwrite",
            )
            target_path.write_text(json.dumps(trw_entries, indent=2) + "\n", encoding="utf-8")
            result["updated"].append(rel)
            return result

        # Handle hooks.json shape: remove prior TRW entries by command prefix
        if "hooks" in existing and isinstance(existing["hooks"], dict) and identity_prefix:
            for event, handlers in existing["hooks"].items():
                if isinstance(handlers, list):
                    existing["hooks"][event] = [
                        h
                        for h in handlers
                        if not (
                            isinstance(h, dict)
                            and isinstance(h.get("command"), str)
                            and h["command"].startswith(identity_prefix)
                        )
                    ]

        # Merge trw_entries into existing document
        for key, value in trw_entries.items():
            if key == "hooks" and isinstance(value, dict) and isinstance(existing.get("hooks"), dict):
                # Deep merge: extend per-event handler lists
                for event, handlers in value.items():
                    if event in existing["hooks"]:
                        existing["hooks"][event] = existing["hooks"][event] + handlers
                    else:
                        existing["hooks"][event] = handlers
            else:
                existing[key] = value

        target_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        result["updated"].append(rel)
    else:
        target_path.write_text(json.dumps(trw_entries, indent=2) + "\n", encoding="utf-8")
        result["created"].append(rel)

    logger.debug(
        "smart_merge_cursor_json",
        path=rel,
        created=result["created"],
        updated=result["updated"],
    )
    return result
