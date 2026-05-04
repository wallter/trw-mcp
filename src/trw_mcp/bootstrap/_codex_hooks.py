"""Codex hooks helpers — extracted from _codex.py.

Belongs to the ``_codex.py`` facade. Re-exported there for back-compat.

Hook-cluster helpers that build, identify, merge, and emit the
``.codex/hooks.json`` payload for TRW-managed shell hooks
(SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / Stop):

- ``_trw_hook_group`` — single matcher group factory
- ``_codex_hooks_payload`` — full TRW hooks.json payload
- ``_is_trw_hook_group`` — TRW hook group detector
- ``merge_codex_hooks`` — TRW + user merge preserving user groups
- ``generate_codex_hooks`` — write .codex/hooks.json (with merge if exists)

Extracted as DIST-243 batch 42 (continuation) to keep the parent
``_codex.py`` module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from trw_mcp.models.typed_dicts import (
    BootstrapFileResult,
    CodexHookCommand,
    CodexHookMatcherEntry,
    CodexHooksConfig,
)

from ._codex_normalize import _normalize_hook_config
from ._file_ops import _new_result, _record_write

_TRW_HOOK_DESCRIPTION_PREFIX = "TRW managed:"
_CODEX_HOOKS_PATH = ".codex/hooks.json"


def _trw_hook_group(
    *,
    event: str,
    script_name: str,
    status_message: str | None = None,
    matcher: str | None = None,
    timeout: int | None = None,
) -> CodexHookMatcherEntry:
    """Create a single TRW-managed hook matcher group."""
    git_root = "$(git rev-parse --show-toplevel)"
    command = f'/bin/sh "{git_root}/.claude/hooks/{script_name}"'
    hook_command: CodexHookCommand = {"type": "command", "command": command}
    if status_message is not None:
        hook_command["statusMessage"] = status_message
    if timeout is not None:
        hook_command["timeout"] = timeout

    group: CodexHookMatcherEntry = {
        "description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} {event}",
        "hooks": [hook_command],
    }
    if matcher is not None:
        group["matcher"] = matcher
    return group


def _codex_hooks_payload() -> CodexHooksConfig:
    """Return a Codex hooks.json payload backed by existing TRW shell hooks."""
    return {
        "hooks": {
            "SessionStart": [
                _trw_hook_group(
                    event="SessionStart",
                    matcher="startup|resume",
                    script_name="session-start.sh",
                    status_message="Loading TRW session context",
                )
            ],
            "UserPromptSubmit": [
                _trw_hook_group(
                    event="UserPromptSubmit",
                    script_name="user-prompt-submit.sh",
                    status_message="Checking TRW phase guidance",
                )
            ],
            "PreToolUse": [
                _trw_hook_group(
                    event="PreToolUse",
                    script_name="pre-tool-deliver-gate.sh",
                    status_message="Checking TRW delivery gate",
                )
            ],
            "PostToolUse": [
                _trw_hook_group(
                    event="PostToolUse",
                    script_name="post-tool-event.sh",
                    status_message="Logging TRW tool effects",
                )
            ],
            "Stop": [_trw_hook_group(event="Stop", script_name="stop-ceremony.sh", timeout=30)],
        }
    }


def _is_trw_hook_group(event: str, group: CodexHookMatcherEntry) -> bool:
    """Identify a TRW-managed hook group in an existing hooks config."""
    description = group.get("description")
    if isinstance(description, str) and description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX):
        return True

    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False

    expected_script_names = {
        "SessionStart": "session-start.sh",
        "UserPromptSubmit": "user-prompt-submit.sh",
        "PreToolUse": "pre-tool-deliver-gate.sh",
        "PostToolUse": "post-tool-event.sh",
        "Stop": "stop-ceremony.sh",
    }
    expected_script = expected_script_names.get(event)
    if expected_script is None:
        return False

    for hook in hooks:
        if isinstance(hook, dict):
            command = hook.get("command")
            if isinstance(command, str) and expected_script in command and "/.claude/hooks/" in command:
                return True
    return False


def merge_codex_hooks(existing: CodexHooksConfig) -> CodexHooksConfig:
    """Merge TRW-managed Codex hooks into an existing hooks config."""
    merged = _normalize_hook_config(existing)
    current_hooks = merged.get("hooks", {})
    trw_hooks = _codex_hooks_payload()["hooks"]
    merged_hooks: dict[str, list[CodexHookMatcherEntry]] = {}

    for event_name in sorted(set(current_hooks) | set(trw_hooks)):
        user_groups = [
            group for group in current_hooks.get(event_name, []) if not _is_trw_hook_group(event_name, group)
        ]
        if event_name in trw_hooks:
            merged_hooks[event_name] = user_groups + trw_hooks[event_name]
        elif user_groups:
            merged_hooks[event_name] = user_groups

    return {"hooks": merged_hooks}


def generate_codex_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate `.codex/hooks.json`."""
    result: BootstrapFileResult = cast("BootstrapFileResult", _new_result())
    codex_dir = target_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    hooks_path = codex_dir / "hooks.json"
    existed = hooks_path.exists()

    try:
        if existed and not force:
            raw_existing = json.loads(hooks_path.read_text(encoding="utf-8"))
            payload = merge_codex_hooks(_normalize_hook_config(raw_existing))
            hooks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_HOOKS_PATH, existed=True)
        else:
            payload = _codex_hooks_payload()
            hooks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_HOOKS_PATH, existed=existed)
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append(f"Failed to write {hooks_path}: {exc}")

    return result
