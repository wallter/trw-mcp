"""Cross-client dispatch MCP tools (PUBLIC, BSL-1.1).

Exposes the dispatch launcher to harnesses WITHOUT a shell: an agent can ask the
MCP server to run ANOTHER coding-agent CLI for a second-opinion audit, either
synchronously (``wait=True``) or via a fire-and-poll background job
(``wait=False``, the default).

Two tools are registered:

- ``trw_dispatch`` — resolve + launch a request. Default ``wait=False`` returns a
  ``job_id`` immediately; poll ``trw_dispatch_status``.
- ``trw_dispatch_status`` — return a job's status, including the redacted result
  once terminal.

Redaction: the raw ``prompt`` argument is the caller's own input — it is accepted
but NEVER echoed back in the tool return or logged. The :class:`DispatchResult`
and job record only carry the prompt-redacted ``argv_redacted``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import structlog
from fastmcp import FastMCP

from trw_mcp.dispatch._child_marker import dispatched_child_active
from trw_mcp.dispatch._jobs import _TERMINAL_STATUSES, get_status, start_background
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchResult
from trw_mcp.dispatch._usage import record_child_usage, record_dispatch_policy
from trw_mcp.models.config import get_config

logger = structlog.get_logger(__name__)

# Upper bound on a synchronous ``wait=True`` dispatch. A shell-less harness that
# blocks the MCP request thread for minutes can stall the whole server; longer
# dispatches MUST use the background (wait=False) + poll path instead.
_MAX_WAIT_TIMEOUT_S = 120

# Cap raw stdout/stderr returned THROUGH MCP (the on-disk result file keeps the
# full streams). A multi-MB raw stream would bloat the tool response / context.
_MAX_RETURNED_STREAM_CHARS = 50_000
_STREAM_TRUNCATION_MARKER = "...[truncated; full output in result file]"


def _truncate_stream(value: str) -> str:
    """Truncate a raw stream to the MCP return cap, appending a marker if cut."""
    if len(value) <= _MAX_RETURNED_STREAM_CHARS:
        return value
    return value[:_MAX_RETURNED_STREAM_CHARS] + _STREAM_TRUNCATION_MARKER


def _result_payload_capped(
    result: DispatchResult,
    *,
    verbose: bool = False,
    result_path: str | None = None,
) -> dict[str, object]:
    """Serialize a DispatchResult for MCP return, compact-by-default.

    On SUCCESS (``result.ok``) the raw ``raw_stdout``/``raw_stderr`` streams are
    OMITTED from the returned payload: they are pure diagnostic duplication of
    the already-normalized ``text``/``structured`` fields, yet each can reach the
    50k cap (~25k tokens combined) and is re-paid on every re-poll of a terminal
    job. The full, untruncated streams still persist to the on-disk result file
    for a background job (referenced via ``raw_streams_result_file`` when known),
    so nothing is lost. ``raw_streams_omitted=True`` marks the compaction.

    On FAILURE (``not result.ok`` — timeout, nonzero exit, or empty answer) the
    full capped streams are RETAINED, since that is exactly when a caller needs
    the raw diagnostics. The 50k truncation cap still applies.

    ``verbose=True`` restores the legacy full shape (capped streams always
    present) regardless of ``ok``. Only the RETURNED payload is shaped; ``text``
    and ``structured`` are kept intact and the on-disk result file is untouched.

    Fail-open: if the success-path omission somehow raises, fall back to the full
    capped shape so a result is never lost.
    """
    payload = result.model_dump(mode="json")
    try:
        if result.ok and not verbose:
            payload.pop("raw_stdout", None)
            payload.pop("raw_stderr", None)
            payload["raw_streams_omitted"] = True
            if result_path is not None:
                payload["raw_streams_result_file"] = result_path
            return payload
    except Exception:  # justified: fail-open, response shaping must never lose a result
        logger.debug("dispatch_stream_omit_failed", exc_info=True)
    payload["raw_stdout"] = _truncate_stream(result.raw_stdout)
    payload["raw_stderr"] = _truncate_stream(result.raw_stderr)
    return payload


def register_dispatch_tools(server: FastMCP) -> None:
    """Register the cross-client dispatch MCP tools."""

    def _with_client_list(func: Callable[..., object]) -> Callable[..., object]:
        """Fill ``{clients}`` in the docstring from the DispatchClient registry.

        The list was restated in prose, and prose does not move when a registry
        does: the description still advertised four clients after three more were
        added (cursor-cli, copilot, grok), and that string is what a model reads
        to choose one. Rendering it means adding a client cannot leave the
        description stale, and the assertion lives in a test.

        ``replace``, not ``format``: format would parse the WHOLE docstring, so
        any other brace raises KeyError during registration -- i.e. the server
        would not boot, a worse failure than the staleness this fixes.
        """
        from typing import get_args

        from trw_mcp.dispatch._client_specs import DispatchClient

        if func.__doc__:
            func.__doc__ = func.__doc__.replace("{clients}", ", ".join(get_args(DispatchClient)))
        return func

    @server.tool(output_schema=None)
    @_with_client_list
    def trw_dispatch(
        prompt: str,
        client: str | None = None,
        role: str | None = None,
        model: str | None = None,
        effort: str = "",
        timeout_s: int | None = None,
        read_only: bool | None = None,
        allow_writes: bool = False,
        cwd: str | None = None,
        isolate: bool = True,
        posture: str = "default",
        with_trw: bool | None = None,
        wait: bool = False,
        verbose: bool = False,
    ) -> dict[str, object]:
        """Delegate a prompt to a sub-agent CLI in {clients}.
        Use when you need an independent agent's review. Async by default
        (job_id; poll trw_dispatch_status), or wait=True (<=120s) inline.
        Read-only unless allow_writes=True.

        Output: job_id+status to poll, or an inline result if wait=True; error+exit_code if rejected.

        Args:
            prompt: instruction for the child; never echoed back.
            posture: "reviewer" limits the child to nine read-only TRW
                tools; excludes allow_writes.
            with_trw: gives the child a TRW session on this project (claude,
                codex only); excludes posture="reviewer".
            verbose: raw streams on success.
        """
        # Nested-launch guard (PRD-CORE-281): FIRST, ahead of config, so a server
        # started for a dispatched child does nothing at all on this path. Without
        # it a read-only with_trw child could start a grandchild with writes on.
        if dispatched_child_active():
            logger.warning("dispatch_tool_nested_launch_refused", client=client)
            return {
                "error": (
                    "nested dispatch is refused: this TRW server was started for a dispatched "
                    "child. No process was launched."
                ),
                "exit_code": 2,
            }

        dispatch_cfg = get_config().dispatch

        # F-07 (light cwd traversal guard): reject a '..' path COMPONENT before
        # resolution; full project-root confinement is a documented follow-up.
        from pathlib import Path

        resolved_cwd: Path | None = None
        if cwd:
            if ".." in Path(cwd).parts:
                return {"error": "cwd must not contain '..'", "exit_code": 2}
            resolved_cwd = Path(cwd)

        # PRD-SEC-015-FR13: a REVIEWER-role server refuses to widen a grandchild.
        # trw_dispatch is already outside REVIEWER_TOOLS, so the middleware denies
        # it first — this is the second layer, because a later surface change or a
        # tool_resolution_mode="all" misconfiguration must not silently restore the
        # escape the external audit measured (row 9): a child confined by --sandbox
        # read-only reaching this tool could spawn a grandchild with
        # --sandbox workspace-write. Refused before resolution, so no subprocess.
        from trw_mcp.state._surface_role import reviewer_role_active

        if allow_writes and reviewer_role_active():
            logger.warning("dispatch_tool_reviewer_allow_writes_refused", client=client, surface_role="reviewer")
            return {
                "error": (
                    "allow_writes is refused under surface_role='reviewer': a reviewer lane may not "
                    "spawn a writable agent. No process was launched."
                ),
                "exit_code": 2,
            }

        # Same argument, second escape (PRD-CORE-281-FR02): a bounded reviewer
        # that could hand a grandchild an UNBOUNDED TRW connection would have
        # laundered its own read-only tool surface through a child process.
        if with_trw and reviewer_role_active():
            logger.warning("dispatch_tool_reviewer_with_trw_refused", client=client, surface_role="reviewer")
            return {
                "error": (
                    "with_trw is refused under surface_role='reviewer': a reviewer lane may not give "
                    "a child an unbounded TRW surface. No process was launched."
                ),
                "exit_code": 2,
            }

        # F-03: an explicit read_only is honored; allow_writes=True forces writes
        # (read_only=False); None defers to dispatch_default_read_only (True).
        # This parameter used to default to True, so the resolver's config branch
        # — and the config field itself — were unreachable from MCP: an operator
        # setting dispatch_default_read_only had no effect here. Same behavior
        # under default config, honest under a customized one.
        resolved_read_only: bool | None = False if allow_writes else read_only

        # F-07 (full cwd confinement): a WRITES-enabled dispatch must not run with
        # cwd pointing outside the project tree (e.g. /etc). Reads are lower-risk
        # and unaffected. allow_writes=True is the only explicit write signal we
        # can see here without re-resolving config, so confine on it.
        if allow_writes and resolved_cwd is not None:
            from trw_mcp.state._paths import resolve_trw_dir

            project_root = resolve_trw_dir().parent.resolve()
            target = resolved_cwd.resolve()
            if not target.is_relative_to(project_root):
                return {
                    "error": (
                        f"cwd must be within the project root ({project_root}) when writes are enabled; got {target}"
                    ),
                    "exit_code": 2,
                }

        try:
            req = resolve_dispatch_request(
                client=client,
                prompt=prompt,
                role=role,
                model=model,
                effort=effort or None,  # "" = not requested; the smallest schema under the signature budget
                cwd=resolved_cwd,
                timeout_s=timeout_s,
                read_only=resolved_read_only,
                isolate=isolate,
                use_pty=False,  # MCP launches no PTY; the CLI keeps --pty (PRD-CORE-290-FR03 budget)
                posture=posture,
                with_trw=with_trw,
                dispatch_cfg=dispatch_cfg,
            )
        except DispatchResolutionError as err:
            logger.info("dispatch_tool_resolution_error", error=str(err), exit_code=err.exit_code)
            return {"error": str(err), "exit_code": err.exit_code}

        if wait:
            # F-02: a synchronous wait must stay short — a multi-minute blocking
            # call stalls the MCP request thread. Longer dispatches use wait=False.
            if req.timeout_s > _MAX_WAIT_TIMEOUT_S:
                return {
                    "error": (
                        f"wait=True is only supported for timeout_s<= {_MAX_WAIT_TIMEOUT_S}s; "
                        "use wait=False (background) + trw_dispatch_status for longer dispatches"
                    ),
                    "exit_code": 2,
                }
            child_id = f"sync-{uuid.uuid4().hex}"
            policy = record_dispatch_policy(req, child_id)  # PRD-CORE-290-FR03
            result = dispatch(req)
            record_child_usage(result, child_id=child_id)  # PRD-CORE-290-FR01
            return {
                "job_id": None,
                "status": "succeeded" if result.ok else "failed",
                "policy": policy,
                "result": _result_payload_capped(result, verbose=verbose),
            }

        job = start_background(req)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "client": job.client,
            "argv_redacted": job.argv_redacted,
            "policy": record_dispatch_policy(req, job.job_id),
        }

    @server.tool(output_schema=None)
    def trw_dispatch_status(job_id: str, verbose: bool = False) -> dict[str, object]:
        """Poll a job from trw_dispatch(wait=False). Use when checking if a
        background dispatch has finished.

        Output: job_id + status + terminal (stop polling when true); result is
        present once terminal AND the child wrote one, else None.

        Args:
            verbose: include full raw streams on success.
        """
        try:
            job = get_status(job_id)
        except (KeyError, ValueError):
            return {"error": f"unknown job_id {job_id!r}"}

        # Terminal set is imported, not restated: the hand-copied tuple here
        # omitted "cancelled", so a job cancelled AFTER its child had already
        # written a result reported result=None forever — real evidence on disk,
        # discarded because a poller's local list had drifted from the registry's.
        # ``terminal`` is returned so a caller knows when to stop polling without
        # keeping its own copy of the same set.
        terminal = job.status in _TERMINAL_STATUSES
        result_payload: dict[str, object] | None = None
        if terminal:
            from trw_mcp.dispatch._jobs import get_result

            result = get_result(job_id)
            if result is not None:
                record_child_usage(result, child_id=job_id)  # PRD-CORE-290-FR01; a re-poll counts once
                result_payload = _result_payload_capped(result, verbose=verbose, result_path=job.result_path)

        return {
            "job_id": job.job_id,
            "status": job.status,
            "terminal": terminal,
            "policy": job.policy,
            "result": result_payload,
        }
