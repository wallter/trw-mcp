"""Subprocess runner for the dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. ``dispatch`` is the single I/O
entry point: it builds the argv (``_commands``), the sanitized env (``_env``),
runs the child with ``shell=False`` under a hard timeout, and returns a
normalized, prompt-redacted :class:`DispatchResult`.

Security posture:
- ``shell=False`` + argv list — the prompt is a single token, never shell-parsed.
- Sanitized allowlisted env — no wholesale ``os.environ`` to the child.
- The prompt body is redacted in the logged/returned argv.
- The child runs in its own process group (``start_new_session``) so a timeout
  kills the WHOLE tree — including the PTY ``script`` grandchild and any
  subprocesses the agent spawned — leaving no orphans.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections.abc import Callable

import structlog

from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._env import build_subprocess_env
from trw_mcp.dispatch._normalize import normalize_output
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult

logger = structlog.get_logger(__name__)

# How the prompt body is detected for redaction in argv: it is the request's
# exact prompt string, replaced wherever it appears as a standalone token.
_PROMPT_PLACEHOLDER = "<prompt:{n} chars>"

# Cap on the child output we RETURN to the caller: we keep at most this many
# chars of each stream and mark the truncation, so a runaway child cannot bloat
# the tool response / caller context.
# NOTE: this does NOT bound the server's peak memory — `_cap_output` runs after
# `proc.communicate()` has already buffered the full stream in memory. Bounding
# peak memory would need an incremental capped read + kill-on-overflow; tracked
# as a P2 follow-up (release-verify 2026-07-17). Do not rely on this cap for
# memory-exhaustion defense until that lands.
_MAX_OUTPUT_CHARS = 10_000_000

# POSIX gate: only platforms with process-group primitives get the
# new-session + killpg tree-kill path; elsewhere we fall back to a child kill.
_POSIX = hasattr(os, "killpg") and hasattr(os, "setsid")


def _cap_output(text: str) -> str:
    """Truncate *text* to ``_MAX_OUTPUT_CHARS``, appending a marker if cut."""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    dropped = len(text) - _MAX_OUTPUT_CHARS
    return text[:_MAX_OUTPUT_CHARS] + f"\n…[truncated {dropped} chars]"


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill *proc* and its entire process group (POSIX) or just the child.

    Wrapped so a race where the process already exited (``ProcessLookupError``)
    is benign — there is nothing left to kill.
    """
    try:
        if _POSIX:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - exercised only on non-POSIX platforms
            proc.kill()
    except (ProcessLookupError, OSError):  # pragma: no cover - benign race
        pass


def _redact_argv(argv: list[str], prompt: str) -> list[str]:
    """Return *argv* with any token equal to *prompt* replaced by a placeholder."""
    placeholder = _PROMPT_PLACEHOLDER.format(n=len(prompt))
    return [placeholder if tok == prompt else tok for tok in argv]


def _wrap_pty(argv: list[str]) -> list[str]:
    """Wrap *argv* in ``script -qec '<cmd>' /dev/null`` for a pseudo-TTY.

    The inner command is shlex-quoted so the prompt token (already a single
    argv element) survives ``script``'s single-string command argument intact.
    ANSI introduced by the PTY is stripped during normalization.
    """
    inner = shlex.join(argv)
    return ["script", "-qec", inner, "/dev/null"]


