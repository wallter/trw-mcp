"""Before-edit hook installer for the Antigravity CLI.

# Managed by TRW — no trw_distill imports permitted.

Integrates with agy's workspace hook surface, verified live on agy 1.2.15 (2026-10-03, AG03-HOOK-REENABLE):

- Hooks file: ``<workspace>/.agents/hooks.json``, a JSON object whose top-level keys are NAMED hooks::

    {"trw-before-edit-telemetry": {"PreToolUse": [
        {"matcher": "<regex over tool names>", "hooks": [{"type": "command", "command": "<sh -c line>"}]}]}}

- A handler's command runs through ``sh -c`` with the working directory set to the directory that holds
  ``hooks.json`` (``.agents/``), so the installed command is relative to it: ``python3 hooks/<script>``.
- stdin is a camelCase JSON object: ``{"toolCall": {"name": "write_to_file", "args": {"TargetFile": ...}},
  "stepIdx": N, "conversationId": ..., "workspacePaths": [...]}``; stdout must be ``{"decision": "allow" | "deny" |
  "ask" | "force_ask", ...}``. A ``deny`` reply blocked a real ``write_to_file`` call in the live check.
- The hook is fail-open: it always answers ``allow`` and exits 0, so a hook fault never blocks a tool call.
- Path resolution is ``__file__``-relative (same pattern as the Codex hook).

Until 2026-10-02 TRW wrote the agy 1.0.2 shape (``.antigravitycli/hooks.json``, flat entries), which agy 1.2.x does
not read; the legacy paths are kept below only so uninstall and ``trw-mcp doctor`` can find old installs.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file

log = structlog.get_logger(__name__)

__all__ = [
    "AG03_HOOKS_PATH",
    "AG03_HOOK_NAME",
    "HOOK_SCRIPT_CONTENT",
    "LEGACY_EDIT_TOOL_MATCHER",
    "LEGACY_HOOKS_PATH",
    "LEGACY_HOOK_SCRIPT_PATH",
    "ag03_hook_spec",
    "generate_hook_script",
    "install_before_edit_hook",
]
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AG03_HOOKS_PATH = ".agents/hooks.json"
AG03_HOOK_NAME = "trw-before-edit-telemetry"
_AG03_HOOK_SCRIPT_PATH = ".agents/hooks/trw_before_edit_telemetry.py"
#: The command agy runs: relative to the directory holding hooks.json (``.agents/``), verified live.
_AG03_HOOK_COMMAND = "python3 hooks/trw_before_edit_telemetry.py"
#: What TRW wrote before agy 1.2.x moved the surface: still looked for by uninstall and doctor.
LEGACY_HOOKS_PATH = ".antigravitycli/hooks.json"
LEGACY_HOOK_SCRIPT_PATH = ".antigravitycli/hooks/trw_before_edit_telemetry.py"

_PRE_TOOL_USE_EVENT = "PreToolUse"

# Matcher regex over agy tool names (step type lowercased, minus the CORTEX_STEP_TYPE_ prefix). The names agy 1.2.15
# uses for file edits: write_to_file (live-verified), replace_file_content, multi_replace_file_content.
_EDIT_TOOL_MATCHER = "write_to_file|replace_file_content|multi_replace_file_content"
# The matcher older TRW wrote to the legacy file; uninstall recognises its entry by it.
LEGACY_EDIT_TOOL_MATCHER = "write_file|replace_file_content|multi_replace_file_content"


def ag03_hook_spec() -> dict[str, Any]:
    """The named-hook value TRW installs under :data:`AG03_HOOK_NAME` (also what uninstall must match, whole)."""
    return {
        _PRE_TOOL_USE_EVENT: [
            {"matcher": _EDIT_TOOL_MATCHER, "hooks": [{"type": "command", "command": _AG03_HOOK_COMMAND}]}
        ]
    }


# ---------------------------------------------------------------------------
# Hook script content — stdlib-only, no {{ }} tokens, __file__-relative paths
# ---------------------------------------------------------------------------

# This is the actual Python script installed at .antigravitycli/hooks/trw_before_edit_telemetry.py
# Defined as a string constant (not a Jinja template) — zero {{ }} tokens (audit P0-02).

HOOK_SCRIPT_CONTENT = textwrap.dedent("""\
    #!/usr/bin/env python3
    \"\"\"TRW PreToolUse telemetry hook for Antigravity CLI (AG-03).

    Installed by trw-mcp channels. Registered in .agents/hooks.json under the name
    trw-before-edit-telemetry. Always answers {"decision": "allow"} and exits 0, so it
    never blocks an Antigravity tool call.

    Contract, verified live on agy 1.2.15 (2026-10-03):
    - stdin: camelCase JSON with toolCall (name and args), stepIdx, conversationId, workspacePaths
    - the edited path is toolCall.args.TargetFile (write_to_file, replace_file_content)
    - stdout: {"decision": "allow" | "deny" | "ask" | "force_ask", ...}
    \"\"\"

    from __future__ import annotations

    import json
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    _CHANNEL_ID = "ag-03-before-edit-hook"
    # The canonical channel-event/v1 fields (trw_mcp.channels._telemetry
    # CHANNEL_EVENT_V1_REQUIRED): schema_version, channel_id, client, ts, event_type.
    _EVENT_SCHEMA = "channel-event/v1"
    _EVENT_TYPE = "pull_tool_call"
    _ALLOW_RESPONSE = json.dumps({"decision": "allow"})


    def _resolve_telemetry_path() -> Path:
        \"\"\"Resolve telemetry log path relative to this script's location.

        Uses __file__-relative resolution (audit P0-02 pattern).
        Assumes the hook is installed at .agents/hooks/trw_before_edit_telemetry.py,
        so the repo root is 2 levels up from this script's directory.
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
        \"\"\"Main hook entrypoint — always allows and exits 0 (fail-open).\"\"\"
        try:
            raw = sys.stdin.read()
            data = json.loads(raw)
        except Exception:  # trw-fail-silent-allow: fail-open, bad input never blocks agy
            print(_ALLOW_RESPONSE)
            return

        try:
            call = data.get("toolCall") if isinstance(data, dict) else None
            call = call if isinstance(call, dict) else {}
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            tool_name = str(call.get("name", ""))
            file_path = str(args.get("TargetFile", ""))

            event: dict[str, object] = {
                "schema_version": _EVENT_SCHEMA,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "channel_id": _CHANNEL_ID,
                "client": "antigravity-cli",
                "event_type": _EVENT_TYPE,
                "tool_name": tool_name,
                "file_path": file_path,
            }

            telemetry_path = _resolve_telemetry_path()
            _write_event(telemetry_path, event)
        except Exception:  # trw-fail-silent-allow: best-effort telemetry, never blocks agy
            pass

        print(_ALLOW_RESPONSE)


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
# hooks.json named-hook merge
# ---------------------------------------------------------------------------


