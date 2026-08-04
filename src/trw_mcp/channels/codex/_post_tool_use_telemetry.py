"""Codex PostToolUse hook installer and script generator.

# Managed by TRW — no trw_distill imports permitted.

Status: active (PRD-DIST-2402 §6.3). Hook input mechanism empirically confirmed
2026-05-28 via binary string analysis of codex-cli 0.133.0:
- CODEX_HOOK_INPUT env var is ABSENT from the Rust binary — Codex does NOT use it.
- "failed to write hook stdin:" error string is PRESENT — Codex delivers hook input via stdin.
- See scripts/verify-codex-hook-input.sh for the full verification procedure.

Key design decisions (audit compliance):
- P0-02: Script uses __file__-relative path resolution (NOT Jinja {{ repo_root }})
- P0-03 RESOLVED: Script reads stdin as primary mechanism; CODEX_HOOK_INPUT env var
  kept as forward-compatibility fallback only (Codex never sets this env var).
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

    Installed by trw-mcp channels. Status: active (PRD-DIST-2402).
    Always exits 0 — never blocks Codex execution.

    Input delivery (audit P0-03 RESOLVED — empirically verified 2026-05-28):
    - Codex cli 0.133.0 delivers hook input via stdin (confirmed via binary
      string analysis: "failed to write hook stdin:" present; CODEX_HOOK_INPUT absent).
    - Primary: reads stdin.
    - Fallback: CODEX_HOOK_INPUT env var (for forward compatibility only; Codex
      never sets this env var as of 0.133.0).
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
        \"\"\"Read hook input from stdin or CODEX_HOOK_INPUT env var fallback.

        Empirically verified 2026-05-28 (codex-cli 0.133.0, binary string analysis of
        the Rust binary at codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex):
        Codex delivers PostToolUse hook input via stdin. CODEX_HOOK_INPUT env var
        is absent from the Codex Rust binary and is never set by Codex. The env
        var fallback is kept for forward compatibility only.

        Hook payload field names (confirmed offline from binary, 2026-05-28):
          session_id, turn_id, transcript_path, hook_event_name, model,
          permission_mode, prompt, trigger, tool_name, tool_input, tool_use_id.
        Field 'turn_id' (snake_case) is the session-correlation key. The binary also
        contains 'turnId' but that is TypeScript-layer naming; the wire protocol uses
        snake_case. CODEX_THREAD_ID is an unrelated env var (not a hook payload field).
        Source: strings analysis of hook_runtime.rs section in the Rust binary.
        \"\"\"
        stdin_val = sys.stdin.read()
        if stdin_val:
            return stdin_val
        # Fallback: CODEX_HOOK_INPUT env var (forward compatibility; Codex 0.133.0
        # does not set this — confirmed via binary string analysis 2026-05-28).
        return os.environ.get("CODEX_HOOK_INPUT", "")


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
            # turn_id confirmed field name (snake_case) from binary string analysis of
            # hook_runtime.rs section in codex-cli 0.133.0 Rust binary, 2026-05-28.
            # Defensive fallbacks kept for forward-compat with future Codex versions.
            # Worst case: null turn_id degrades correlation quality, never breaks.
            turn_id = (
                data.get("turn_id")
                or data.get("turnId")
                or data.get("thread_id")
            )
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
    rewrite_unchanged: bool = False,
) -> dict[str, Any]:
    """Install the PostToolUse hook script at target_dir/.codex/hooks/.

    Validates no {{ }} tokens before writing (audit P0-02, FR07 AC).

    An existing hook whose bytes already equal the generated script is left
    alone and reported ``outcome="preserved"``. That is what makes the caller's
    ``preserved`` bucket reachable at all: it used to be filled only when a
    write was SKIPPED, and the only production caller passed
    ``overwrite=force or True`` — always ``True`` — so no install could ever
    report it, and every run claimed ``created`` even when nothing changed.

    Note what is deliberately NOT gated: an existing hook whose bytes DIFFER is
    still rewritten. A stale hook script that stays registered is how commit
    ``1fc4be8850`` shipped a security fix that could not reach an existing
    install, and content-equality is the check that distinguishes "already
    current" from "never refreshed" without reopening that hole.

    Args:
        target_dir: Repository root (hook installed at target_dir/.codex/hooks/).
        overwrite: If False, an existing file whose content DIFFERS is left in
            place rather than refreshed.
        rewrite_unchanged: If True, rewrite even when the content is identical.

    Returns:
        Dict with keys: installed (bool), path (str), skipped (bool),
        outcome (``"created"`` | ``"updated"`` | ``"preserved"``).
    """
    hook_dir = target_dir / ".codex" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hook_dir / "trw_post_edit_telemetry.py"
    content = generate_hook_script()
    existed = hook_path.exists()

    if existed:
        try:
            current: str | None = hook_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Unreadable but present: treat as differing so a refresh is
            # attempted. "We could not check" must not degrade to "it is fine".
            #
            # UnicodeDecodeError is NOT an OSError — it subclasses ValueError —
            # so catching OSError alone left the one unreadable case this guard
            # exists for. A corrupt or partially-written hook raised out of
            # install_hook_script, the caller swallowed it into result["errors"],
            # and the stale file stayed registered in .codex/hooks.json: exactly
            # the "shipped fix cannot reach an existing install" defect
            # 1fc4be8850 fixed. Found by an independent reviewer.
            current = None
        unchanged = current == content
        if (unchanged and not rewrite_unchanged) or (not unchanged and not overwrite):
            log.debug(
                "codex_hook_install_skipped",
                path=str(hook_path),
                outcome="preserved_unchanged" if unchanged else "preserved_existing",
            )
            return {"installed": False, "path": str(hook_path), "skipped": True, "outcome": "preserved"}

    hook_path.write_text(content, encoding="utf-8")

    log.debug(
        "codex_hook_installed",
        path=str(hook_path),
        bytes_written=len(content.encode("utf-8")),
        outcome="updated" if existed else "created",
    )

    return {
        "installed": True,
        "path": str(hook_path),
        "skipped": False,
        "outcome": "updated" if existed else "created",
    }
