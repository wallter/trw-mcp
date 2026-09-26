"""Subprocess runner for the dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. ``dispatch`` is the single I/O
entry point: it builds the argv (``_commands``), the sanitized env (``_env``),
runs the child with ``shell=False`` under a hard timeout, and returns a
normalized, prompt-redacted :class:`DispatchResult`.

Security posture:
- ``shell=False`` + argv list — the prompt is a single token, never shell-parsed.
- Sanitized allowlisted env — no wholesale ``os.environ`` to the child.
- The prompt body is redacted in the logged/returned argv.
- The child runs in its own process group (``start_new_session``). Abnormal
  exits attempt a verified group kill, then reap the direct child with a bounded
  wait. Escaped descendants are not guaranteed to terminate.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import structlog

from trw_mcp.dispatch._capability import unadvertised_flags
from trw_mcp.dispatch._child_marker import dispatched_child_active
from trw_mcp.dispatch._client_specs import client_spec_for
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._env import build_subprocess_env
from trw_mcp.dispatch._host_confinement import _confinement_for, _needs_host_confinement, _read_only_enforced
from trw_mcp.dispatch._isolated import IsolationFailedError, run_isolated
from trw_mcp.dispatch._normalize import classify_silence, normalize_output, turn_cap_next_read
from trw_mcp.dispatch._posture import (
    ISOLATED_REVIEW_POSTURE,
    ReviewerPostureError,
    TrwAccessError,
    reviewer_posture_enforced,
    trw_access_enforced,
    verify_reviewer_posture,
    verify_trw_access,
)
from trw_mcp.dispatch._process_identity import capture_identity, signal_group
from trw_mcp.dispatch._sandbox_probe import SandboxProbe, probe_write_containment
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


def _kill_tree(proc: subprocess.Popen[str], identity: dict[str, str | int] | None = None) -> None:
    """Request a verified group kill, falling back to killing the direct child.

    Missing or mismatched identity refuses GROUP signaling — deliberately, since
    signaling a process group we cannot prove is ours could hit a recycled pid.
    But a refusal used to end the story, and then NOTHING was killed: a slow
    ``ps`` during identity capture, an unverifiable identity on a platform whose
    probe returns None, or a pid that outran its record all left the timed-out
    child running while the caller was told the tree had been killed.

    So a refusal now falls back to ``proc.kill()``. That is strictly narrower
    than the group kill — it reaches the child we spawned and nothing else,
    which is precisely the process we can always prove is ours — and it restores
    the guarantee the timeout path advertises. Escaped grandchildren remain
    possible when the group signal is refused; the bounded drain below reports
    that case rather than hiding it.

    Wrapped so a race where the process already exited (``ProcessLookupError``)
    is benign — there is nothing left to kill.
    """
    try:
        if _POSIX and signal_group(identity, signal.SIGKILL):
            return
        proc.kill()
    except ProcessLookupError:  # pragma: no cover  # trw-fail-silent-allow: child already exited
        return
    except OSError as exc:
        logger.warning("dispatch_child_kill_failed", pid=proc.pid, reason=type(exc).__name__)


def _finish_child(proc: subprocess.Popen[str], identity: dict[str, str | int] | None, *, terminate: bool) -> str:
    """Release direct-child ownership without an unbounded pipe drain.

    This also runs when identity capture or the PID callback raises. Unexpected
    dispatch exceptions remain exceptions; cleanup failures are logged, and on
    returning paths also appended to stderr. No escaped-descendant claim is made.
    """
    failures: list[str] = []
    try:
        if terminate:
            _kill_tree(proc, identity)
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"direct child not reaped ({type(exc).__name__})")
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError as exc:
                    failures.append(f"pipe close failed ({type(exc).__name__})")
    if failures:
        note = "Dispatch cleanup incomplete: " + "; ".join(failures)
        logger.warning("dispatch_cleanup_incomplete", pid=proc.pid, detail=note)
        return note
    return ""


def _binary_resolves(binary: str, cwd: Path | None) -> bool:
    """True if *binary* would resolve the way ``Popen`` resolves it.

    Three details, each one a measured wrong answer from the naive version:
    ``Path(binary).is_file()`` accepts a NON-EXECUTABLE file (it accepted
    ``CLAUDE.md``); a relative path resolves against the CHILD's working
    directory, not ours; and only a bare name goes through ``PATH``.
    """
    if os.sep in binary or binary.startswith("."):
        candidate = Path(binary)
        if not candidate.is_absolute() and cwd is not None:
            candidate = cwd / candidate
        return candidate.is_file() and os.access(candidate, os.X_OK)
    if shutil.which(binary) is not None:
        return True
    # A RELATIVE entry on PATH ("bin", or the empty string) resolves against the
    # CHILD's working directory, which ``shutil.which`` cannot do from here. This
    # check is a diagnostic that improves an error code, never a permission
    # decision, so when it cannot answer it says yes and lets ``Popen`` be the
    # authority — refusing a client that would have launched is the worse error.
    return any(entry == "" or not os.path.isabs(entry) for entry in os.get_exec_path())


def _redact_argv(argv: list[str], prompt: str) -> list[str]:
    """Return *argv* with any token equal to *prompt* replaced by a placeholder."""
    placeholder = _PROMPT_PLACEHOLDER.format(n=len(prompt))
    return [placeholder if tok == prompt else tok for tok in argv]


def _wrap_pty(argv: list[str]) -> list[str]:
    """Wrap *argv* in ``script`` for a pseudo-TTY.

    util-linux ``script`` (Linux) takes the command as one ``-c`` string, so the
    argv is shlex-joined and the prompt token survives intact; BSD ``script``
    (macOS) has no ``-c`` and takes the command argv directly after the log file.
    ANSI introduced by the PTY is stripped during normalization.
    """
    if sys.platform == "darwin":
        return ["script", "-q", "/dev/null", *argv]
    inner = shlex.join(argv)
    return ["script", "-qec", inner, "/dev/null"]


def dispatch(
    req: DispatchRequest,
    *,
    pid_callback: Callable[[int], None] | None = None,
    _lane_home: Path | None = None,
) -> DispatchResult:
    """Run *req* and return a normalized :class:`DispatchResult`.

    Never raises for an expected outcome: a timeout sets ``timed_out=True`` /
    ``exit_code=None`` (and group termination is attempted); a non-zero exit is
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
    # Nested-launch guard, second layer (PRD-CORE-281-FR07). ``trw_dispatch``
    # refuses first; this covers every OTHER in-process caller on a server started
    # for a dispatched child (``trw_review``'s cross-model path calls this function
    # directly). The detached job runner and a shell-run CLI are not affected: the
    # subprocess env allowlist never forwards a ``TRW_*`` name to them.
    if dispatched_child_active():
        logger.warning("dispatch_nested_launch_refused", client=req.client)
        return _early_result(
            req,
            [],
            exit_code=-1,
            stderr="nested dispatch is refused: this TRW server was started for a dispatched child",
        )
    if req.posture == ISOLATED_REVIEW_POSTURE and _lane_home is None:
        return _enter_isolated_lane(req, pid_callback)
    # Posture is settled BEFORE any argv exists and therefore before any spawn.
    # DispatchRequest already refuses reviewer+writes at construction; this second
    # check costs nothing and covers a request reconstructed by another path (a
    # model_construct, a hand-built job file), so no route to Popen skips it.
    confine_argv, confine_note = _confinement_for(req, _lane_home)
    # The probe runs BEFORE the child it describes, which is what the field
    # claims and what an operator needs: a verdict that arrives after the run it
    # was meant to bound has already finished is a post-mortem, not a control.
    sandbox_verdict, sandbox_note = _sandbox_claim(req, confine_note)
    if req.read_only and not confine_argv and _needs_host_confinement(req):
        # AGY-SANDBOX: this client's bare read-only flag denies reads and its
        # read-enabling flag is safe only inside the wrapper, so without one there
        # is no read-only run that is both usable and contained: refuse, never widen.
        return _early_result(req, [], exit_code=2, stderr=f"read-only dispatch to {req.client} refused: {confine_note}")
    try:
        verify_reviewer_posture(req.client, req.posture, read_only=req.read_only)
        if req.posture == ISOLATED_REVIEW_POSTURE and not (_lane_home and confine_argv):
            # Only the lane (``_isolated.run_isolated``) may spawn this posture, and
            # only confined: an empty prefix is a refusal, never an unconfined run.
            raise ReviewerPostureError("posture='isolated-review' runs only inside its confined snapshot lane")
        if req.posture == ISOLATED_REVIEW_POSTURE and req.with_trw:
            raise ReviewerPostureError("posture='isolated-review' refuses with_trw: it would add an MCP server")
        verify_trw_access(req.client, req.with_trw)
        argv = build_command(req, confined=bool(confine_argv))
    except ReviewerPostureError as exc:
        logger.warning("dispatch_posture_refused", client=req.client, posture=req.posture, error=str(exc))
        return _early_result(req, [], exit_code=-1, stderr=f"reviewer posture refused: {exc}")
    except TrwAccessError as exc:
        # Same shape as the posture refusal and for the same reason: a request
        # built by another path (model_construct, a hand-written job file) must
        # not reach Popen with ``with_trw`` silently dropped, because the caller
        # would then read a TRW-less child's answer as a TRW-connected one.
        logger.warning("dispatch_trw_access_refused", client=req.client, error=str(exc))
        return _early_result(req, [], exit_code=-1, stderr=f"trw access refused: {exc}")
    _warn_trw_access_config_residue(req)
    # Redact the prompt on the bare argv BEFORE PTY-wrapping, then wrap the
    # redacted form for display — otherwise the prompt would survive inside the
    # `script -qec` inner string and leak into logs / argv_redacted.
    redacted_base = _redact_argv(argv, req.prompt)
    # Resolve the CLIENT binary here, before any wrapper is applied. Wrapped,
    # a missing binary is no longer an OSError out of Popen -- `script` and
    # `sandbox-exec` both spawn fine and exit with their own status -- so the
    # -127 "Failed to launch" contract every caller reads would silently become
    # an anonymous non-zero exit. Checking after `_wrap_pty` asked whether
    # `script` exists, which is never the question. Only the wrapped paths are
    # checked, so an unwrapped launch still reports through Popen as before.
    if (confine_argv or req.use_pty) and not _binary_resolves(argv[0], req.cwd):
        return _early_result(
            req,
            redacted_base,
            exit_code=-127,
            stderr=f"Failed to launch {req.client!r}: {argv[0]!r} not found on PATH",
        )
    if req.use_pty:
        run_argv = _wrap_pty(argv)
        argv_redacted = _wrap_pty(redacted_base)
    else:
        run_argv = argv
        argv_redacted = redacted_base
    if confine_argv:
        # The wrapper goes OUTSIDE the PTY wrapper as well: it must be the ancestor
        # of every process in the tree, or a grandchild escapes the denial.
        run_argv = [*confine_argv, *run_argv]
        argv_redacted = [*confine_argv, *argv_redacted]
    env = build_subprocess_env(req.client, posture=req.posture, with_trw=req.with_trw)
    if _lane_home is not None:
        env["HOME"] = str(_lane_home)

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
    # Before any child runs: an installed CLI that lacks a flag TRW passes it
    # either rejects the argv or (the copilot shim) answers exit 0 with an
    # install prompt that would read as ok=True.
    unsupported = unadvertised_flags(
        client_spec_for(req.client),
        redacted_base,
        read_only=req.read_only,
        extra_args=req.extra_args,
        env=env,
        cwd=req.cwd,
    )
    if unsupported is not None:
        reason, message = unsupported
        logger.warning("dispatch_client_unsupported", client=req.client, silence_reason=reason, detail=message)
        return _early_result(req, argv_redacted, exit_code=-1, stderr=message, silence_reason=reason)

    if not req.read_only:
        logger.warning(
            "dispatch_writes_enabled",
            client=req.client,
            detail="read_only=False — the child agent may modify files",
        )

    logger.info(
        "dispatch_start",
        client=req.client,
        posture=req.posture,
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
            # PRD-CORE-277-FR01. Every registered client takes its prompt from argv,
            # so no child needs stdin -- and inheriting it is actively harmful: inside
            # a stdio MCP server the parent's fd 0 is the client's open JSON-RPC pipe,
            # where `codex exec` prints "Reading additional input from stdin..." and
            # blocks until the dispatch times out. The background path
            # (``_jobs.py``) has always passed DEVNULL; this is the same contract for
            # the synchronous one. Under ``use_pty`` it is ``script`` that receives
            # DEVNULL and the child still gets its pseudo-terminal.
            stdin=subprocess.DEVNULL,
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

    identity = None
    communicated = False
    try:
        identity = capture_identity(proc.pid) if _POSIX else None
        if pid_callback is not None:
            pid_callback(proc.pid)
        try:
            raw_stdout, raw_stderr = proc.communicate(timeout=req.timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc, identity)
            # Bounded drain: escaped descendants may still hold these pipes.
            try:
                raw_stdout, raw_stderr = proc.communicate(timeout=5)
                communicated = True
            except subprocess.TimeoutExpired:
                raw_stdout, raw_stderr = "", "Dispatch cleanup incomplete; process may still be running."
                logger.warning("dispatch_cleanup_incomplete", pid=proc.pid)
            timed_out = True
            exit_code = None
        else:
            communicated = True
            exit_code = proc.returncode
    finally:
        cleanup_note = _finish_child(proc, identity, terminate=not communicated)
    if cleanup_note:
        raw_stderr = f"{raw_stderr or ''}\n{cleanup_note}".strip()
        if not timed_out:
            exit_code = -1

    raw_stdout = _cap_output(raw_stdout or "")
    raw_stderr = _cap_output(raw_stderr or "")

    duration_s = time.monotonic() - start
    text, structured = normalize_output(req.client, raw_stdout)
    silence_reason = classify_silence(
        text=text,
        raw_stderr=raw_stderr,
        structured=structured,
        exit_code=exit_code,
        timed_out=timed_out,
        # A pty has ONE stream: under --pty the child's stderr arrives inside
        # stdout and proc.stderr is empty, so the stderr rules would inspect
        # nothing. Hand the merged stream over for that launch shape alone.
        merged_stderr=raw_stdout if req.use_pty else "",
        prompt=req.prompt,
    )
    next_read = turn_cap_next_read(req, raw_stderr if not req.use_pty else raw_stdout)
    if next_read:
        silence_reason = "turn_cap_reached"

    result = DispatchResult(
        client=req.client,
        argv_redacted=argv_redacted,
        read_only_enforced=_read_only_enforced(req, confine_argv),
        posture=req.posture,
        posture_enforced=reviewer_posture_enforced(req.client, req.posture),
        trw_access_enforced=trw_access_enforced(req.client, req.with_trw),
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration_s,
        text=text,
        raw_stdout=raw_stdout,
        raw_stderr=raw_stderr,
        structured=structured,
        sandbox_verified=sandbox_verdict,
        sandbox_note=sandbox_note,
        silence_reason=silence_reason,
        next_read=next_read,
    )

    logger.info(
        "dispatch_complete",
        client=req.client,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=round(duration_s, 3),
        ok=result.ok,
        silence_reason=silence_reason,
        sandbox_verified=sandbox_verdict,
        text_chars=len(text),
    )
    return result


def _enter_isolated_lane(req: DispatchRequest, pid_callback: Callable[[int], None] | None) -> DispatchResult:
    """Admit, then run *req* in a standalone snapshot (PRD-CORE-297-FR04); refusals never spawn."""
    try:
        verify_reviewer_posture(req.client, req.posture, read_only=req.read_only)
        return run_isolated(req, lambda lane_req, home: dispatch(lane_req, pid_callback=pid_callback, _lane_home=home))
    except ReviewerPostureError as exc:
        logger.warning("dispatch_posture_refused", client=req.client, posture=req.posture, error=str(exc))
        return _early_result(req, [], exit_code=-1, stderr=f"reviewer posture refused: {exc}")
    except IsolationFailedError as exc:
        logger.warning("dispatch_isolation_failed", client=req.client, error=str(exc))
        return _early_result(req, [], exit_code=-1, stderr=str(exc))


def _sandbox_claim(req: DispatchRequest, confine_note: str) -> tuple[bool | Literal["unverified"], str]:
    """Decide what this run may claim about write containment.

    Without ``verify_sandbox`` the answer is always ``"unverified"`` plus whatever
    the confinement step observed: TRW may report the MECHANISM it applied, but a
    mechanism is not a measurement, and reporting one as the other is the defect
    this field exists to prevent.

    A run that ASKED for writes is never certified, probe or no probe. The probe
    would be measuring a child launched with ``allow_writes_argv``, and a caller
    reading ``sandbox_verified: true`` beside ``read_only_enforced: false`` would
    have every reason to believe the writes had been contained. No model call is
    spent to learn what the request already said.
    """
    if not req.read_only:
        return "unverified", "writes were requested (read_only=False); no containment claim is made"
    if not req.verify_sandbox:
        return "unverified", confine_note or "no probe requested; no containment claim made"
    try:
        probe: SandboxProbe = probe_write_containment(
            req.client,
            run=lambda probe_req: dispatch(probe_req),
            model=req.model,
            timeout_s=req.timeout_s,
            mechanism=confine_note or "the client's own read-only flags",
            # The verdict has to describe THIS launch shape. A probe that dropped the
            # PTY wrapper, the posture, the isolation flag or the extra tokens would
            # certify a command line the caller never ran.
            read_only=req.read_only,
            use_pty=req.use_pty,
            isolate=req.isolate,
            posture=req.posture,
            extra_args=req.extra_args,
        )
    except OSError as exc:
        # The probe builds a fixture on disk. Now that it runs BEFORE the child,
        # a full disk or an unwritable temp dir would abort the caller's actual
        # task -- for a side question. Report the failure as the absence of a
        # claim and carry on.
        logger.warning("sandbox_probe_setup_failed", client=req.client, error=str(exc))
        return "unverified", f"the write-containment probe could not run ({exc}); no claim is made"
    return probe.verdict, probe.note


def _warn_trw_access_config_residue(req: DispatchRequest) -> None:
    """Log what a ``with_trw`` launch stopped isolating, when it stopped isolating it.

    PRD-CORE-281-FR04. ``trw_access_enforced=True`` says TRW's own server reached
    the child; for codex it ALSO means ``--ignore-user-config`` was displaced, so
    the child reads the user's and the project's codex config after all. Leaving
    that in a source comment made it true and unreadable at the same time — the
    operator weighing a with_trw dispatch never sees the module.

    A WARNING, not a debug line: under the shipped default install the effective
    level is INFO, so a debug event here would exist only for someone who had
    already opted into verbose logging — i.e. not for the run that needed it. It
    stays out of ``DispatchResult`` on the response-token rule; the string is
    registry DATA (``ClientSpec.trw_access_config_residue``) and is reachable
    from there by anyone rendering the client table.
    """
    if not req.with_trw:
        return
    # No except-and-return here, deliberately: this runs only AFTER
    # ``verify_trw_access`` accepted the client and ``build_command`` built its
    # argv, both of which refuse an unregistered id, so the lookup cannot fail.
    # Catching it would add an unreachable silent branch to satisfy a shape.
    spec = client_spec_for(req.client)
    if spec.trw_access_config_residue:
        logger.warning(
            "dispatch_trw_access_config_residue",
            client=req.client,
            residue=spec.trw_access_config_residue,
        )


def _early_result(
    req: DispatchRequest,
    argv_redacted: list[str],
    *,
    exit_code: int,
    stderr: str,
    silence_reason: str = "nonzero_exit",
) -> DispatchResult:
    """Build a clean failure result for a pre-spawn / launch failure (no child).

    ``posture_enforced`` and ``trw_access_enforced`` are False on every one of
    these paths by construction: no child was launched, so nothing was bounded
    and nothing was connected. Reporting the spec's capability here would claim
    containment -- or a TRW connection -- for a process that never existed.

    ``read_only_enforced`` is False for the same reason: a wrapper prefix that
    was found but never ran enforced nothing (a missing agy binary once
    reported enforced containment here).
    """
    return DispatchResult(
        client=req.client,
        argv_redacted=argv_redacted,
        read_only_enforced=False,
        posture=req.posture,
        posture_enforced=False,
        trw_access_enforced=False,
        exit_code=exit_code,
        timed_out=False,
        duration_s=0.0,
        text="",
        raw_stdout="",
        raw_stderr=stderr,
        structured=None,
        silence_reason=silence_reason,
    )
