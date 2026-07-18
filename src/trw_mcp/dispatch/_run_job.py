"""Detached module-entry runner for a single background dispatch job.

Belongs to the ``trw_mcp.dispatch`` package. Invoked as::

    python -m trw_mcp.dispatch._run_job <req_json_path> <result_json_path> <child_pid_path>

by :func:`trw_mcp.dispatch._jobs.start_background` as a DETACHED child. It reads
a :class:`DispatchRequest` from ``req_json_path``, runs it through the (synchronous,
never-raises-for-expected-outcomes) :func:`dispatch` runner, and writes the
resulting :class:`DispatchResult` JSON to ``result_json_path``.

It also passes a ``pid_callback`` into :func:`dispatch` that records the foreign
agent's session-leader pid into ``child_pid_path`` (atomically). This sidecar
lets :func:`trw_mcp.dispatch._jobs.cancel_job` reach the foreign tree — the
foreign agent runs in its OWN session (``start_new_session``) and would otherwise
survive a cancel that only kills this intermediate's process group. A failure to
write the sidecar is swallowed: the job still completes; only cancel-reach is lost.

The existence of the result file is the job-registry's completion signal — so the
write is atomic (temp file + ``os.replace``) to ensure a poller never reads a
half-written result. The request file is deleted in all terminal paths after it
is no longer needed, independently of whether the parent ever polls the job.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from trw_mcp.dispatch._private_io import write_private_atomic
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchClient, DispatchRequest, DispatchResult


def _client_from_req(req_path: Path) -> DispatchClient:
    """Best-effort recover the client from a (possibly malformed) request file.

    Used only to label a FAILURE result when the normal path raised. Falls back
    to ``codex`` (a valid Literal member) when the request cannot be parsed — the
    failure result's value is the ``raw_stderr`` reason, not the client.
    """
    try:
        req = DispatchRequest.model_validate_json(req_path.read_text(encoding="utf-8"))
    except Exception:  # any parse/IO failure -> default label
        return "codex"
    return req.client


def _failure_result(req_path: Path, exc: BaseException) -> DispatchResult:
    """Build a minimal ok=False result describing an unexpected runner failure."""
    return DispatchResult(
        client=_client_from_req(req_path),
        argv_redacted=[],
        read_only_enforced=True,
        exit_code=-1,
        timed_out=False,
        duration_s=0.0,
        text="",
        raw_stdout="",
        raw_stderr=f"_run_job failed: {exc}",
        structured=None,
    )


def main(argv: list[str] | None = None) -> int:
    """Run one dispatch job described by JSON files. Returns a process exit code.

    Robustness (F-10): ANY exception in the body still writes a minimal ok=False
    result file so the job registry detects ``failed`` with a reason — never a
    bare crash that leaves the job hanging in ``running`` until its pid dies.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print(
            "usage: python -m trw_mcp.dispatch._run_job <req_json> <result_json> <child_pid_path>",
            file=sys.stderr,
        )
        return 2

    req_path = Path(args[0])
    result_path = Path(args[1])
    child_pid_path = Path(args[2])

    def _record_child_pid(pid: int) -> None:
        # Best-effort: a sidecar write failure must NOT crash the job. The job
        # still completes; only a later cancel won't reach the foreign tree.
        try:
            write_private_atomic(child_pid_path, str(pid))
        except OSError:
            pass

    try:
        try:
            req = DispatchRequest.model_validate_json(req_path.read_text(encoding="utf-8"))
            result = dispatch(req, pid_callback=_record_child_pid)
            write_private_atomic(result_path, result.model_dump_json(indent=2))
            return 0
        except Exception as exc:  # intentional catch-all: must emit a result, never crash
            write_private_atomic(result_path, _failure_result(req_path, exc).model_dump_json(indent=2))
            return 1
    finally:
        with contextlib.suppress(OSError):
            req_path.unlink()


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
