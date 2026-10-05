"""Cross-client dispatch MCP tools (PUBLIC, BSL-1.1).

Exposes the dispatch launcher to harnesses WITHOUT a shell: an agent can ask the
MCP server to run ANOTHER coding-agent CLI for a second-opinion audit, either
synchronously (``wait=True``) or via a fire-and-poll background job
(``wait=False``, the default).

One tool, ``trw_dispatch``, with four actions (PRD-CORE-300-FR09):

- ``launch`` (default) — resolve + launch a request. Default ``wait=False``
  returns a ``job_id`` immediately; poll it with ``action="status"``.
- ``status`` — a job's status, including the redacted result once terminal.
- ``evidence`` / ``validate_evidence`` — export or check an AgentWorkEvidence v1
  record (:mod:`trw_mcp.tools.agent_work_evidence`).

Redaction: the raw ``prompt`` argument is the caller's own input — it is accepted
but NEVER echoed back in the tool return or logged. The :class:`DispatchResult`
and job record only carry the prompt-redacted ``argv_redacted``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.dispatch._child_marker import dispatched_child_active
from trw_mcp.dispatch._codex_observed import with_observed
from trw_mcp.dispatch._jobs import _TERMINAL_STATUSES, get_status, start_background
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request, uncommitted_work_warning
from trw_mcp.dispatch._runner import dispatch as _dispatch_child
from trw_mcp.dispatch._targets import Target, TargetError, list_clients, parse_targets, resolve_role, variant_lanes
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult
from trw_mcp.dispatch._usage import record_child_usage, record_dispatch_policy
from trw_mcp.models.config import get_config
from trw_mcp.tools._dispatch_fanout import launch_fanout, status_many
from trw_mcp.tools.agent_work_evidence import export_evidence, validate_evidence

logger = structlog.get_logger(__name__)

# Upper bound on a synchronous ``wait=True`` dispatch. A shell-less harness that
# blocks the MCP request thread for minutes can stall the whole server; longer
# dispatches MUST use the background (wait=False) + poll path instead.
_MAX_WAIT_TIMEOUT_S = 120
# PRD-CORE-355: a synchronous call must not hold its MCP request thread on a busy cap; it gets
# ``concurrency_cap`` back after this long (the CLI and background jobs keep the configured wait).
_SYNC_SLOT_WAIT_S = 10.0


def dispatch(req: DispatchRequest) -> DispatchResult:
    """Run *req* synchronously for the MCP tool (single and fan-out lanes), with the short slot wait."""
    return _dispatch_child(req, slot_wait_s=_SYNC_SLOT_WAIT_S)


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
    # No fallback chain ran: its two fields carry nothing a caller acts on. Same
    # for FR08's pair when nothing was measured (e.g. read_only=False, or a
    # client/isolate row the FR08 probe table doesn't cover) — an empty tuple
    # and an empty note are the "nothing to report" case, not signal.
    exclude: set[str] = set()
    if not result.attempts:
        exclude |= {"attempts", "fallback_note"}
    if not result.enforcement_layers and not result.mcp_role_note:
        exclude |= {"enforcement_layers", "mcp_role_note"}
    if not result.posture_note:  # only a posture the client could not carry is signal
        exclude.add("posture_note")
    payload = result.model_dump(mode="json", exclude=exclude or None)
    try:
        if result.ok and not verbose:
            # ``structured`` is the client's raw JSON envelope (usage, ids,
            # thinking); ``text`` already carries the answer.
            payload.pop("structured", None)
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


_ACTIONS = ("launch", "status", "clients", "evidence", "validate_evidence")


def _refuse(message: str) -> dict[str, object]:
    return {"error": message, "exit_code": 2}


def _credential_status() -> dict[str, object]:
    """``action="status"`` with no job id: each OAuth client's login and credential lock (PRD-CORE-304-FR04)."""
    from trw_mcp.dispatch._credentials import credential_report

    status, message, rows = credential_report()
    return {"status": status.lower(), "credentials": rows, "summary": message}


