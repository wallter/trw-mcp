"""Cursor IDE-specific bootstrap configuration (PRD-CORE-136 FR03-FR06, FR08).

Manages:
  .cursor/agents/   — subagent definitions (FR03)
  .cursor/skills/   — Agent Skills mirror (FR04)
  .cursor/commands/ — slash command wrappers (FR05)
  .cursor/hooks/    — full 8-event TRW hook set + hooks.json (FR08)

Shared with cursor-cli (via _cursor.py):
  .cursor/mcp.json     — MCP server entry
  .cursor/rules/       — MDC rules
  generate_cursor_hook_scripts, build_cursor_hook_config, smart_merge_cursor_json

This module composes shared helpers from _cursor.py — it does NOT duplicate
JSON-merge logic, file-copy logic, or template-rendering logic.
"""

from __future__ import annotations

import json
from importlib.resources import files as _pkg_files
from pathlib import Path

import structlog

from trw_mcp.bootstrap._cursor import HookHandlerEntry
from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Subagent definitions
# ---------------------------------------------------------------------------

# (name, description) — description goes into YAML frontmatter.
#
# Descriptions are phrased as routing rules per Cursor's delegation heuristic
# (cursor.com/docs/subagents): the main agent reads the description to decide
# delegation, so specificity about *when to invoke* matters more than a label.
# "Use proactively" framing signals Cursor to reach for the subagent without
# being asked. See docs/research/providers/cursor/cursor-ide/
# eval-and-customizations-2026-04-13.md §C1-C2.
_TRW_SUBAGENTS: list[tuple[str, str]] = [
    (
        "trw-explorer",
        "Use when the user asks to 'find', 'locate', 'search for', 'where is', "
        "'look up', or wants to map architecture, dependencies, or module "
        "boundaries. Use proactively before making changes to unfamiliar code. "
        "Read-only; does not modify files.",
    ),
    (
        "trw-implementer",
        "Use when the user asks to 'implement', 'build', 'add', 'fix', or "
        "'write tests for' any feature, bug, or component. Follows TDD: writes "
        "a failing test first, then production code that passes it. "
        "Write-enabled; respects TRW ceremony gates (build_check before "
        "declaring complete).",
    ),
    (
        "trw-reviewer",
        "Use proactively after any non-trivial code edit to check for quality "
        "gaps, security issues, missing tests, or spec drift before the user "
        "reviews. Also invoked when the user asks to 'review', 'audit', "
        "'check the diff', or 'look over changes'. Read-only; scores code "
        "against TRW's 7 review dimensions.",
    ),
    (
        "trw-researcher",
        "Use when the user asks to 'research', 'investigate', 'compare', "
        "'survey', or 'evaluate' external libraries, APIs, papers, or tools. "
        "Also use proactively when the main agent needs up-to-date docs that "
        "may have changed since the model's training cutoff. Read-only, "
        "background-capable (runs async via is_background: true).",
    ),
]

# ---------------------------------------------------------------------------
# Curated skill list (IDE surface)
#
# Excluded from the full _IDE_CURATED_SKILLS spec because they are not present
# in data/skills/ at the time of writing:
#   - trw-release  (not yet created as a skill directory)
#
# If new skills are added to data/skills/, append them here.
# ---------------------------------------------------------------------------

_IDE_CURATED_SKILLS: list[str] = [
    "trw-deliver",
    "trw-prd-ready",
    "trw-framework-check",
    "trw-ceremony-guide",
    "trw-audit",
    "trw-test-strategy",
    "trw-self-review",
    "trw-dry-check",
    "trw-security-check",
    "trw-sprint-init",
    "trw-project-health",
    "trw-memory-audit",
    "trw-reflect",
    "trw-delegate",
    # "trw-release" — skill directory does not exist yet (PRD-CORE-137)
]

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

_TRW_COMMANDS: list[tuple[str, str]] = [
    (
        "trw-deliver",
        "Run the TRW deliver ceremony — reflect, checkpoint, sync learnings, close run.",
    ),
    (
        "trw-prd-ready",
        "Create or groom a PRD to sprint-ready quality (draft → groom → review → exec plan).",
    ),
    (
        "trw-audit",
        "Run adversarial spec-vs-code audit on a PRD.",
    ),
    (
        "trw-sprint-init",
        "Initialize a new sprint: list draft PRDs, create sprint doc, bootstrap run.",
    ),
    (
        "trw-framework-check",
        "Check TRW framework compliance for the current or specified work.",
    ),
    (
        "trw-reflect",
        "End-of-session reflection — route process improvements to quick-fixes, PRDs, learnings, or backlog.",
    ),
]

