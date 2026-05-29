"""AG-03: Before-edit hook installer for Antigravity CLI.

# Managed by TRW — no trw_distill imports permitted.

Status: ACTIVE — hook surface confirmed 2026-05-28 via binary string analysis
of agy v1.0.2 (Google "jetski" internal codename).

Empirical verification (2026-05-28):
- agy v1.0.2 is the real Antigravity CLI (Go binary, google3/third_party/jetski/)
- .antigravitycli/settings.json is confirmed as the repo-scoped MCP config path
  (Gate G-01 CONFIRMED — file exists at /home/wallter/projects/trw-framework/.antigravitycli/settings.json)
- Hooks live in a SEPARATE file: .antigravitycli/hooks.json (NOT in settings.json)
  Evidence: binary function ParseHooksFile (jsonhook package) + binary strings showing
  "agents.txt, agent.json, hooks.json, rules.json, skills.txt" as peer workspace files
- Hook event names: "PreToolUse" and "PostToolUse" confirmed (binary strings: exact literals)
- Hook format:
    {
      "PreToolUse": [{"matcher": "<regex>", "command": "<shell-command>"}],
      "PostToolUse": [{"matcher": "<regex>", "command": "<shell-command>"}]
    }
- PreToolHookResult has: Decision, Reason, Overwrite, PermissionOverrides, AllowTool, DenyReason
  (binary: hooks_go_proto.(*PreToolHookResult).GetDecision etc.)
- Hook is fail-open: always exits 0 to avoid blocking Antigravity tool execution
- Script uses __file__-relative path resolution (same pattern as Codex hook, audit P0-02)
- Global settings: ~/.gemini/antigravity-cli/settings.json (user-level)
- Config dir: .antigravitycli/ (repo-scoped)

OQ-01 RESOLVED: hooks.json is the correct file, "PreToolUse" is the event key.
Gate G-01 CONFIRMED: .antigravitycli/settings.json is the correct config path.

PRD-DIST-2404 FR07-FR10, AG-03.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "AG03_CHANNEL_ID",
    "AG03_HOOKS_PATH",
    "HOOK_SCRIPT_CONTENT",
    "generate_hook_script",
    "install_before_edit_hook",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AG03_CHANNEL_ID = "ag-03-before-edit-hook"
AG03_HOOKS_PATH = ".antigravitycli/hooks.json"
_AG03_HOOK_SCRIPT_PATH = ".antigravitycli/hooks/trw_before_edit_telemetry.py"

# Empirically confirmed hook event name (agy v1.0.2 binary string analysis 2026-05-28)
_PRE_TOOL_USE_EVENT = "PreToolUse"

# Matcher regex: write_file, replace_file_content, multi_replace_file_content
# These are the Antigravity CLI file-editing tool names observed in the binary.
_EDIT_TOOL_MATCHER = "write_file|replace_file_content|multi_replace_file_content"

# ---------------------------------------------------------------------------
# Hook script content — stdlib-only, no {{ }} tokens, __file__-relative paths
# ---------------------------------------------------------------------------

# This is the actual Python script installed at .antigravitycli/hooks/trw_before_edit_telemetry.py
# Defined as a string constant (not a Jinja template) — zero {{ }} tokens (audit P0-02).

HOOK_SCRIPT_CONTENT = textwrap.dedent("""\
    #!/usr/bin/env python3
    \"\"\"TRW PreToolUse telemetry hook for Antigravity CLI (AG-03).

    Installed by trw-mcp channels. Status: active (PRD-DIST-2404).
    Always exits 0 — never blocks Antigravity tool execution.

    Empirically verified 2026-05-28 (agy v1.0.2 binary string analysis):
    - Hooks loaded from .antigravitycli/hooks.json
    - Event key: "PreToolUse"
    - Input: JSON delivered via stdin (same pattern as Gemini CLI jsonhook.ParseHooksFile)
    - Format: {"matcher": "<regex>", "command": "<cmd>"}
    - PreToolHookResult allows: Decision, Reason, AllowTool, DenyReason
    \"\"\"

    from __future__ import annotations

    import json
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    _CHANNEL_ID = "ag-03-before-edit-hook"
    _EVENT_SCHEMA = "channel-event/v1"
    _CONTINUE_RESPONSE = json.dumps({"continue": True})


    def _resolve_telemetry_path() -> Path:
        \"\"\"Resolve telemetry log path relative to this script's location.

        Uses __file__-relative resolution (audit P0-02 pattern).
        Assumes hook is installed at .antigravitycli/hooks/trw_before_edit_telemetry.py
        so repo root is 2 levels up.
        \"\"\"
        script_dir = Path(__file__).parent
        repo_root = script_dir.parent.parent
        return repo_root / ".trw" / "telemetry" / "channel-events.jsonl"


    def _write_event(telemetry_path: Path, event: dict[str, object]) -> None:
        \"\"\"Append a JSONL event line to the telemetry log.\"\"\"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        with telemetry_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\\n")


    def main() -> None:
        \"\"\"Main hook entrypoint — always exits 0 (fail-open).\"\"\"
        try:
            raw = sys.stdin.read()
            data = json.loads(raw)
        except Exception:
            print(_CONTINUE_RESPONSE)
            return

        try:
            tool_name = str(data.get("tool_name", data.get("name", "")))
            file_path = str(data.get("file_path", data.get("path", "")))

            event: dict[str, object] = {
                "schema": _EVENT_SCHEMA,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "channel_id": _CHANNEL_ID,
                "client": "antigravity-cli",
                "tool_name": tool_name,
                "file_path": file_path,
            }

            telemetry_path = _resolve_telemetry_path()
            _write_event(telemetry_path, event)
        except Exception:
            pass

        print(_CONTINUE_RESPONSE)


    if __name__ == "__main__":
        main()
