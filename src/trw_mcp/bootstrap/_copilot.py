"""Copilot CLI-specific bootstrap configuration.

Generates and smart-merges repo-scoped Copilot artifacts:
- .github/copilot-instructions.md  (repo-wide instructions)
- .github/instructions/*.instructions.md  (path-scoped instructions)
- .github/hooks/hooks.json  (hook event handlers with stdin JSON I/O)
- .github/agents/*.agent.md  (agent definitions)
- .github/skills/*/SKILL.md  (skill definitions)

PRD-CORE-127: Copilot CLI integration as first-class TRW client profile.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

import structlog

# Mirrored-artifact generators (.github/instructions, /agents, /skills) live in
# _copilot_artifacts (350-eLOC gate). Re-exported so ``from ._copilot import ...``
# keeps working for _init_project_ide.py, _ide_targets.py,
# _version_migration_clients.py, bootstrap/__init__.py, and the test modules that
# import through this facade.
from ._copilot_artifacts import _COPILOT_AGENT_TEMPLATES as _COPILOT_AGENT_TEMPLATES
from ._copilot_artifacts import _COPILOT_AGENTS_DIR as _COPILOT_AGENTS_DIR
from ._copilot_artifacts import _COPILOT_INSTRUCTIONS_DIR as _COPILOT_INSTRUCTIONS_DIR
from ._copilot_artifacts import _COPILOT_SKILLS_DIR as _COPILOT_SKILLS_DIR
from ._copilot_artifacts import _PATH_SCOPED_TEMPLATES as _PATH_SCOPED_TEMPLATES
from ._copilot_artifacts import _copilot_data_dir as _copilot_data_dir
from ._copilot_artifacts import _copilot_skills_source_dir as _copilot_skills_source_dir
from ._copilot_artifacts import copilot_agent_contents as copilot_agent_contents
from ._copilot_artifacts import copilot_path_instruction_contents as copilot_path_instruction_contents
from ._copilot_artifacts import copilot_skill_contents as copilot_skill_contents
from ._copilot_artifacts import generate_copilot_agents as generate_copilot_agents
from ._copilot_artifacts import generate_copilot_path_instructions as generate_copilot_path_instructions
from ._copilot_artifacts import install_copilot_skills as install_copilot_skills
from ._copilot_models import (
    CopilotHookCommand,
    CopilotHookConfig,
    CopilotHookGroup,
    CopilotHooksPayload,
    PathScopedTemplate,
)
from ._file_ops import (
    _new_result,
    _record_write,
    read_json_object,
    write_instruction_file_with_merge,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_GITHUB_DIR = ".github"
_COPILOT_INSTRUCTIONS_PATH = ".github/copilot-instructions.md"
_COPILOT_HOOKS_PATH = ".github/hooks/hooks.json"
_COPILOT_ADAPTER_SCRIPT_NAME = "trw-copilot-adapter.sh"
_COPILOT_ADAPTER_INSTALL_PATH = f".github/hooks/{_COPILOT_ADAPTER_SCRIPT_NAME}"

# ---------------------------------------------------------------------------
# Marker constants (prefixed to avoid confusion with _opencode.py markers)
# ---------------------------------------------------------------------------

_COPILOT_TRW_START_MARKER = "<!-- trw:copilot:start -->"
_COPILOT_TRW_END_MARKER = "<!-- trw:copilot:end -->"
_TRW_HOOK_DESCRIPTION_PREFIX = "TRW managed:"

#: Copilot's own externalization sidecar. Deliberately NOT the shared
#: ``.trw/INSTRUCTIONS.md``: that file holds the Claude Code block, and a
#: project with both clients installed would have each overwrite the other's
#: content while both instruction files still reported success.
_COPILOT_SIDECAR_RELPATH = ".trw/COPILOT-INSTRUCTIONS.md"


# ---------------------------------------------------------------------------
# TypedDicts for structured data
# ---------------------------------------------------------------------------


# TypedDicts re-exported from _copilot_models.py (cycle 36).
__all__ = [
    "CopilotHookCommand",
    "CopilotHookConfig",
    "CopilotHookGroup",
    "CopilotHooksPayload",
    "PathScopedTemplate",
]


# ---------------------------------------------------------------------------
# Data directory helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Instructions generation
# ---------------------------------------------------------------------------


def _copilot_deliver_gate_block() -> str:
    """Return the FR03 session-start + deliver-gate block (bundled-source derived).

    Function-local import avoids pulling the heavy ``sections`` package at
    ``_copilot`` import time (PRD-QUAL-104 FR03).
    """
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

    return render_deliver_gate_statement()


def _copilot_instructions_content() -> str:
    """Generate repo-wide Copilot instruction content with TRW ceremony guidance.

    PRD-QUAL-104 FR03: injects the non-negotiable session-start + deliver-gate
    block so the Copilot protocol carrier always states the gate verbatim.
    """
    return f"""{_COPILOT_TRW_START_MARKER}
