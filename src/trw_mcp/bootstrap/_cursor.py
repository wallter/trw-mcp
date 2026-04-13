"""Shared Cursor bootstrap helpers — used by both cursor-ide and cursor-cli surfaces.

Named exports (PRD-CORE-136-FR02):
  generate_cursor_mcp_config      FR07: .cursor/mcp.json smart-merge
  generate_cursor_rules_mdc       FR06: .cursor/rules/trw-ceremony.mdc (new name)
  generate_cursor_rules           FR06: backward-compat alias → generate_cursor_rules_mdc
  generate_cursor_skills_mirror   NEW:  shutil.copytree per named skill, preserves others
  generate_cursor_hook_scripts    NEW:  copy bundled data/hooks/cursor/<name> → .cursor/hooks/
  build_cursor_hook_config        NEW:  returns {"version":1,"hooks":events_map}
  smart_merge_cursor_json         NEW:  idempotent JSON merge keyed on command prefix

Legacy exports (kept for backward compat):
  generate_cursor_hooks           FR05: old hook-list style; still wired in _ide_targets.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Final

import structlog
from typing_extensions import TypedDict

from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data directory for bundled cursor hook scripts
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data"
_CURSOR_HOOKS_DATA_DIR = _DATA_DIR / "hooks" / "cursor"


# ---------------------------------------------------------------------------
# TypedDicts for Cursor JSON config structures
# ---------------------------------------------------------------------------


class CursorServerEntry(TypedDict, total=False):
    """TRW MCP server entry within Cursor's mcp.json ``mcpServers`` section."""

    command: str | list[str]
    args: list[str]


class CursorMcpConfig(TypedDict, total=False):
    """Shape of a parsed .cursor/mcp.json document."""

    mcpServers: dict[str, CursorServerEntry]


class CursorHookEntry(TypedDict, total=False):
    """Single hook entry in the *legacy* list-style .cursor/hooks.json.

    Used only by ``generate_cursor_hooks`` (the backward-compat legacy helper).
    New code should use ``HookHandlerEntry`` and ``CursorHooksV1Config``.
    """

    event: str
    command: str
    description: str


class HookHandlerEntry(TypedDict, total=False):
    """A single handler within a Cursor hooks.json v1 event list.

    Cursor v2.4+ hooks.json shape::

        {
          "version": 1,
          "hooks": {
            "<eventName>": [<HookHandlerEntry>, ...]
          }
        }

    Required key: ``command``.  Optional: ``type``, ``timeout``, ``failClosed``, ``matcher``.
    """

    command: str
    type: str
    timeout: int
    failClosed: bool
    matcher: str


class CursorHooksV1Config(TypedDict, total=False):
    """Shape of a parsed .cursor/hooks.json (version 1, dict-of-events format).

    Contrast with the legacy list-style format used by ``generate_cursor_hooks``.
    """

    version: int
    hooks: dict[str, list[HookHandlerEntry]]


class CursorHooksConfig(TypedDict, total=False):
    """Shape of a parsed .cursor/hooks.json document (legacy list style)."""

    hooks: list[CursorHookEntry]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_trw_mcp_entry_cursor() -> CursorServerEntry:
    """Return TRW MCP server entry for Cursor's mcp.json format.

    Uses the installed ``trw-mcp`` binary when available; falls back to
    the current Python interpreter invoking the module directly.
    """
    if shutil.which("trw-mcp"):
        command: str | list[str] = "trw-mcp"
    else:
        command = [sys.executable, "-m", "trw_mcp.server"]
    return {"command": command, "args": ["--debug"]}


def _write_fresh_mcp(path: Path, trw_entry: CursorServerEntry) -> None:
    """Write a fresh .cursor/mcp.json with only the TRW server entry."""
    config: CursorMcpConfig = {"mcpServers": {"trw": trw_entry}}
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# FR05: Cursor Hook Adapter
# ---------------------------------------------------------------------------