def _status(job_id: str, verbose: bool) -> dict[str, object]:
    """A job's status; its result once terminal and the child wrote one, else None."""
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
        prompt: str | list[str] = "",
        action: str = "launch",
        client: str | None = None,
        role: str | None = None,
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
        target: str = "",
        ctx: Context | None = None,
    ) -> dict[str, object]:
        """Run a prompt on other agent CLIs ({clients}), one or many at once.
        Use when you want codex/agy/grok/claude to review, critique, plan or implement.
        client: "codex", "grok:grok-4.7" (client:model), or a comma list to fan out;
        action="clients" lists names. prompt: as-is; a list runs each variant (x clients).
        role: optional preamble preset (implement writes). wait=True answers inline
        (<=120s); else poll action="status" with the job id(s).

        Output: fan-out {results:[{target, ok, text}]}; single {status, result}; job_id(s); or error.

        Args:
            action: launch | status | clients | evidence | validate_evidence
                (target: job id(s), run path or JSON; bare status = OAuth logins).
            posture: "reviewer" asks for the read-only TRW surface, best effort
                (see posture_note); "reviewer!" refuses if it cannot hold.
            with_trw: give the child a TRW session (claude, codex).
            model/effort: request overrides config; Codex otherwise uses its client defaults.
            verbose: raw streams on success; for evidence, events + schema.
        """
        # A bare status reports each OAuth client's credential state (PRD-CORE-304-FR04).
        if action == "status":
            if not target:
                return _credential_status()
            ids = [t.strip() for t in target.split(",") if t.strip()]
            return _status(ids[0], verbose) if len(ids) == 1 else status_many(ids)
        if action == "clients":
            return list_clients(dict(get_config().dispatch.dispatch_default_models or {}))
        if action == "evidence":
            return export_evidence(ctx, target or None, include_events=verbose, include_schema=verbose)
        if action == "validate_evidence":
            return (
                validate_evidence(target) if target else _refuse("action='validate_evidence' needs the JSON in target")
            )
        if action != "launch":
            return _refuse(f"unknown action {action!r}; valid actions: {', '.join(_ACTIONS)}")
        prompts = [p for p in ([prompt] if isinstance(prompt, str) else prompt) if p]
        if not prompts:
            return _refuse("action='launch' needs prompt")
        # The modes above launch nothing, so they run anywhere; everything below launches.
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

        try:
            targets = parse_targets(client, None)  # a model rides in the client: "grok:grok-4.7"
            role, role_writes = resolve_role(role)
        except TargetError as err:
            return _refuse(str(err))
        if role_writes is False and read_only is None and not allow_writes:
            read_only = True  # a preset's default only: a role never refuses the caller's own choice
        if role_writes and read_only:  # never let a preset silently widen an explicit read_only=True
            return _refuse("role='implement' needs writes but read_only=True was passed; drop one of them")
        if role_writes:
            allow_writes = True
        if wait and timeout_s is None:
            timeout_s = _MAX_WAIT_TIMEOUT_S  # an inline wait defaults to the cap instead of refusing

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
            target_cwd = resolved_cwd.resolve()
            if not target_cwd.is_relative_to(project_root):
                return {
                    "error": (
                        f"cwd must be within the project root ({project_root}) when writes are enabled; got {target_cwd}"
                    ),
                    "exit_code": 2,
                }

        def _resolve(t: Target | None) -> DispatchRequest:
            return resolve_dispatch_request(
                client=(t.client or None) if t else None,
                prompt=prompts[max(t.variant, 1) - 1] if t else prompts[0],
                role=role,
                model=t.model if t else None,
                effort=effort or None,  # "" = not requested; the smallest schema under the signature budget
                cwd=resolved_cwd,
                timeout_s=timeout_s,
                read_only=resolved_read_only,
                isolate=isolate,
                use_pty=False,  # MCP launches no PTY; the CLI keeps --pty (PRD-CORE-290-FR03 budget)
                posture=posture.rstrip("!"),
                require_posture=posture.endswith("!"),  # "reviewer!" = required; one parameter, not two
                with_trw=with_trw,
                dispatch_cfg=dispatch_cfg,
            )

        # DISPATCH-DELTA-LOW: a writable child in a tree with uncommitted work is warned about, not refused.
        default_ro = bool(getattr(dispatch_cfg, "dispatch_default_read_only", True))
        writes = not (default_ro if resolved_read_only is None else resolved_read_only)
        warning = uncommitted_work_warning(resolved_cwd, writes=writes)

        def _warned(out: dict[str, object]) -> dict[str, object]:
            return {**out, "warning": warning} if warning else out

        lanes = variant_lanes(targets, None, len(prompts))
        if len(lanes) > 1:
            return _warned(launch_fanout(lanes, _resolve, wait=wait))

        try:
            req = _resolve(targets[0] if targets else None)
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
                        "use wait=False (background) + action='status' for longer dispatches"
                    ),
                    "exit_code": 2,
                }
            child_id = f"sync-{uuid.uuid4().hex}"
            policy = record_dispatch_policy(req, child_id)  # PRD-CORE-290-FR03
            result = dispatch(req)
            record_child_usage(result, child_id=child_id)  # PRD-CORE-290-FR01
            policy = with_observed(policy, req.client, result.raw_stdout)  # CODEX-P0-A S2
            return _warned(
                {
                    "job_id": None,
                    "status": "succeeded" if result.ok else "failed",
                    "policy": policy,
                    "result": _result_payload_capped(result, verbose=verbose),
                }
            )

        job = start_background(req)
        return _warned(
            {
                "job_id": job.job_id,
                "status": job.status,
                "client": job.client,
                "argv_redacted": job.argv_redacted,
                "policy": record_dispatch_policy(req, job.job_id),
            }
        )