def _merge_named_hook(existing: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Add TRW's named hook to an existing hooks.json object without touching anyone else's.

    Returns ``(merged, status)``: ``"added"``, ``"unchanged"`` (TRW's entry is already there, byte for byte in
    meaning) or ``"user_edited"`` (the name exists with different content: the user's edit is kept, never replaced).
    """
    spec = ag03_hook_spec()
    current = existing.get(AG03_HOOK_NAME)
    if current is None:
        merged = dict(existing)
        merged[AG03_HOOK_NAME] = spec
        return merged, "added"
    return dict(existing), "unchanged" if current == spec else "user_edited"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _result(
    target_dir: Path, *, installed: bool = False, skipped: bool = False, error: str | None = None, status: str = ""
) -> dict[str, Any]:
    return {
        "installed": installed,
        "hook_script_path": str(target_dir / _AG03_HOOK_SCRIPT_PATH),
        "hooks_json_path": str(target_dir / AG03_HOOKS_PATH),
        "skipped": skipped,
        "error": error,
        "status": status,
    }


def install_before_edit_hook(
    target_dir: Path,
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Install the AG-03 PreToolUse hook for the Antigravity CLI.

    Installs, in this order:
    1. the hook script at ``.agents/hooks/trw_before_edit_telemetry.py``;
    2. TRW's named hook into ``.agents/hooks.json`` (merged, idempotent; every other named hook is kept).

    A ``hooks.json`` that is not a JSON object is the user's file in a state TRW cannot reason about, so it is left
    untouched and reported as an error rather than started over. Fail-open: nothing here raises.

    Returns a dict with ``installed``, ``skipped``, ``error``, ``status`` and the two paths.
    """
    hook_script_path = target_dir / _AG03_HOOK_SCRIPT_PATH
    hooks_json_path = target_dir / AG03_HOOKS_PATH

    if not overwrite and hook_script_path.exists() and hooks_json_path.exists():
        log.debug("ag03_hook_install_skipped", hooks_json=str(hooks_json_path), outcome="skipped_exists")
        return _result(target_dir, skipped=True, status="skipped_exists")

    if hooks_json_path.is_symlink():
        return _result(target_dir, error=f"{AG03_HOOKS_PATH} is a symlink (symlink_leaf); refused, nothing written")

    existing: dict[str, Any] = {}
    if hooks_json_path.exists():
        try:
            parsed = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning(
                "ag03_hooks_json_unreadable", path=str(hooks_json_path), error=str(exc), outcome="left_untouched"
            )
            return _result(target_dir, error=f"{AG03_HOOKS_PATH} is not readable JSON ({exc}); left untouched")
        if not isinstance(parsed, dict):
            return _result(target_dir, error=f"{AG03_HOOKS_PATH} is not a JSON object; left untouched")
        existing = parsed

    merged, status = _merge_named_hook(existing)
    if status == "user_edited":
        # The user changed TRW's entry: honour it, and do not install a script nothing will call.
        log.info("ag03_hook_user_edited", path=str(hooks_json_path), outcome="kept")
        return _result(target_dir, skipped=True, status="user_edited")

    try:
        content = generate_hook_script()
    except ValueError as exc:
        log.warning("ag03_hook_script_invalid", error=str(exc), outcome="error")
        return _result(target_dir, error=str(exc))

    try:
        write_checkout_file(target_dir, hook_script_path, content.encode("utf-8"))  # bytes: no CRLF on Windows
        if status == "added":
            write_checkout_file(target_dir, hooks_json_path, json.dumps(merged, indent=2) + "\n")
    except (OSError, UnsafeWriteError) as exc:
        log.warning("ag03_hook_write_failed", error=str(exc), outcome="error")
        return _result(target_dir, error=f"Failed to write the AG-03 hook: {exc}")

    log.debug("ag03_hook_installed", hooks_json=str(hooks_json_path), matcher=_EDIT_TOOL_MATCHER, outcome=status)
    return _result(target_dir, installed=True, status=status)
