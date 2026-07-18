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

import structlog
from fastmcp import FastMCP

from trw_mcp.dispatch._jobs import get_status, start_background
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchResult
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

    @server.tool(output_schema=None)
    def trw_dispatch(
        prompt: str,
        client: str | None = None,
        role: str | None = None,
        model: str | None = None,
        timeout_s: int | None = None,
        read_only: bool = True,
        allow_writes: bool = False,
        cwd: str | None = None,
        isolate: bool = True,
        use_pty: bool = False,
        wait: bool = False,
        verbose: bool = False,
    ) -> dict[str, object]:
        """Dispatch a prompt to another coding-agent CLI for a second opinion.

        Use when you (a shell-less harness) want an independent agent
        (claude / codex / agy / opencode) to review or answer something. The
        target client, model, timeout, and read-only posture all fall back to
        ``.trw/config.yaml`` ``dispatch.*`` defaults when omitted.

        By default (``wait=False``) this returns IMMEDIATELY with a ``job_id``
        and ``status="running"`` — the child runs detached in the background.
        Poll ``trw_dispatch_status(job_id)`` until the status is terminal
        (succeeded / failed / timed_out / cancelled). Pass ``wait=True`` only for
        short prompts to run synchronously and get the result inline.

        Args:
            prompt: The instruction/question for the child agent. Never echoed
                back in logs or the return payload.
            client: Target CLI (claude/codex/agy/opencode). None -> config default.
            role: Optional read-only audit role preamble (e.g. "adversarial-audit").
            model: Optional model override. None -> per-client config default.
            timeout_s: Hard wall-clock timeout. None -> config default.
            read_only: Forbid the child from writing (default True).
            allow_writes: Authoritatively enable writes (overrides read_only).
                When True, ``cwd`` (if given) must be within the project root.
            cwd: Working directory for the child. None -> current directory.
            isolate: Isolate the child from host config/hooks/MCP (default True).
            use_pty: Wrap in a pseudo-TTY for clients that drop non-TTY stdout.
            wait: Run synchronously and return the result inline (default False).
            verbose: On a successful (``ok=True``) result, include the full
                (capped) ``raw_stdout``/``raw_stderr`` streams. By default they
                are omitted on success (they duplicate ``text``/``structured``);
                failure results always carry the capped streams. Default False.

        Returns:
            wait=False: {"job_id", "status": "running", "client", "argv_redacted"}.
            wait=True:  {"job_id": None, "status": "succeeded"|"failed", "result": {...}}.
                On a successful result the ``result`` omits ``raw_stdout``/``raw_stderr``
                (marked ``raw_streams_omitted=True``) unless ``verbose=True``.
            On a resolution error: {"error": str, "exit_code": int}.
        """
        dispatch_cfg = get_config().dispatch

        # F-07 (light cwd traversal guard): reject a '..' path COMPONENT before
        # resolution; full project-root confinement is a documented follow-up.
        from pathlib import Path

        resolved_cwd: Path | None = None
        if cwd:
            if ".." in Path(cwd).parts:
                return {"error": "cwd must not contain '..'", "exit_code": 2}
            resolved_cwd = Path(cwd)

        # F-03: an explicit read_only is honored; allow_writes=True forces writes
        # (read_only=False). Otherwise leave read_only=None so the resolver applies
        # the config default rather than this signature's True default silently
        # overriding a config default of False.
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
                cwd=resolved_cwd,
                timeout_s=timeout_s,
                read_only=resolved_read_only,
                isolate=isolate,
                use_pty=use_pty,
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
            result = dispatch(req)
            return {
                "job_id": None,
                "status": "succeeded" if result.ok else "failed",
                "result": _result_payload_capped(result, verbose=verbose),
            }

        job = start_background(req)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "client": job.client,
            "argv_redacted": job.argv_redacted,
        }

    @server.tool(output_schema=None)
    def trw_dispatch_status(job_id: str, verbose: bool = False) -> dict[str, object]:
        """Poll a background dispatch job started by ``trw_dispatch(wait=False)``.

        Use when you called ``trw_dispatch(wait=False)`` and need to check
        whether the background job has finished.

        Returns the job's current status; when the status is terminal
        (succeeded / failed / timed_out) the redacted :class:`DispatchResult` is
        included under ``result``. While the job is still ``running`` (or it was
        ``cancelled`` with no result), ``result`` is None.

        On a successful (``ok=True``) terminal result the ``raw_stdout``/
        ``raw_stderr`` streams are omitted (marked ``raw_streams_omitted=True``,
        with ``raw_streams_result_file`` pointing at the on-disk result JSON that
        still holds the full streams) so a re-poll does not re-pay their token
        cost. A failed result keeps the capped streams. ``verbose=True`` includes
        the full capped streams even on success.

        Args:
            job_id: The id returned by ``trw_dispatch(wait=False)``.
            verbose: Include the full capped raw streams even on a successful
                result (default False omits them on success).

        Returns:
            {"job_id", "status", "result": {...}|None}, or
            {"error": "unknown job_id ..."} if the id is not known.
        """
        try:
            job = get_status(job_id)
        except (KeyError, ValueError):
            return {"error": f"unknown job_id {job_id!r}"}

        result_payload: dict[str, object] | None = None
        if job.status in ("succeeded", "failed", "timed_out"):
            from trw_mcp.dispatch._jobs import get_result

            result = get_result(job_id)
            if result is not None:
                result_payload = _result_payload_capped(result, verbose=verbose, result_path=job.result_path)

        return {
            "job_id": job.job_id,
            "status": job.status,
            "result": result_payload,
        }