<!-- TRW AUTO-GENERATED — do not edit between markers -->

# TRW Framework Integration

This project uses the TRW (The Real Work) framework for structured AI-assisted development.

## Session Protocol

| Tool | When | Why |
|------|------|-----|
| `trw_session_start()` | First action | Loads prior learnings |
| `trw_learn(summary, detail)` | On discoveries | Saves findings for future sessions |
| `trw_checkpoint(message)` | After milestones | Resume point if context compacts |
| `trw_deliver()` | Last action after validation | Persists work after build evidence or a valid structured acceptable-failure record |

## Available MCP Tools

TRW tools are available via MCP server. Key tools: `trw_session_start`, `trw_learn`,
`trw_checkpoint`, `trw_deliver`, `trw_init`, `trw_status`, `trw_recall`,
`trw_build_check`, `trw_review`, `trw_prd_create`, `trw_prd_validate`.

## Conventions

- Run tests after each change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)

{_copilot_deliver_gate_block()}
{_COPILOT_TRW_END_MARKER}
"""


def generate_copilot_instructions(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate or smart-merge ``.github/copilot-instructions.md``.

    Delegates to the shared ``write_instruction_file_with_merge`` helper.
    """
    result = _new_result()
    target_path = target_dir / _COPILOT_INSTRUCTIONS_PATH
    rendered = _copilot_instructions_content()

    if _externalize_copilot_block(target_path, target_dir, rendered, result, force=force):
        return result

    write_instruction_file_with_merge(
        target_path=target_path,
        rel_path=_COPILOT_INSTRUCTIONS_PATH,
        trw_section=rendered,
        start_marker=_COPILOT_TRW_START_MARKER,
        end_marker=_COPILOT_TRW_END_MARKER,
        force=force,
        result=result,
    )
    return result