def dispatch(
    req: DispatchRequest,
    *,
    pid_callback: Callable[[int], None] | None = None,
) -> DispatchResult:
    """Run *req* and return a normalized :class:`DispatchResult`.

    Never raises for an expected outcome: a timeout sets ``timed_out=True`` /
    ``exit_code=None`` (and the whole process tree is killed); a non-zero exit is
    reported as-is; a missing/un-spawnable binary returns ``exit_code=-127``; an
    invalid ``cwd`` returns ``exit_code=-1`` without spawning.

    ``pid_callback`` (optional, keyword-only): when provided, it is invoked with
    the spawned child's OS pid IMMEDIATELY after :class:`subprocess.Popen`
    succeeds. The background job path uses this to record the foreign-agent
    session-leader pid in a sidecar file so a later cancel can reach the whole
    foreign tree (the child runs with ``start_new_session`` in its OWN session).
    When ``pid_callback is None`` (the CLI / synchronous / ``_run_job``-less
    callers) the behavior is byte-identical to before — nothing else changes.
    """
    argv = build_command(req)
    # Redact the prompt on the bare argv BEFORE PTY-wrapping, then wrap the
    # redacted form for display — otherwise the prompt would survive inside the
    # `script -qec` inner string and leak into logs / argv_redacted.
    redacted_base = _redact_argv(argv, req.prompt)
    if req.use_pty:
        run_argv = _wrap_pty(argv)
        argv_redacted = _wrap_pty(redacted_base)
    else:
        run_argv = argv
        argv_redacted = redacted_base
    env = build_subprocess_env(req.client)

    # Validate cwd in the RUNNER (not just the CLI) so the future MCP path is
    # protected too: a non-directory cwd would make subprocess raise.
    if req.cwd is not None and not req.cwd.is_dir():
        return _early_result(
            req,
            argv_redacted,
            exit_code=-1,
            stderr=f"cwd is not a directory: {req.cwd}",
        )
    cwd = str(req.cwd) if req.cwd is not None else None

    if not req.read_only:
        logger.warning(
            "dispatch_writes_enabled",
            client=req.client,
            detail="read_only=False — the child agent may modify files",
        )

    logger.info(
        "dispatch_start",
        client=req.client,
        argv=argv_redacted,
        timeout_s=req.timeout_s,
        use_pty=req.use_pty,
        read_only=req.read_only,
        isolate=req.isolate,
    )

    start = time.monotonic()
    timed_out = False
    exit_code: int | None
    try:
        proc = subprocess.Popen(
            run_argv,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
            start_new_session=_POSIX,
        )
    except OSError as exc:
        # Missing binary / not executable / permission denied: a clean failure,
        # never an exception out of dispatch.
        logger.warning("dispatch_launch_failed", client=req.client, error=str(exc))
        return _early_result(
            req,
            argv_redacted,
            exit_code=-127,
            stderr=f"Failed to launch {req.client!r}: {exc}",
        )

    if pid_callback is not None:
        pid_callback(proc.pid)

    try:
        raw_stdout, raw_stderr = proc.communicate(timeout=req.timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        # Drain whatever the (now dead) tree already produced.
        raw_stdout, raw_stderr = proc.communicate()
        timed_out = True
        exit_code = None
    else:
        exit_code = proc.returncode

    raw_stdout = _cap_output(raw_stdout or "")
    raw_stderr = _cap_output(raw_stderr or "")

    duration_s = time.monotonic() - start
    text, structured = normalize_output(req.client, raw_stdout)

    result = DispatchResult(
        client=req.client,
        argv_redacted=argv_redacted,
        read_only_enforced=req.read_only,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration_s,
        text=text,
        raw_stdout=raw_stdout,
        raw_stderr=raw_stderr,
        structured=structured,
    )

    logger.info(
        "dispatch_complete",
        client=req.client,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=round(duration_s, 3),
        ok=result.ok,
        text_chars=len(text),
    )
    return result


def _early_result(
    req: DispatchRequest,
    argv_redacted: list[str],
    *,
    exit_code: int,
    stderr: str,
) -> DispatchResult:
    """Build a clean failure result for a pre-spawn / launch failure (no child)."""
    return DispatchResult(
        client=req.client,
        argv_redacted=argv_redacted,
        read_only_enforced=req.read_only,
        exit_code=exit_code,
        timed_out=False,
        duration_s=0.0,
        text="",
        raw_stdout="",
        raw_stderr=stderr,
        structured=None,
    )