# ---------------------------------------------------------------------------
# Hook events and scripts (full 8-event IDE set)
# ---------------------------------------------------------------------------

_IDE_HOOK_EVENTS: dict[str, list[HookHandlerEntry]] = {
    "sessionStart": [
        {
            "command": ".cursor/hooks/trw-session-start.sh",
            "type": "command",
            "timeout": 5,
        }
    ],
    "beforeMCPExecution": [
        {
            "command": ".cursor/hooks/trw-before-mcp.sh",
            "type": "command",
            "timeout": 5,
            "failClosed": False,
        }
    ],
    "postToolUse": [
        {
            "command": ".cursor/hooks/trw-post-tool-use.sh",
            "type": "command",
            "timeout": 5,
        }
    ],
    # preToolUse carries TWO chained handlers (PRD-DIST-2459 FR-4):
    #   1. trw-pre-tool-use.sh   — the existing observer/logger (emits allow).
    #   2. trw-before-edit-hint.sh — the CUR-06 trw-distill advisory hint hook
    #      (opt-in via cc03_hook_enabled; emits {"permission":"allow",
    #      "agent_message":...}; NEVER denies / NEVER exits 2).
    # Both run as independent array entries; both are strictly non-blocking, so
    # neither displaces the other. The hint hook is registered unconditionally
    # but is a clean plain-allow no-op until the operator enables the shared gate.
    "preToolUse": [
        {
            "command": ".cursor/hooks/trw-pre-tool-use.sh",
            "type": "command",
            "timeout": 5,
        },
        {
            "command": ".cursor/hooks/trw-before-edit-hint.sh",
            "type": "command",
            "timeout": 3,
            "failClosed": False,
        },
    ],
    "afterFileEdit": [
        {
            "command": ".cursor/hooks/trw-after-file-edit.sh",
            "type": "command",
            "timeout": 3,
        }
    ],
    "preCompact": [
        {
            "command": ".cursor/hooks/trw-pre-compact.sh",
            "type": "command",
            "timeout": 10,
        }
    ],
    "stop": [
        {
            "command": ".cursor/hooks/trw-stop.sh",
            "type": "command",
            "timeout": 5,
        }
    ],
    "beforeSubmitPrompt": [
        {
            "command": ".cursor/hooks/trw-before-submit-prompt.sh",
            "type": "command",
            "timeout": 5,
        }
    ],
}

# Scripts the IDE surface installs.
# trw-before-mcp.sh and trw-stop.sh are also used by the CLI surface;
# listing them here is harmless because generate_cursor_hook_scripts is idempotent.
_IDE_HOOK_SCRIPTS: list[str] = [
    "cli-adapter.sh",
    "trw-session-start.sh",
    "trw-before-mcp.sh",
    "trw-post-tool-use.sh",
    "trw-pre-tool-use.sh",
    # CUR-06 (PRD-DIST-2459 FR-4): trw-distill before-edit hint hook + its
    # standalone lib sibling. Chained alongside the observer in preToolUse.
    "trw-before-edit-hint.sh",
    "lib-distill-hint.sh",
    "trw-after-file-edit.sh",
    "trw-pre-compact.sh",
    "trw-stop.sh",
    "trw-before-submit-prompt.sh",
]


# ---------------------------------------------------------------------------
# Public generators
# ---------------------------------------------------------------------------