def _externalize_copilot_block(
    target_path: Path,
    project_root: Path,
    rendered: str,
    result: dict[str, list[str]],
    *,
    force: bool = False,
) -> bool:
    """Replace copilot's inline block with an ``@``-include. ``True`` when handled.

    **Resolution base — verified, not assumed.** GitHub documents the reference as
    repo-relative without saying whether it resolves from the repository root or
    the containing directory, and those differ for a file under ``.github/``.
    Emitting on a guess would produce an instruction file that parses and carries
    nothing. Read out of the shipped Copilot CLI bundle
    (``@github/copilot-linux-x64/app.js``): the repository case calls
    ``hut(root, root, "repository", "")`` -> ``repoResolveInstructionImports(
    content, filePath, root)``. The base is the REPOSITORY ROOT, so a
    repo-relative sidecar path resolves correctly from
    ``.github/copilot-instructions.md``. (The home-level branch passes
    ``dirname(path)`` — different base, but TRW never writes there.)

    Copilot's OWN marker pair is threaded through, because the uninstall registry
    and ``doctor`` both key on ``trw:copilot:start/end``; emitting the carrier's
    generic pair here would orphan the block from both.

    Declines to the inline path (returning ``False``) whenever externalization is
    off, the profile declares no import syntax, or the carrier raises — inline
    always works, a dangling include does not. ``force`` still replaces wholesale,
    and an unchanged re-run reports ``preserved`` rather than ``updated``.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.state.claude_md._instruction_carrier import IMPORT_CAPABLE_SYNTAXES, CarrierMode, apply_carrier

    config = get_config()
    if config.instruction_externalize == "off":
        return False

    # Decline BEFORE touching the file. `apply_carrier` writes whichever mode it
    # resolves to, so calling it for an include-incapable client wrote the file
    # via the INLINE path and only then failed the mode check below. The restore
    # is a no-op when the file did not exist, so it stayed on disk — and the
    # inline writer that runs next saw a pre-existing identical file and
    # reported "preserved" for a file this installer had just created. Wrong
    # bookkeeping about our own writes is a truthfulness defect, not cosmetic.
    if resolve_client_profile("copilot").instruction_import_syntax not in IMPORT_CAPABLE_SYNTAXES:
        return False

    before = target_path.read_text(encoding="utf-8") if target_path.is_file() else None
    if force and target_path.is_file():
        target_path.write_text("", encoding="utf-8")

    profile = resolve_client_profile("copilot")
    try:
        outcome = apply_carrier(
            target_path,
            rendered,
            profile.instruction_max_lines,
            import_syntax=profile.instruction_import_syntax,
            externalize=config.instruction_externalize,
            scope="root",
            external_filename=_COPILOT_SIDECAR_RELPATH,
            project_root=project_root,
            markers=(_COPILOT_TRW_START_MARKER, _COPILOT_TRW_END_MARKER),
        )
    except Exception:  # justified: fail-open — bootstrap must never break on carrier failure
        logger.warning("copilot_externalize_failed", target=str(target_path), exc_info=True)
        if before is not None:
            target_path.write_text(before, encoding="utf-8")
        return False

    if outcome.mode is not CarrierMode.IMPORT:
        if before is not None:
            target_path.write_text(before, encoding="utf-8")
        return False

    after = target_path.read_text(encoding="utf-8") if target_path.is_file() else None
    if before is None:
        key = "created"
    elif before == after:
        key = "preserved"
    else:
        key = "updated"
    result.setdefault(key, []).append(_COPILOT_INSTRUCTIONS_PATH)
    return True


# ---------------------------------------------------------------------------
# Hooks generation — stdin JSON I/O adapter
# ---------------------------------------------------------------------------

# Copilot hook event names (camelCase) mapped to TRW hook scripts
_COPILOT_HOOK_MAP: dict[str, CopilotHookConfig] = {
    "sessionStart": {
        "script": "session-start.sh",
        "description": "Loading TRW session context",
    },
    "userPromptSubmitted": {
        "script": "user-prompt-submit.sh",
        "description": "Checking TRW phase guidance",
    },
    "preToolUse": {
        "script": "pre-tool-deliver-gate.sh",
        "description": "Checking TRW delivery gate",
    },
    "postToolUse": {
        "script": "post-tool-event.sh",
        "description": "Logging TRW tool effects",
    },
    "sessionEnd": {
        "script": "stop-ceremony.sh",
        "description": "Running TRW session cleanup",
    },
}


def _bundled_adapter_script_path() -> Path:
    """Return the path to the bundled Copilot adapter shell script."""
    return _copilot_data_dir() / "hooks" / _COPILOT_ADAPTER_SCRIPT_NAME


def _build_hook_adapter_command(event_name: str, hook_path: str, adapter_path: str | None = None) -> str:
    """Build a shell command that adapts Copilot stdin JSON to TRW hook scripts.

    The command invokes the bundled ``trw-copilot-adapter.sh`` script, which:
    1. Reads JSON from stdin
    2. Extracts ``toolName`` (jq preferred, grep/sed fallback)
    3. Exports ``$TOOL_NAME`` and pipes the raw JSON to the target TRW hook
    4. For ``preToolUse``: translates the hook exit code into a JSON
       ``permissionDecision`` object on stdout

    Because the logic lives in a real shell script there is NO inline shell
    quoting inside a JSON string — eliminating the entire class of
    single-quote-nesting bugs that caused the previous ``unexpected EOF``
    error when Copilot ran the command via ``bash -c``.

    ``adapter_path`` is the installed location of ``trw-copilot-adapter.sh``
    inside the target project (defaults to ``$git_root/.github/hooks/…``).
    It is a plain path string — no quoting needed in the generated command.
    """
    if adapter_path is None:
        git_root = "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
        adapter_path = f"{git_root}/.github/hooks/{_COPILOT_ADAPTER_SCRIPT_NAME}"

    # The generated command is a simple two-argument invocation of the adapter
    # script.  No nested quoting, no inline shell logic — shell-safe by design.
    return f'/bin/sh "{adapter_path}" "{hook_path}" "{event_name}"'


def _copilot_hooks_payload() -> CopilotHooksPayload:
    """Return a Copilot hooks.json payload with TRW adapter scripts.

    Copilot hooks use stdin JSON I/O. Each hook entry runs a shell adapter
    that reads JSON from stdin, extracts fields, sources the shared TRW
    hook script, and (for preToolUse) returns JSON on stdout.
    """
    hooks: dict[str, list[CopilotHookGroup]] = {}
    git_root = "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

    for event_name, config in _COPILOT_HOOK_MAP.items():
        hook_path = f"{git_root}/.claude/hooks/{config['script']}"
        command = _build_hook_adapter_command(event_name, hook_path)

        hook_entry: CopilotHookCommand = {
            "type": "command",
            "command": command,
        }

        hooks[event_name] = [
            {
                "description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} {config['description']}",
                "hooks": [hook_entry],
            }
        ]

    return {"version": 1, "hooks": hooks}


def _is_trw_hook_group(group: dict[str, object]) -> bool:
    """Identify a TRW-managed hook group in an existing hooks config."""
    description = group.get("description")
    return isinstance(description, str) and description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX)


def _merge_copilot_hooks(
    existing: dict[str, object],
) -> CopilotHooksPayload:
    """Merge TRW-managed hooks into existing Copilot hooks.json."""
    existing_hooks = existing.get("hooks", {})
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}

    trw_payload = _copilot_hooks_payload()
    trw_hooks = trw_payload["hooks"]

    merged_hooks: dict[str, list[CopilotHookGroup]] = {}

    for event_name in sorted(set(existing_hooks) | set(trw_hooks)):
        existing_groups = existing_hooks.get(event_name, [])
        if not isinstance(existing_groups, list):
            existing_groups = []

        # Keep user-managed groups, replace TRW-managed ones
        user_groups: list[CopilotHookGroup] = [
            cast("CopilotHookGroup", g) for g in existing_groups if isinstance(g, dict) and not _is_trw_hook_group(g)
        ]
        trw_groups = trw_hooks.get(event_name, [])

        if trw_groups:
            merged_hooks[event_name] = user_groups + trw_groups
        elif user_groups:
            merged_hooks[event_name] = user_groups

    return {"version": 1, "hooks": merged_hooks}


def generate_copilot_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate ``.github/hooks/hooks.json`` and install the adapter script.

    Also copies the bundled ``trw-copilot-adapter.sh`` into
    ``.github/hooks/`` so the generated hook commands can invoke it.
    The adapter script contains all shell logic — the hooks.json ``command``
    strings are simple ``/bin/sh "<adapter>" "<hook>" "<event>"`` invocations
    with no nested quoting.
    """
    result = _new_result()
    hooks_dir = target_dir / _GITHUB_DIR / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # --- Install the bundled adapter script ---
    adapter_src = _bundled_adapter_script_path()
    adapter_dest = target_dir / _COPILOT_ADAPTER_INSTALL_PATH
    if adapter_src.is_file():
        try:
            shutil.copy2(adapter_src, adapter_dest)
            # Make it executable
            adapter_dest.chmod(adapter_dest.stat().st_mode | 0o111)
            _record_write(result, _COPILOT_ADAPTER_INSTALL_PATH, existed=adapter_dest.exists())
        except OSError as exc:
            result["errors"].append(f"Failed to install adapter script: {exc}")

    # --- Write hooks.json ---
    hooks_path = target_dir / _COPILOT_HOOKS_PATH
    existed = hooks_path.exists()
    try:
        if existed and not force:
            raw_existing = read_json_object(hooks_path, context="copilot_hooks")
            if raw_existing is None:
                # Existing file is unreadable / non-UTF-8 / malformed / not a
                # JSON object. Leave the user's file untouched and report a
                # content-free diagnostic rather than crashing or silently
                # clobbering it. ``force=True`` overwrites with a fresh payload.
                result["errors"].append(
                    f"Skipped {_COPILOT_HOOKS_PATH}: existing file is not a readable JSON object "
                    "(left untouched; re-run with force=True to overwrite)"
                )
                return result
            payload = _merge_copilot_hooks(raw_existing)
        else:
            payload = _copilot_hooks_payload()
        hooks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        _record_write(result, _COPILOT_HOOKS_PATH, existed=existed)
    except OSError as exc:
        result["errors"].append(f"Failed to write {hooks_path}: {exc}")

    return result