def generate_cursor_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate .cursor/hooks.json with TRW hook configurations (FR05).

    Cursor supports hook events for IDE lifecycle integration. TRW uses 4:
    - beforeMCPExecution: Advisory tool ordering enforcement (non-blocking)
    - beforeSubmitPrompt: Phase-aware protocol injection
    - afterFileEdit: File modification tracking reminder
    - stop: Advisory ceremony warning (non-blocking)

    If the file already exists and ``force`` is False, performs a smart merge:
    preserves user hooks while adding/replacing TRW hooks (identified by
    description prefix "TRW").

    Returns dict with 'created'/'updated'/'preserved' lists.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    hooks_dir = target_dir / ".cursor"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = hooks_dir / "hooks.json"

    trw_hooks: list[CursorHookEntry] = [
        {
            "event": "beforeMCPExecution",
            "command": "bash -c 'echo \"TRW: Ensure trw_session_start() is called before other trw_* tools for full context.\"'",
            "description": "TRW MCP tool ordering advisory",
        },
        {
            "event": "beforeSubmitPrompt",
            "command": "bash -c 'echo \"TRW: Load prior learnings with trw_session_start() for full context.\"'",
            "description": "TRW ceremony protocol injection",
        },
        {
            "event": "afterFileEdit",
            "command": "bash -c 'echo \"TRW: File modified — checkpoint saves progress.\"'",
            "description": "TRW file modification tracking",
        },
        {
            "event": "stop",
            "command": "bash -c 'echo \"TRW: Call trw_deliver() to persist learnings for future sessions.\"'",
            "description": "TRW delivery reminder (advisory)",
        },
    ]

    if hooks_file.exists() and not force:
        # Smart merge: preserve user hooks, add/replace TRW hooks
        try:
            existing: CursorHooksConfig = json.loads(hooks_file.read_text(encoding="utf-8"))
            existing_hooks: list[CursorHookEntry] = existing.get("hooks", [])
            # Remove existing TRW hooks (identified by description prefix)
            non_trw = [h for h in existing_hooks if not h.get("description", "").startswith("TRW")]
            existing["hooks"] = non_trw + trw_hooks
            hooks_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        except (json.JSONDecodeError, KeyError):
            # Malformed JSON — overwrite with fresh config
            hooks_file.write_text(json.dumps({"hooks": trw_hooks}, indent=2) + "\n", encoding="utf-8")
        result["updated"].append(".cursor/hooks.json")
    else:
        hooks_file.write_text(json.dumps({"hooks": trw_hooks}, indent=2) + "\n", encoding="utf-8")
        result["created"].append(".cursor/hooks.json")

    logger.debug(
        "generate_cursor_hooks",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# FR06: Cursor Rules Generation
# ---------------------------------------------------------------------------


def generate_cursor_rules(
    target_dir: Path,
    trw_section: str,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Backward-compat alias for generate_cursor_rules_mdc (FR06).

    Thin wrapper — delegates to the canonical ``generate_cursor_rules_mdc``
    with ``client_id="cursor-ide"``.  Kept for one release to avoid breaking
    callers that import this name directly (e.g. _ide_targets.py).

    Args:
        target_dir: Root of the target git repository.
        trw_section: Content to embed between the MDC frontmatter and end of file.
        force: When True, overwrite unconditionally.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    return generate_cursor_rules_mdc(target_dir, trw_section, client_id="cursor-ide", force=force)


# ---------------------------------------------------------------------------
# FR07: Cursor MCP Configuration
# ---------------------------------------------------------------------------


def generate_cursor_mcp_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate .cursor/mcp.json with TRW MCP server entry (FR07).

    Smart merges with an existing config — preserves the user's other MCP
    servers while ensuring the ``trw`` entry is present and up-to-date.

    Args:
        target_dir: Root of the target git repository.
        force: When True, overwrite the file unconditionally.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    cursor_dir = target_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = cursor_dir / "mcp.json"

    trw_entry = _get_trw_mcp_entry_cursor()

    if mcp_file.exists() and not force:
        # Smart merge: update only the trw key, preserve everything else
        try:
            existing: CursorMcpConfig = json.loads(mcp_file.read_text(encoding="utf-8"))
            servers: dict[str, CursorServerEntry] = existing.get("mcpServers", {})
            servers["trw"] = trw_entry
            existing["mcpServers"] = servers
            mcp_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        except (json.JSONDecodeError, KeyError):
            # Malformed JSON — write fresh config
            _write_fresh_mcp(mcp_file, trw_entry)
        result["updated"].append(".cursor/mcp.json")
    else:
        _write_fresh_mcp(mcp_file, trw_entry)
        result["created"].append(".cursor/mcp.json")

    logger.debug(
        "generate_cursor_mcp_config",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# PRD-CORE-136-FR02: generate_cursor_rules_mdc (new canonical name)
# generate_cursor_rules kept as backward-compat alias below
# ---------------------------------------------------------------------------


_CURSOR_IDE_APPENDIX: Final[str] = """\

## TRW Trigger Phrases

When the user says any of these, use the corresponding TRW tool:

| User says | Call |
|-----------|------|
| "start", "begin", "load context", "what do we know about X" | `trw_session_start(query="X")` |
| "checkpoint", "save progress", "mark milestone" | `trw_checkpoint(message="...")` |
| "I learned that X", "gotcha: X", "remember X" | `trw_learn(summary="X", detail="...")` |
| "deliver", "finish up", "wrap up" | `trw_deliver()` |
| "check", "verify", "did tests pass" | `trw_build_check(scope="full")` |
| "review", "audit", "look over the changes" | `trw_review()` |
| "plan", "break down", "organize this task" | `trw_init(task_name="...")` |

## Verification Pass

After completing a non-trivial task, run the verification pass:

1. `trw_build_check(scope="full")` — confirm tests + types pass.
2. `trw_review()` — score the diff against quality dimensions.
3. If PRD-scoped, run `/trw-audit` for spec compliance.

Treat the verification pass as a hard gate, not a suggestion. Cursor's
native harness auto-verifies against requirements; amplify that behavior
with the TRW tools, do not skip it.

## If the Agent Drifts

If you notice output diverging from the ceremony protocol mid-conversation,
say one of:

- "Follow the TRW ceremony protocol."
- "Call `trw_session_start` before other TRW tools."
- "Verify with `git diff` before claiming complete."

Cursor's Agent prioritizes recent messages over the always-apply rule
(recency bias), so a mid-conversation reinforcement restores context.

## Planning

- If Cursor's Plan Mode (Shift+Tab) is active, let the plan file drive
  execution.
- Otherwise, `trw_init(task_name="...")` produces the run plan.
- Pick one per task; do not duplicate planning artifacts.

## Pre-Compaction

When Cursor signals an upcoming context compaction (preCompact hook fires,
or the conversation nears the context window limit), call
`trw_pre_compact_checkpoint()` BEFORE responding further — it preserves
the resumption point across the compression boundary.
"""


def generate_cursor_rules_mdc(
    target_dir: Path,
    trw_section: str,
    *,
    client_id: str = "cursor-ide",
    force: bool = False,
) -> BootstrapFileResult:
    """Generate .cursor/rules/trw-ceremony.mdc (canonical name for PRD-CORE-136-FR02).

    Identical to ``generate_cursor_rules`` but accepts a ``client_id`` parameter
    so the generator can be called from both cursor-ide and cursor-cli surfaces.
    When ``client_id == "cursor-ide"``, a cursor-IDE-specific appendix is
    concatenated after the platform-generic ``trw_section`` — trigger-phrase
    table, verification-pass guidance, drift-recovery hints, Plan Mode note,
    and pre-compaction checkpoint reminder. See docs/research/providers/cursor/
    cursor-ide/eval-and-customizations-2026-04-13.md §C3/C7/C8/C10.

    Args:
        target_dir: Root of the target git repository.
        trw_section: Platform-generic ceremony content (from
            ``render_agents_trw_section()``).
        client_id: Caller surface identifier. ``"cursor-ide"`` receives the
            appendix; other values get only the shared ``trw_section``.
        force: When True, overwrite unconditionally. Since this generator
            always rewrites the file, ``force`` affects only logging.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists. A file that existed
        before this call is reported as ``updated``; otherwise ``created``.
        The ``force`` flag does not change this classification — a forced
        rewrite of an existing file is still an update.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    rules_dir = target_dir / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rules_file = rules_dir / "trw-ceremony.mdc"

    appendix = _CURSOR_IDE_APPENDIX if client_id == "cursor-ide" else ""
    content = (
        "---\n"
        'description: "TRW ceremony enforcement — ensures learnings persist across sessions"\n'
        "globs: []\n"
        "alwaysApply: true\n"
        "---\n\n"
        f"{trw_section}\n"
        f"{appendix}"
    )

    # Existence check determines create-vs-update classification.
    # ``force`` is reserved for future smart-merge variants; here the file is
    # always rewritten so classification is driven by prior existence only.
    existed = rules_file.exists()
    rules_file.write_text(content, encoding="utf-8")

    if existed:
        result["updated"].append(".cursor/rules/trw-ceremony.mdc")
    else:
        result["created"].append(".cursor/rules/trw-ceremony.mdc")

    logger.info(
        "generate_cursor_rules_mdc",
        outcome="success",
        client_id=client_id,
        force=force,
        appendix_applied=bool(appendix),
        created=len(result["created"]),
        updated=len(result["updated"]),
    )
    return result


# ---------------------------------------------------------------------------
# PRD-CORE-136-FR02: generate_cursor_skills_mirror
# ---------------------------------------------------------------------------


def generate_cursor_skills_mirror(
    target_dir: Path,
    skill_names: list[str],
    source_dir: Path | None = None,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Mirror named TRW skills into .cursor/skills/ (PRD-CORE-136-FR02).

    For each name in ``skill_names``, copies the skill directory tree from
    ``source_dir`` (or the bundled ``data/skills/`` directory) to
    ``.cursor/skills/<name>/``.  User-authored skills NOT in ``skill_names``
    are preserved untouched.

    Args:
        target_dir: Root of the target git repository.
        skill_names: Skill directory names to mirror (e.g. ["trw-deliver"]).
        source_dir: Override for bundled skills directory. Defaults to
            ``data/skills/`` within the installed package.
        force: When True, remove existing skill dirs before copy.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    skills_src = source_dir or (_DATA_DIR / "skills")
    dest_root = target_dir / ".cursor" / "skills"
    dest_root.mkdir(parents=True, exist_ok=True)

    for name in skill_names:
        src = skills_src / name
        dst = dest_root / name
        if not src.is_dir():
            logger.warning("cursor_skill_source_missing", skill=name, src=str(src))
            continue

        existed = dst.exists()
        if existed and force:
            shutil.rmtree(dst)
            existed = False

        shutil.copytree(str(src), str(dst), dirs_exist_ok=True)

        rel = f".cursor/skills/{name}"
        if existed:
            result["updated"].append(rel)
        else:
            result["created"].append(rel)

    logger.debug(
        "generate_cursor_skills_mirror",
        skills=skill_names,
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# PRD-CORE-136-FR02: generate_cursor_hook_scripts
# ---------------------------------------------------------------------------


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
            os.chmod(str(dst), 0o755)
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


# ---------------------------------------------------------------------------
# PRD-CORE-136-FR02: build_cursor_hook_config
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# PRD-CORE-136-FR02: smart_merge_cursor_json
# ---------------------------------------------------------------------------


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
                        h for h in handlers
                        if not (isinstance(h, dict) and isinstance(h.get("command"), str)
                                and h["command"].startswith(identity_prefix))
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