def cursor_ide_agent_contents() -> dict[str, bytes]:
    """Bundled ``.cursor/agents/trw-*.md`` content, keyed by repo-relative path.

    Single source of truth shared by :func:`generate_cursor_ide_subagents` and
    the managed-artifact manifest sweep.
    """
    template_pkg = _pkg_files("trw_mcp").joinpath("data/cursor_ide/agents")
    contents: dict[str, bytes] = {}

    for name, description in _TRW_SUBAGENTS:
        template_path = template_pkg.joinpath(f"{name}.md")
        try:
            body = template_path.read_text(encoding="utf-8")
        except (FileNotFoundError, TypeError, OSError):
            body = f"# {name}\n\nSpecialist agent for TRW framework tasks.\n"
            logger.warning("cursor_ide_agent_template_missing", name=name)

        readonly = name != "trw-implementer"
        is_background = name == "trw-researcher"

        frontmatter = (
            "---\n"
            f"name: {name}\n"
            # JSON-encode description so embedded colons / apostrophes don't
            # break the YAML scanner. JSON string literals are valid YAML
            # flow scalars.
            f"description: {json.dumps(description)}\n"
            "model: inherit\n"
            f"readonly: {str(readonly).lower()}\n"
            f"is_background: {str(is_background).lower()}\n"
            "---\n\n"
        )
        contents[f".cursor/agents/{name}.md"] = (frontmatter + body).encode("utf-8")

    return contents


def generate_cursor_ide_subagents(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> BootstrapFileResult:
    """Generate .cursor/agents/trw-*.md subagent definitions (FR03).

    Each file has YAML frontmatter (name, description, model, readonly,
    is_background) followed by the body content from bundled templates in
    data/cursor_ide/agents/<name>.md.

    Content-aware (CONSTITUTION HB-2): a TRW agent matching the bundled content
    or TRW's recorded last write is refreshed; a user-edited one is preserved.
    These files used to be written unconditionally, so a hand edit was destroyed
    on every update. User-authored agents in .cursor/agents/ outside the
    ``trw-`` prefix were and remain untouched.

    Args:
        target_dir: Root of the target git repository.
        force: When True, rewrite TRW agents even if user-edited — the
            documented escape hatch for discarding local edits.
        manifest_hashes: ``content_hashes`` from the manifest as it stood BEFORE
            this run, used to recognise TRW's own previous write.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    from ._managed_client_artifacts import artifact_user_edited

    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    agents_dir = target_dir / ".cursor" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    for rel, incoming in cursor_ide_agent_contents().items():
        target = target_dir / rel
        existed = target.exists()
        if existed and not force and artifact_user_edited(target, rel, incoming, manifest_hashes):
            logger.info("cursor_ide_agent_user_modified", path=rel)
            result["preserved"].append(rel)
            continue
        target.write_bytes(incoming)
        (result["updated"] if existed else result["created"]).append(rel)

    logger.info(
        "generate_cursor_ide_subagents",
        created=len(result["created"]),
        updated=len(result["updated"]),
        preserved=len(result["preserved"]),
    )
    return result


def cursor_ide_command_contents() -> dict[str, bytes]:
    """Bundled ``.cursor/commands/trw-*.md`` content, keyed by repo-relative path.

    Single source of truth shared by :func:`generate_cursor_ide_commands` and
    the managed-artifact manifest sweep.
    """
    template_pkg = _pkg_files("trw_mcp").joinpath("data/cursor_ide/commands")
    contents: dict[str, bytes] = {}

    for cmd_name, description in _TRW_COMMANDS:
        # Try bundled template first
        template_path = template_pkg.joinpath(f"{cmd_name}.md")
        try:
            content = template_path.read_text(encoding="utf-8")
        except (FileNotFoundError, TypeError, OSError):
            # Fallback inline body
            content = (
                f"# /{cmd_name}\n\n"
                f"{description}\n\n"
                "## When to use\n\n"
                f"When you explicitly want to invoke TRW's {cmd_name} workflow.\n\n"
                "## What it does\n\n"
                f"Invokes the TRW `{cmd_name}` skill via the MCP server, following "
                "the framework's ceremony protocol.\n"
            )
            logger.warning("cursor_ide_command_template_missing", name=cmd_name)

        contents[f".cursor/commands/{cmd_name}.md"] = content.encode("utf-8")

    return contents


def generate_cursor_ide_commands(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> BootstrapFileResult:
    """Generate .cursor/commands/trw-*.md slash command wrappers (FR05).

    Each command file is generated from a bundled template in
    data/cursor_ide/commands/<name>.md when present; falls back to an
    inline body.  User-authored commands in .cursor/commands/ outside
    the ``trw-`` prefix are preserved.

    Content-aware (CONSTITUTION HB-2): a TRW command matching the bundled
    content or TRW's recorded last write is refreshed; a user-edited one is
    preserved. These files used to be written unconditionally.

    Args:
        target_dir: Root of the target git repository.
        force: When True, rewrite TRW commands even if user-edited.
        manifest_hashes: ``content_hashes`` from the manifest as it stood BEFORE
            this run, used to recognise TRW's own previous write.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    from ._managed_client_artifacts import artifact_user_edited

    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
    commands_dir = target_dir / ".cursor" / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)

    for rel, incoming in cursor_ide_command_contents().items():
        target = target_dir / rel
        existed = target.exists()
        if existed and not force and artifact_user_edited(target, rel, incoming, manifest_hashes):
            logger.info("cursor_ide_command_user_modified", path=rel)
            result["preserved"].append(rel)
            continue
        target.write_bytes(incoming)
        (result["updated"] if existed else result["created"]).append(rel)

    logger.info(
        "generate_cursor_ide_commands",
        created=len(result["created"]),
        updated=len(result["updated"]),
        preserved=len(result["preserved"]),
    )
    return result