""")


# ---------------------------------------------------------------------------
# Validation and script generation
# ---------------------------------------------------------------------------


def generate_hook_script() -> str:
    """Return the hook script content string.

    Validates that no {{ }} template tokens are present (audit P0-02).

    Returns:
        Hook script as a string.

    Raises:
        ValueError: If {{ }} tokens found in the generated script content.
    """
    content = HOOK_SCRIPT_CONTENT
    if "{{" in content or "}}" in content:
        raise ValueError(
            "Hook script content contains unsubstituted {{ }} template tokens. "
            "This is a bug in _before_edit_hook.py — fix the template."
        )
    return content


# ---------------------------------------------------------------------------
# hooks.json deep-merge helper
# ---------------------------------------------------------------------------


def _merge_hooks_json(
    existing: dict[str, Any],
    hook_entry: dict[str, str],
    event_key: str = _PRE_TOOL_USE_EVENT,
) -> dict[str, Any]:
    """Deep-merge a hook entry into an existing hooks.json dict.

    Idempotent: if a hook entry with the same command already exists,
    it is not duplicated.

    Args:
        existing: Current hooks.json content (may be empty dict).
        hook_entry: Hook entry dict with "matcher" and "command" keys.
        event_key: Antigravity event key (e.g. "PreToolUse").

    Returns:
        Merged dict suitable for json.dumps().
    """
    result = dict(existing)
    event_hooks: list[dict[str, str]] = list(result.get(event_key, []))

    # Idempotent: skip if an entry with the same command already exists
    command = hook_entry.get("command", "")
    if not any(h.get("command") == command for h in event_hooks):
        event_hooks.append(hook_entry)

    result[event_key] = event_hooks
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_before_edit_hook(
    target_dir: Path,
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Install the AG-03 PreToolUse hook for Antigravity CLI.

    Empirically confirmed schema (agy v1.0.2, 2026-05-28):
    - Hooks file: .antigravitycli/hooks.json
    - Event key: "PreToolUse"
    - Format: {"PreToolUse": [{"matcher": "<regex>", "command": "<shell>"}]}

    Installs:
    1. Hook script at .antigravitycli/hooks/trw_before_edit_telemetry.py
    2. Merges hook entry into .antigravitycli/hooks.json (idempotent deep-merge)

    Fail-open: all exceptions are caught and returned in the result dict.
    Never raises.

    Args:
        target_dir: Repository root directory.
        overwrite: When False, skip if both files already exist.

    Returns:
        Dict with keys: installed (bool), hook_script_path (str),
        hooks_json_path (str), skipped (bool), error (str | None).
    """
    hook_script_path = target_dir / _AG03_HOOK_SCRIPT_PATH
    hooks_json_path = target_dir / AG03_HOOKS_PATH

    if not overwrite and hook_script_path.exists() and hooks_json_path.exists():
        log.debug(
            "ag03_hook_install_skipped",
            hook_script=str(hook_script_path),
            hooks_json=str(hooks_json_path),
            outcome="skipped_exists",
        )
        return {
            "installed": False,
            "hook_script_path": str(hook_script_path),
            "hooks_json_path": str(hooks_json_path),
            "skipped": True,
            "error": None,
        }

    try:
        content = generate_hook_script()
    except ValueError as exc:
        log.warning("ag03_hook_script_invalid", error=str(exc), outcome="error")
        return {
            "installed": False,
            "hook_script_path": str(hook_script_path),
            "hooks_json_path": str(hooks_json_path),
            "skipped": False,
            "error": str(exc),
        }

    try:
        hook_script_path.parent.mkdir(parents=True, exist_ok=True)
        hook_script_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        log.warning(
            "ag03_hook_script_write_failed",
            path=str(hook_script_path),
            error=str(exc),
            outcome="error",
        )
        return {
            "installed": False,
            "hook_script_path": str(hook_script_path),
            "hooks_json_path": str(hooks_json_path),
            "skipped": False,
            "error": f"Failed to write hook script: {exc}",
        }

    # Build the hook entry: command is the path to the installed script
    # The command is called by agy on each PreToolUse event matching the tool regex
    hook_command = f"python3 {_AG03_HOOK_SCRIPT_PATH}"
    hook_entry: dict[str, str] = {
        "matcher": _EDIT_TOOL_MATCHER,
        "command": hook_command,
    }

    # Deep-merge into hooks.json (idempotent)
    existing: dict[str, Any] = {}
    if hooks_json_path.exists():
        try:
            raw = hooks_json_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                existing = parsed
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "ag03_hooks_json_parse_failed",
                path=str(hooks_json_path),
                error=str(exc),
                outcome="warning",
            )
            # Start fresh — don't corrupt the file

    merged = _merge_hooks_json(existing, hook_entry, event_key=_PRE_TOOL_USE_EVENT)

    try:
        hooks_json_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_json_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning(
            "ag03_hooks_json_write_failed",
            path=str(hooks_json_path),
            error=str(exc),
            outcome="error",
        )
        return {
            "installed": False,
            "hook_script_path": str(hook_script_path),
            "hooks_json_path": str(hooks_json_path),
            "skipped": False,
            "error": f"Failed to write hooks.json: {exc}",
        }

    log.debug(
        "ag03_hook_installed",
        hook_script=str(hook_script_path),
        hooks_json=str(hooks_json_path),
        event_key=_PRE_TOOL_USE_EVENT,
        matcher=_EDIT_TOOL_MATCHER,
        outcome="installed",
    )

    return {
        "installed": True,
        "hook_script_path": str(hook_script_path),
        "hooks_json_path": str(hooks_json_path),
        "skipped": False,
        "error": None,
    }
