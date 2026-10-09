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
from pathlib import Path
from typing import cast

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file

from ._copilot_artifacts import _COPILOT_INSTRUCTIONS_DIR as _COPILOT_INSTRUCTIONS_DIR
from ._copilot_artifacts import _COPILOT_SKILLS_DIR as _COPILOT_SKILLS_DIR
from ._copilot_artifacts import _PATH_SCOPED_TEMPLATES as _PATH_SCOPED_TEMPLATES
from ._copilot_artifacts import _copilot_data_dir as _copilot_data_dir
from ._copilot_artifacts import copilot_path_instruction_contents as copilot_path_instruction_contents
from ._copilot_artifacts import copilot_skill_contents as copilot_skill_contents
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
    """Generate the minimal repo-wide Copilot block.

    `.github/copilot-instructions.md` is a file the USER owns, and Copilot has
    no syntax to include content into it — GitHub's repository-instructions docs
    and VS Code's custom-instructions docs both describe inline Markdown only.
    So the goal here is not "reference instead of inject" (impossible) but
    "inject as little as correctness allows".

    The full protocol now lives in `.github/instructions/trw-ceremony.instructions.md`,
    a TRW-owned file Copilot loads itself via `applyTo: "**"`. What stays here is
    only what that mechanism cannot guarantee: GitHub documents this file as
    always-on and "automatically included in every chat request", whereas
    `.instructions.md` files apply by pattern match. PRD-QUAL-104-FR03 requires
    the deliver gate stated verbatim in the carrier, so the gate stays where
    inclusion is unconditional.
    """
    return f"""{_COPILOT_TRW_START_MARKER}
<!-- TRW AUTO-GENERATED — do not edit between markers -->

# TRW Framework Integration

This project uses the TRW (The Real Work) framework. The full protocol — session
lifecycle, tool reference, and conventions — is in
`.github/instructions/trw-ceremony.instructions.md`, which applies to every request.

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

    ``TRW_HOOK_CLIENT`` is exported ahead of the adapter and inherited by the
    target hook it execs internally: copilot's adapter invokes the SAME
    PHYSICAL ``.claude/hooks/<script>.sh`` claude-code installs, so
    ``lib-trw.sh``'s path-derived key would otherwise always resolve "claude"
    here too. The value is derived with the SAME resolver the hook-env writer
    uses (``_hook_env_key``) rather than a literal "copilot" -- a hand-typed
    literal here previously diverged from the writer's actual key
    (``_hook_env_key`` resolves copilot's ``.github`` config dir to
    ``"github"``, not ``"copilot"``), so copilot silently read no file at all
    and ran on lib-trw.sh's built-in defaults (sol round 2 P1).
    """
    from trw_mcp.bootstrap._hook_env import _hook_env_key
    from trw_mcp.models.config._profiles import resolve_client_profile

    hook_client_key = _hook_env_key(resolve_client_profile("copilot"))
    if adapter_path is None:
        git_root = "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
        adapter_path = f"{git_root}/.github/hooks/{_COPILOT_ADAPTER_SCRIPT_NAME}"

    # The generated command is a simple two-argument invocation of the adapter
    # script.  No nested quoting, no inline shell logic — shell-safe by design.
    return f'TRW_HOOK_CLIENT={hook_client_key} /bin/sh "{adapter_path}" "{hook_path}" "{event_name}"'


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
    *,
    root: Path | None = None,
    result: dict[str, list[str]] | None = None,
) -> CopilotHooksPayload:
    """Merge TRW-managed hooks into existing Copilot hooks.json.

    Given *root* and *result*, an existing TRW group that runs a hook file the update kept stays as it is.
    """
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
        if root is not None and result is not None:
            from ._kept_hook_registration import merge_owned_groups

            owned = [g for g in existing_groups if isinstance(g, dict) and _is_trw_hook_group(g)]
            trw_groups = cast(
                "list[CopilotHookGroup]",
                merge_owned_groups(
                    cast("list[dict[str, object]]", owned),
                    cast("list[dict[str, object]]", trw_groups),
                    root=root,
                    result=result,
                    settings_rel=_COPILOT_HOOKS_PATH,
                ),
            )

        if trw_groups:
            merged_hooks[event_name] = user_groups + trw_groups
        elif user_groups:
            merged_hooks[event_name] = user_groups

    return {"version": 1, "hooks": merged_hooks}


def _keep_edited_hook(
    target_dir: Path,
    rel: str,
    incoming: bytes,
    result: dict[str, list[str]],
    force: bool,
    manifest_hashes: dict[str, str] | None,
) -> bool:
    """Keep a user-edited ``.github/hooks`` script (UF-BOOT-07, HB-2); True when it was kept.

    Replaced only when it is bytes TRW wrote: the bundled script, or the manifest's recorded hash. With no
    manifest the bundled bytes are the baseline, so anything else is an edit and survives. Reported as
    ``preserved`` like the other Copilot surfaces (a warning would read as a failed writer to the client
    adoption probe). A symlink is not judged here: the safe writer refuses it as ``symlink_leaf``.
    """
    from ._managed_client_artifacts import artifact_user_edited

    dest = target_dir / rel
    if force or dest.is_symlink() or not dest.is_file():
        return False
    if not artifact_user_edited(dest, rel, incoming, manifest_hashes):
        return False
    logger.info("copilot_hook_user_modified", path=rel)
    result["preserved"].append(rel)
    return True


def generate_copilot_hooks(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Generate ``.github/hooks/hooks.json`` and install the adapter script.

    Also copies the bundled ``trw-copilot-adapter.sh`` into
    ``.github/hooks/`` so the generated hook commands can invoke it.
    The adapter script contains all shell logic — the hooks.json ``command``
    strings are simple ``TRW_HOOK_CLIENT=copilot /bin/sh "<adapter>" "<hook>"
    "<event>"`` invocations with no nested quoting.
    """
    result = _new_result()
    hooks_dir = target_dir / _GITHUB_DIR / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # --- Install the bundled adapter script ---
    adapter_src = _bundled_adapter_script_path()
    adapter_dest = target_dir / _COPILOT_ADAPTER_INSTALL_PATH
    adapter_bytes = adapter_src.read_bytes() if adapter_src.is_file() else None
    if adapter_bytes is not None and not _keep_edited_hook(
        target_dir, _COPILOT_ADAPTER_INSTALL_PATH, adapter_bytes, result, force, manifest_hashes
    ):
        try:
            write_checkout_file(target_dir, adapter_dest, adapter_bytes)
            # Make it executable
            adapter_dest.chmod(adapter_dest.stat().st_mode | 0o111)
            _record_write(result, _COPILOT_ADAPTER_INSTALL_PATH, existed=adapter_dest.exists())
        except (OSError, UnsafeWriteError) as exc:
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
            payload = _merge_copilot_hooks(raw_existing, root=target_dir, result=result)
        else:
            payload = _copilot_hooks_payload()
        write_checkout_file(target_dir, hooks_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        _record_write(result, _COPILOT_HOOKS_PATH, existed=existed)
    except (OSError, UnsafeWriteError) as exc:
        result["errors"].append(f"Failed to write {hooks_path}: {exc}")

    return result