def cursor_ide_skill_contents(source_skills_dir: Path | None = None) -> dict[str, bytes]:
    """Bundled ``.cursor/skills/**`` content for the IDE curated skill list.

    Single source of truth shared by :func:`generate_cursor_ide_skills` (via
    ``generate_cursor_skills_mirror``) and the managed-artifact manifest sweep.
    """
    from ._cursor import cursor_skill_mirror_contents

    return cursor_skill_mirror_contents(_IDE_CURATED_SKILLS, source_skills_dir)


def generate_cursor_ide_skills(
    target_dir: Path,
    source_skills_dir: Path | None = None,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> BootstrapFileResult:
    """Mirror curated TRW skills into .cursor/skills/<name>/ (FR04).

    Delegates to shared ``generate_cursor_skills_mirror`` from ``_cursor.py``
    with the IDE curated skill list.  Skills not present in source are logged
    and skipped — the generator does not fail.  User skills in
    ``.cursor/skills/`` outside the curated list are preserved.

    Args:
        target_dir: Root of the target git repository.
        source_skills_dir: Override for bundled skills directory. Defaults to
            ``data/skills/`` within the installed package.
        force: When True, remove existing skill dirs before copy.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    from ._cursor import generate_cursor_skills_mirror

    return generate_cursor_skills_mirror(
        target_dir,
        _IDE_CURATED_SKILLS,
        source_skills_dir,
        force=force,
        manifest_hashes=manifest_hashes,
    )


def generate_cursor_ide_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Install IDE hook scripts and merge the 8-event IDE event list into hooks.json (FR08).

    Composes shared helpers from ``_cursor.py`` — does not reimplement JSON
    merge or script copy logic.

    Steps:
    1. Install bash adapters via ``generate_cursor_hook_scripts``.
    2. Merge IDE events into ``.cursor/hooks.json`` via ``smart_merge_cursor_json``.

    Args:
        target_dir: Root of the target git repository.
        force: When True, overwrite existing hook scripts unconditionally.

    Returns:
        Dict with 'created'/'updated'/'preserved' lists.
    """
    from ._cursor import (
        build_cursor_hook_config,
        generate_cursor_hook_scripts,
        smart_merge_cursor_json,
    )

    result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}

    # 1. Install bash adapters via shared helper
    script_result = generate_cursor_hook_scripts(
        target_dir,
        _IDE_HOOK_SCRIPTS,
        force=force,
    )
    result["created"].extend(script_result.get("created") or [])
    result["updated"].extend(script_result.get("updated") or [])
    result["preserved"].extend(script_result.get("preserved") or [])

    # 2. Merge IDE events into hooks.json via shared helper
    hooks_file = target_dir / ".cursor" / "hooks.json"
    trw_hooks_body = build_cursor_hook_config(_IDE_HOOK_EVENTS)
    merge_result = smart_merge_cursor_json(
        hooks_file,
        trw_hooks_body,
        identity_prefix=".cursor/hooks/trw-",
    )
    result["created"].extend(merge_result.get("created") or [])
    result["updated"].extend(merge_result.get("updated") or [])
    result["preserved"].extend(merge_result.get("preserved") or [])

    logger.info(
        "generate_cursor_ide_hooks",
        created=len(result["created"]),
        updated=len(result["updated"]),
    )
    return result
