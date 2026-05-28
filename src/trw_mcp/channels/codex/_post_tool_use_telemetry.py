"""Codex PostToolUse hook installer and script generator.

# Managed by TRW — no trw_distill imports permitted.

Status: aspirational (PRD-DIST-2402 §6.3, STUB-01). The hook script is
generated and installed but the channel is marked status=aspirational until
the Codex v0.131+ hook input delivery mechanism is empirically confirmed.

Key design decisions (audit compliance):
- P0-02: Script uses __file__-relative path resolution (NOT Jinja {{ repo_root }})
- P0-03: Script tries CODEX_HOOK_INPUT env var first, then stdin
- Fail-open: always exits 0 with {"continue": true}
- No {{ }} template tokens in the installed script (FR07 AC)
- stdlib-only imports (NFR05)

PRD-DIST-2402 FR07, FR08, FR09, FR10.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "HOOK_SCRIPT_CONTENT",
    "generate_hook_script",
    "install_hook_script",
]

# ---------------------------------------------------------------------------
# Hook script content — stdlib-only, no {{ }} tokens, __file__-relative paths
# ---------------------------------------------------------------------------

# This is the actual Python script that gets installed at
# .codex/hooks/trw_post_edit_telemetry.py
# It is defined as a string constant (not a Jinja template) to ensure
# zero {{ }} tokens in the installed output (audit P0-02, FR07 AC).

HOOK_SCRIPT_CONTENT = textwrap.dedent("""\
    #!/usr/bin/env python3
    \"\"\"TRW PostToolUse telemetry hook for Codex.

    Installed by trw-mcp channels. Status: aspirational (PRD-DIST-2402 STUB-01).
    Always exits 0 — never blocks Codex execution.

    Input delivery (audit P0-03 mitigation):
    - Tries CODEX_HOOK_INPUT env var first.
    - Falls back to stdin if env var absent.
    \"\"\"

    from __future__ import annotations

    import json
    import os
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    _MATCHING_TOOLS = frozenset({"apply_patch", "Bash"})
    _EVENT_SCHEMA = "channel-event/v1"
    _CONTINUE_RESPONSE = json.dumps({"continue": True})
    _SUPPRESS_RESPONSE = json.dumps({"continue": True, "suppressOutput": True})


    def _resolve_telemetry_path() -> Path:
        \"\"\"Resolve telemetry log path relative to this script's location.

        Uses __file__-relative resolution (audit P0-02 fix).
        Assumes hook is installed at .codex/hooks/trw_post_edit_telemetry.py
        so repo root is 2 levels up.
        \"\"\"
        script_dir = Path(__file__).parent
        repo_root = script_dir.parent.parent
        return repo_root / ".trw" / "telemetry" / "channel-events.jsonl"


    def _read_hook_input() -> str:
        \"\"\"Read hook input from env var or stdin (audit P0-03).\"\"\"
        env_val = os.environ.get("CODEX_HOOK_INPUT", "")
        if env_val:
            return env_val
        return sys.stdin.read()


    def _write_event(telemetry_path: Path, event: dict[str, object]) -> None:
        \"\"\"Append a JSONL event line to the telemetry log.\"\"\"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        with telemetry_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\\n")


    def main() -> None:
        \"\"\"Main hook entrypoint — always exits 0.\"\"\"
        try:
            raw = _read_hook_input()
            data = json.loads(raw)
        except (json.JSONDecodeError, Exception):
            print(_CONTINUE_RESPONSE)
            return

        try:
            tool_name = str(data.get("tool_name", ""))
        except Exception:
            print(_CONTINUE_RESPONSE)
            return

        if tool_name not in _MATCHING_TOOLS:
            print(_SUPPRESS_RESPONSE)
            return

        try:
            tool_input = data.get("tool_input", {})
            turn_id = data.get("turn_id")
            tool_use_id = data.get("tool_use_id")

            file_paths: list[str] = []
            if isinstance(tool_input, dict):
                patch = tool_input.get("patch", "")
                if patch:
                    for line in str(patch).splitlines():
                        if line.startswith("+++ b/") or line.startswith("--- a/"):
                            fp = line[6:].strip()
                            if fp and fp not in file_paths:
                                file_paths.append(fp)
                cmd = tool_input.get("command", tool_input.get("cmd", ""))
                if cmd and not file_paths:
                    file_paths = []

            event: dict[str, object] = {
                "schema": _EVENT_SCHEMA,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "channel_id": "codex-posttooluse-telemetry",
                "client": "codex",
                "tool_name": tool_name,
                "turn_id": turn_id,
                "tool_use_id": tool_use_id,
                "file_paths": file_paths,
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
# Installer
# ---------------------------------------------------------------------------


def generate_hook_script() -> str:
    """Return the hook script content string.

    Validates that no {{ }} template tokens are present (FR07 AC).

    Returns:
        Hook script as a string.

    Raises:
        ValueError: If {{ }} tokens found in the generated script content.
    """
    content = HOOK_SCRIPT_CONTENT
    if "{{" in content or "}}" in content:
        raise ValueError(
            "Hook script content contains unsubstituted {{ }} template tokens. "
            "This is a bug in _post_tool_use_telemetry.py — fix the template."
        )
    return content


def install_hook_script(
    target_dir: Path,
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Install the PostToolUse hook script at target_dir/.codex/hooks/.

    Validates no {{ }} tokens before writing (audit P0-02, FR07 AC).

    Args:
        target_dir: Repository root (hook installed at target_dir/.codex/hooks/).
        overwrite: If False, skip if the file already exists.

    Returns:
        Dict with keys: installed (bool), path (str), skipped (bool).
    """
    hook_dir = target_dir / ".codex" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hook_dir / "trw_post_edit_telemetry.py"

    if not overwrite and hook_path.exists():
        log.debug(
            "codex_hook_install_skipped",
            path=str(hook_path),
            outcome="skipped_exists",
        )
        return {"installed": False, "path": str(hook_path), "skipped": True}

    content = generate_hook_script()
    hook_path.write_text(content, encoding="utf-8")

    log.debug(
        "codex_hook_installed",
        path=str(hook_path),
        bytes_written=len(content.encode("utf-8")),
        outcome="installed",
    )

    return {"installed": True, "path": str(hook_path), "skipped": False}
