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
- Fail-open: exits 0; ignored tools emit no output
- No {{ }} template tokens in the installed script (FR07 AC)
- stdlib-only imports (NFR05)

PRD-DIST-2402 FR07, FR08, FR09, FR10.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import structlog

from trw_mcp._checkout_write import write_checkout_file

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


    _APPLY_PATCH_MARKERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")
    _STREAM_SOFT_BYTES = 8 * 1024 * 1024
    _STREAM_HARD_BYTES = 9 * 1024 * 1024
    _MAX_PATH_CHARS = 4096
    _MAX_FILES_PER_EDIT = 200
    _MAX_SESSION_ID_CHARS = 200


    def _patch_files(patch: str) -> list[str]:
        \"\"\"File paths an apply_patch input touches.

        An apply_patch envelope (``*** Begin Patch``) names files only through its own markers, at line start;
        a line inside it that merely looks like a diff header is added text, not a file. Outside an envelope the
        input is a unified diff and its ``+++ b/`` / ``--- a/`` headers name the files.
        \"\"\"
        enveloped = any(line.startswith("*** Begin Patch") for line in patch.splitlines())
        found: list[str] = []
        for line in patch.splitlines():
            path = ""
            if enveloped:
                for marker in _APPLY_PATCH_MARKERS:
                    if line.startswith(marker):
                        path = line[len(marker) :].strip()
            elif line.startswith(("+++ b/", "--- a/")):
                path = line[6:].strip()
            if path and len(path) <= _MAX_PATH_CHARS and path not in found:
                found.append(path)
        return found[:_MAX_FILES_PER_EDIT]


    def _open_dir_below(parent_fd: int, name: str) -> int:
        \"\"\"Open (creating if absent) directory *name* under *parent_fd*, refusing a symlink at that component.\"\"\"
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
            return os.open(name, flags, dir_fd=parent_fd)


    def _record_file_modified(repo_root: Path, tool_name: str, file_paths: list[str], session_id: str) -> None:
        \"\"\"Append one ``file_modified`` record per file to the session stream the deliver gate reads.

        Same flat shape as data/hooks/post-tool-event.sh (INC-115): without it a Codex session that
        edited files counted zero changes and trw_deliver succeeded with no build. Every component below
        the repo root is opened relative to a pinned directory descriptor with no-follow, so a path swapped for
        a symlink after a check cannot redirect the write. Between the soft and hard size cap only a
        ``change_evidence_unknown`` record is appended (the gate then fails closed instead of reading zero);
        past the hard cap nothing is. Best effort otherwise (never raises).
        \"\"\"
        if not file_paths:
            return
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        session_id = session_id[:_MAX_SESSION_ID_CHARS]
        records: list[dict[str, object]] = []
        for fp in file_paths:
            path = Path(fp)
            rel = os.path.relpath(path.parent.resolve() / path.name, repo_root.resolve()) if path.is_absolute() else fp
            records.append(
                {
                    "ts": ts,
                    "event": "file_modified",
                    "tool": "codex:" + tool_name,
                    "file": rel,
                    "session_id": session_id,
                    "pinned": False,
                }
            )
        fds: list[int] = []
        try:
            fds.append(os.open(repo_root, os.O_RDONLY | os.O_DIRECTORY))
            fds.append(_open_dir_below(fds[-1], ".trw"))
            fds.append(_open_dir_below(fds[-1], "context"))
            fds.append(
                os.open(
                    "session-events.jsonl",
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                    0o644,
                    dir_fd=fds[-1],
                )
            )
            size = os.fstat(fds[-1]).st_size
            if size >= _STREAM_HARD_BYTES:
                return
            if size >= _STREAM_SOFT_BYTES:
                records = [{"ts": ts, "event": "change_evidence_unknown", "reason": "stream_full"}]
            os.write(fds[-1], "".join(json.dumps(r) + "\\n" for r in records).encode("utf-8"))
        finally:
            for fd in reversed(fds):
                os.close(fd)


    def main() -> None:
        \"\"\"Main hook entrypoint — always exits 0.\"\"\"
        try:
            raw = _read_hook_input()
            data = json.loads(raw, strict=False)  # a raw newline inside a string must not drop the event
        except (json.JSONDecodeError, Exception):  # trw-fail-silent-allow: a hook must never break or block the client
            print(_CONTINUE_RESPONSE)
            return

        try:
            tool_name = str(data.get("tool_name", ""))
        except Exception:  # trw-fail-silent-allow: a hook must never break or block the client
            print(_CONTINUE_RESPONSE)
            return

        if tool_name not in _MATCHING_TOOLS:
            # Codex rejects suppressOutput for PostToolUse. Exit 0 with no
            # output is the supported silent-success contract:
            # https://learn.chatgpt.com/docs/hooks#common-output-fields
            return

        try:
            tool_input = data.get("tool_input", {})
            # turn_id confirmed field name (snake_case) from binary string analysis of
            # hook_runtime.rs section in codex-cli 0.133.0 Rust binary, 2026-05-28.
            # Defensive fallbacks kept for forward-compat with future Codex versions.
            # Worst case: null turn_id degrades correlation quality, never breaks.
            turn_id = data.get("turn_id") or data.get("turnId") or data.get("thread_id")
            tool_use_id = data.get("tool_use_id")

            file_paths: list[str] = []
            if isinstance(tool_input, dict):
                if tool_name == "apply_patch":
                    # The envelope arrives as `command` on the codex versions seen so far; `patch`/`input`
                    # are older or forward-compatible spellings.
                    for key in ("patch", "input", "command", "cmd"):
                        value = tool_input.get(key)
                        if isinstance(value, str) and value:
                            file_paths = _patch_files(value)
                            if file_paths:
                                break
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
            if tool_name == "apply_patch":
                _record_file_modified(
                    telemetry_path.parent.parent.parent,
                    tool_name,
                    file_paths,
                    str(data.get("session_id") or os.environ.get("TRW_SESSION_ID") or ""),
                )
        except Exception:  # trw-fail-silent-allow: telemetry and change evidence are best effort; never break the client
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
    hook_path = target_dir / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
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

    write_checkout_file(target_dir, hook_path, content)

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
