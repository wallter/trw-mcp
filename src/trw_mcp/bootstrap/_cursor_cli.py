"""Cursor CLI-specific bootstrap configuration (PRD-CORE-137).

Owns the CLI-only artifacts:
  - AGENTS.md (primary instruction surface with TRW sentinel block)
  - .cursor/cli.json (baseline permissions — allow/deny token grammar)
  - .cursor/hooks.json (CLI-safe 5-event subset)

All shared concerns — JSON merge, file copy, MCP config — are composed via
helpers in ``_cursor.py``.  This module contains **no** JSON-merge or
file-copy logic of its own (PRD-CORE-137-NFR04).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import structlog
from typing_extensions import TypedDict

from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

from ._cursor import HookHandlerEntry
from ._file_ops import read_json_object

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# TypedDicts for .cursor/cli.json
# ---------------------------------------------------------------------------


class CursorCliPermissions(TypedDict):
    """Shape of the ``permissions`` object within .cursor/cli.json."""

    allow: list[str]
    deny: list[str]


class CursorCliConfig(TypedDict, total=False):
    """Shape of a parsed .cursor/cli.json document."""

    _note: str
    permissions: CursorCliPermissions


# ---------------------------------------------------------------------------
# Permissions baseline (PRD-CORE-137-FR03)
# ---------------------------------------------------------------------------

_DEFAULT_ALLOW: Final[tuple[str, ...]] = (
    "Read(**/*)",
    "Shell(git)",
    "Shell(grep)",
    "Shell(find)",
    "Shell(rg)",
    "Shell(ls)",
    "Shell(cat)",
    "Shell(pytest)",
    "Shell(npm)",
    "Shell(python)",
    "Shell(trw-mcp)",
)

_DEFAULT_DENY: Final[tuple[str, ...]] = (
    "Shell(rm -rf)",
    "Shell(curl)",
    "Shell(wget)",
    "Read(.env*)",
    "Read(**/.env.local)",
    "Read(**/secrets.yaml)",
    "Write(.env*)",
    "Write(.git/**/*)",
    "Write(.trw/**/*)",
    "Write(node_modules/**/*)",
)

# ---------------------------------------------------------------------------
# CLI hook events (PRD-CORE-137-FR05)
# 5 reliably-firing events on cursor-agent; IDE-only events excluded.
# beforeShellExecution + beforeMCPExecution are security-critical gates:
# failClosed=True so a crash/timeout fails the run rather than silently
# permitting the operation.
# ---------------------------------------------------------------------------

_CLI_HOOK_EVENTS: dict[str, list[HookHandlerEntry]] = {
    "beforeShellExecution": [
        {
            "command": ".cursor/hooks/trw-before-shell.sh",
            "type": "command",
            "timeout": 5,
            "failClosed": True,
        }
    ],
    "afterShellExecution": [
        {
            "command": ".cursor/hooks/trw-after-shell.sh",
            "type": "command",
            "timeout": 5,
            "failClosed": False,
        }
    ],
    "beforeMCPExecution": [
        {
            "command": ".cursor/hooks/trw-before-mcp.sh",
            "type": "command",
            "timeout": 5,
            "failClosed": True,
        }
    ],
    "afterMCPExecution": [
        {
            "command": ".cursor/hooks/trw-after-mcp.sh",
            "type": "command",
            "timeout": 5,
            "failClosed": False,
        }
    ],
    "stop": [
        {
            "command": ".cursor/hooks/trw-stop.sh",
            "type": "command",
            "timeout": 5,
            "failClosed": False,
        }
    ],
}

# Scripts the CLI surface installs. cli-adapter, trw-before-mcp, trw-stop are
# shared with the IDE surface; CLI-specific ones are trw-before-shell,
# trw-after-shell, trw-after-mcp.
_CLI_HOOK_SCRIPTS: Final[tuple[str, ...]] = (
    "cli-adapter.sh",
    "trw-before-shell.sh",
    "trw-after-shell.sh",
    "trw-before-mcp.sh",
    "trw-after-mcp.sh",
    "trw-stop.sh",
)


# ---------------------------------------------------------------------------
# Task 14: .cursor/cli.json permissions generator (PRD-CORE-137-FR03)
# ---------------------------------------------------------------------------


def _extract_cli_permissions(raw: dict[str, object]) -> tuple[list[str], list[str]]:
    """Validate and extract allow/deny lists from a parsed cli.json object.

    ``raw`` is the structurally-validated top-level object returned by
    :func:`read_json_object` (always a ``dict``; the non-object case is already
    collapsed to ``None`` by the seam). This function owns the *inner* shape
    policy: it raises ``TypeError`` when ``permissions`` — or its ``allow`` /
    ``deny`` members — has the wrong type, so the caller treats the document as
    corrupt and overwrites with TRW defaults. Without the list checks a
    ``{"permissions": {"allow": "x"}}`` document would reach ``allow.append`` and
    raise an uncaught ``AttributeError``.

    Mutates ``raw`` in place via ``setdefault`` so the caller can write the
    merged document straight back.
    """
    perms = raw.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise TypeError("permissions must be a JSON object")
    allow = perms.setdefault("allow", [])
    deny = perms.setdefault("deny", [])
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise TypeError("permissions.allow and permissions.deny must be JSON arrays")
    return allow, deny


def _write_cli_json(cli_file: Path, payload: object, result: BootstrapFileResult) -> bool:
    """Write the cli.json *payload*, returning ``True`` on success.

    Never raises: an ``OSError`` (unwritable path, is-a-directory, permission)
    is converted into a content-free structural error on ``result["errors"]``.
    The upstream fail-open caller logs ``{exc}`` verbatim, so swallowing the
    error here is what keeps a corrupt/locked ``.cursor/cli.json`` from leaking
    a raw path / errno string into higher-level warnings.
    """
    try:
        cli_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("cursor_cli_config_write_failed", path=str(cli_file), reason="unwritable")
        result.setdefault("errors", []).append(
            ".cursor/cli.json could not be written (unwritable path)."
        )
        return False
    return True


def generate_cursor_cli_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate or smart-merge .cursor/cli.json with TRW baseline permissions.

    Fresh write: creates ``{"_note": ..., "permissions": {"allow": [...], "deny": [...]}}``
    with the full TRW baseline.

    Smart merge: reads the existing file, appends TRW defaults that are not
    already present in either allow OR deny (to avoid duplicate tokens), and
    preserves all extra top-level JSON keys (e.g. "model_defaults").

    On malformed JSON: overwrites with defaults and records a warning.

    Threat model note: ``Read(**/*)`` is appropriate for trusted-repo CI.
    See the ``_note`` field in the generated file and docs/CLIENT-PROFILES.md
    for the full security posture discussion (PRD-CORE-137-FR03).

    Args:
        target_dir: Root of the target repository.
        force: When True, overwrite unconditionally.

    Returns:
        BootstrapFileResult with created/updated/info lists populated.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": [], "info": []}
    cursor_dir = target_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cli_file = cursor_dir / "cli.json"

    _note = (
        "TRW baseline permissions for cursor-agent CI. "
        "Read(**/*) is appropriate for trusted-repo CI; tighten for untrusted content. "
        "See docs/CLIENT-PROFILES.md for the security posture discussion."
    )
    default_permissions: CursorCliPermissions = {
        "allow": list(_DEFAULT_ALLOW),
        "deny": list(_DEFAULT_DENY),
    }
    default_config: CursorCliConfig = {
        "_note": _note,
        "permissions": default_permissions,
    }

    if cli_file.exists() and not force:
        # Read through the shared structural seam: absent / unreadable (OSError) /
        # non-UTF-8 / malformed / non-object all collapse to ``None`` with a
        # content-free diagnostic, so no parser detail or raw byte ever escapes.
        raw = read_json_object(cli_file, context="cursor_cli")
        permissions: tuple[list[str], list[str]] | None = None
        if raw is not None:
            try:
                permissions = _extract_cli_permissions(raw)
            except (TypeError, ValueError):
                permissions = None
        if permissions is not None and raw is not None:
            allow, deny = permissions
            # Add TRW allow-tokens not already in allow or deny
            for token in _DEFAULT_ALLOW:
                if token not in allow and token not in deny:
                    allow.append(token)
            # Add TRW deny-tokens not already in deny or allow
            for token in _DEFAULT_DENY:
                if token not in deny and token not in allow:
                    deny.append(token)
            if _write_cli_json(cli_file, raw, result):
                result["updated"].append(".cursor/cli.json")
        else:
            # Unreadable / corrupt / wrong-shape: preserve existing behavior —
            # overwrite with TRW defaults and surface a content-free advisory.
            logger.warning(
                "cursor_cli_config_overwritten",
                path=str(cli_file),
                reason="corrupt_or_unreadable",
            )
            if _write_cli_json(cli_file, default_config, result):
                result["updated"].append(".cursor/cli.json")
                result.setdefault("info", []).append(
                    "WARNING: .cursor/cli.json was malformed or unreadable — "
                    "overwritten with TRW defaults."
                )
    elif _write_cli_json(cli_file, default_config, result):
        result["created"].append(".cursor/cli.json")

    # Emit TTY/tmux reminder (PRD-CORE-137-FR08a)
    _emit_cli_safety_reminder(result)

    logger.info(
        "generate_cursor_cli_config",
        created=result["created"],
        updated=result["updated"],
        outcome="success",
    )
    return result


# ---------------------------------------------------------------------------
# Task 15: AGENTS.md generator with sentinel block (PRD-CORE-137-FR04)
# ---------------------------------------------------------------------------


def _merge_agents_md(existing: str, trw_block: str, begin: str, end: str) -> str:
    """Return new AGENTS.md content with TRW block replaced/prepended.

    Pure function: takes (existing_content, trw_block, begin_sentinel,
    end_sentinel) and returns the merged document as a string.  No I/O.

    If both sentinels are found, the content between them is replaced with the
    new ``trw_block`` and everything outside is preserved.  If sentinels are
    absent, the TRW block is prepended with a blank line before existing content.
    """
    if begin in existing and end in existing:
        pre, _, rest = existing.partition(begin)
        _, _, post = rest.partition(end)
        return pre + trw_block + post
    return trw_block + "\n\n" + existing


def generate_cursor_cli_agents_md(
    target_dir: Path,
    trw_section: str,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate or smart-merge AGENTS.md with TRW ceremony sentinel block.

    Fresh write: creates AGENTS.md containing only the TRW sentinel block.

    Smart merge: finds the ``<!-- TRW:BEGIN -->`` / ``<!-- TRW:END -->``
    markers and replaces the content between them with the new ``trw_section``.
    Everything outside the block is preserved.  If no markers are found,
    prepends the TRW block and preserves the original content below.

    Args:
        target_dir: Root of the target repository.
        trw_section: Rendered TRW instruction content for the sentinel block.
        force: When True, overwrite unconditionally.

    Returns:
        BootstrapFileResult with created/updated lists.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": [], "info": []}
    agents_file = target_dir / "AGENTS.md"

    begin = "<!-- TRW:BEGIN -->"
    end = "<!-- TRW:END -->"
    trw_block = f"{begin}\n# TRW Ceremony Protocol (cursor-cli)\n\n{trw_section}\n{end}"

    if agents_file.exists() and not force:
        existing = agents_file.read_text(encoding="utf-8")
        new_content = _merge_agents_md(existing, trw_block, begin, end)
        agents_file.write_text(new_content, encoding="utf-8")
        result["updated"].append("AGENTS.md")
    else:
        agents_file.write_text(trw_block + "\n", encoding="utf-8")
        result["created"].append("AGENTS.md")

    logger.info(
        "generate_cursor_cli_agents_md",
        created=result["created"],
        updated=result["updated"],
        outcome="success",
    )
    return result


# ---------------------------------------------------------------------------
# Task 16: CLI hooks subset generator (PRD-CORE-137-FR05)
# ---------------------------------------------------------------------------


def generate_cursor_cli_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Install CLI hook scripts and merge the CLI event subset into hooks.json.

    Composes shared helpers from ``_cursor.py`` — no JSON-merge or file-copy
    logic lives here (PRD-CORE-137-NFR04).

    The 5-event CLI subset: beforeShellExecution, afterShellExecution,
    beforeMCPExecution, afterMCPExecution, stop.  IDE-only events
    (beforeTabFileRead, afterTabFileEdit, subagentStart, beforeSubmitPrompt)
    are never written by this function.

    Args:
        target_dir: Root of the target repository.
        force: When True, overwrite existing hook scripts.

    Returns:
        BootstrapFileResult with created/updated/preserved lists.
    """
    from ._cursor import (
        build_cursor_hook_config,
        generate_cursor_hook_scripts,
        smart_merge_cursor_json,
    )

    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": [], "info": []}

    # 1. Install bash adapters via shared helper (idempotent; missing scripts warned+skipped)
    script_result = generate_cursor_hook_scripts(target_dir, list(_CLI_HOOK_SCRIPTS), force=force)
    result["created"].extend(script_result.get("created", []))
    result["updated"].extend(script_result.get("updated", []))
    result["preserved"].extend(script_result.get("preserved", []))

    # 2. Build the hook config payload and merge via shared helper.
    #    When cursor-ide has already written its 8 events, smart_merge_cursor_json
    #    preserves them and only appends/replaces entries keyed by
    #    identity_prefix=".cursor/hooks/trw-".
    hooks_file = target_dir / ".cursor" / "hooks.json"
    trw_hooks_body = build_cursor_hook_config({k: list(v) for k, v in _CLI_HOOK_EVENTS.items()})
    merge_result = smart_merge_cursor_json(
        hooks_file,
        trw_hooks_body,
        identity_prefix=".cursor/hooks/trw-",
    )
    result["created"].extend(merge_result.get("created", []))
    result["updated"].extend(merge_result.get("updated", []))
    result["preserved"].extend(merge_result.get("preserved", []))

    logger.info(
        "generate_cursor_cli_hooks",
        created=result.get("created", []),
        updated=result.get("updated", []),
        outcome="success",
    )
    return result


# ---------------------------------------------------------------------------
# Task 17b: TTY/tmux reminder helper (PRD-CORE-137-FR08a)
# ---------------------------------------------------------------------------

_TTY_REMINDER_LINES: Final[tuple[str, ...]] = (
    (
        "Cursor CLI requires a real TTY in automation. "
        "Wrap 'cursor-agent -p' invocations in tmux for raw subprocess environments."
    ),
    ("GitHub Actions runners provide a TTY implicitly; no wrapping needed there."),
)


def _emit_cli_safety_reminder(result: BootstrapFileResult) -> None:
    """Append TTY/tmux advisory lines to result["info"] and emit via structlog.

    Called from ``generate_cursor_cli_config`` so the reminder appears every
    time cursor-cli init runs (PRD-CORE-137-FR08a).

    Idempotent: repeated calls do not duplicate lines in the info list.
    """
    info = result.setdefault("info", [])
    for line in _TTY_REMINDER_LINES:
        if line not in info:
            info.append(line)
    logger.info(
        "cursor_cli_tty_reminder",
        tty_required=True,
        tmux_workaround="wrap cursor-agent -p invocations in tmux",
        github_actions_exception="TTY provided implicitly — no tmux needed",
    )
