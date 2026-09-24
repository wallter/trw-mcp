"""Shared dispatch-request resolution.

Belongs to the ``trw_mcp.dispatch`` package. The CLI handler (``_cli.py``) and
the MCP tools (``trw_mcp.tools.dispatch``) must resolve a caller's loose inputs
(optional client/model/timeout/role + ``config.dispatch`` defaults) into the
SAME typed :class:`DispatchRequest`. Centralizing the precedence here is the one
source of truth — a divergence between the CLI and the MCP path would silently
apply different security/posture defaults depending on the entry point.

Precedence (highest wins):

- client: explicit ``client`` > ``dispatch_role_client[role]`` (when ``role``
  set) > ``dispatch_default_client``. ``None`` after that -> error.
- model: explicit ``model`` > ``dispatch_default_models[client]`` > the role's task-class tier
  where the client has a verified tier map (PRD-CORE-290-FR03).
- effort: explicit ``effort`` > ``dispatch_default_effort`` > the role's task-class effort.
- timeout: explicit ``timeout_s`` (not None) > ``dispatch_default_timeout_s``.
- read_only: an EXPLICIT ``read_only`` (True or False) is honored; ``None`` ->
  the ``dispatch_default_read_only`` config baseline. (The caller is responsible
  for turning an ``--allow-writes`` request into ``read_only=False``.)
- posture: taken from the caller only; there is deliberately NO config default,
  because a config that could turn any dispatch into a "reviewer" would let a
  project's own file decide that a child is contained.
- with_trw: an EXPLICIT value (True or False) is honored; ``None`` -> the
  ``dispatch_child_trw_access`` config baseline. Unlike posture, a config
  default here is safe in the one direction that matters: it can only CONNECT a
  child to TRW's own server, never claim a bound the child does not have. An
  explicit request for a client with no argv channel is REFUSED; the config
  default degrades to False for that client instead of failing the dispatch.

A resolved client absent from ``dispatch_enabled_clients`` is rejected, as is a
client whose registry entry records its capabilities as UNVERIFIED
(PRD-CORE-266-FR04). All rejection paths raise :class:`DispatchResolutionError`
carrying an ``exit_code`` so the CLI can translate it to ``sys.exit`` and the MCP
tool can surface it as a structured ``{"error", "exit_code"}`` payload.

Refusal happens HERE, before :func:`resolve_dispatch_request` returns a request
and therefore before any argv is built or any subprocess is launched. That
ordering is the point: an unverified entry carries provisional flag data recorded
for documentation, and it must never be possible for that data to reach a command
line. No fallback and no substitution is ever applied — silently answering with a
different client would answer the operator's question with a different agent's
output, which is a worse failure than refusing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast, get_args

import structlog

from trw_mcp.dispatch._client_specs import UnknownClientError, client_spec_for
from trw_mcp.dispatch._policy import operator_set, resolve_effort, resolve_max_turns, resolve_model
from trw_mcp.dispatch._posture import (
    ReviewerPostureError,
    TrwAccessError,
    verify_reviewer_posture,
    verify_trw_access,
)
from trw_mcp.dispatch._roles import apply_role
from trw_mcp.dispatch._types import DispatchPosture, DispatchRequest

if TYPE_CHECKING:
    from trw_mcp.models.config._sub_models import DispatchConfig

logger = structlog.get_logger(__name__)


class DispatchResolutionError(ValueError):
    """A dispatch request could not be resolved into a valid target.

    Carries ``exit_code`` so the CLI maps it directly to ``sys.exit`` and the MCP
    tool can echo the same code in its structured error payload. ``2`` is used
    for every resolution failure (unresolved / disabled) to mirror the CLI's
    pre-existing exit conventions.
    """

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _resolve_client(
    *,
    client: str | None,
    role: str | None,
    dispatch_cfg: object,
) -> str:
    """Resolve the target client by precedence, raising on failure.

    explicit > role mapping > default. A resolved client not in
    ``dispatch_enabled_clients`` is rejected.
    """
    default_client = getattr(dispatch_cfg, "dispatch_default_client", None)
    role_client = getattr(dispatch_cfg, "dispatch_role_client", {})

    resolved = client
    if resolved is None and role is not None and isinstance(role_client, dict):
        resolved = role_client.get(role)
    if resolved is None:
        resolved = default_client
    if resolved is None:
        raise DispatchResolutionError(
            "No dispatch client resolved: pass a client or set "
            "dispatch.default_client (or dispatch.role_client) in .trw/config.yaml.",
            exit_code=2,
        )

    resolved = str(resolved)
    enabled = getattr(dispatch_cfg, "dispatch_enabled_clients", [])
    if isinstance(enabled, list) and resolved not in enabled:
        raise DispatchResolutionError(
            f"client {resolved!r} is disabled (set dispatch.enabled_clients in .trw/config.yaml)",
            exit_code=2,
        )
    _refuse_unverified(resolved)
    return resolved


def _refuse_unverified(client: str) -> None:
    """Refuse a client TRW has not verified, naming what verification is missing.

    Runs AFTER the enabled check so an operator who never enabled the client
    still hears the accurate "disabled" answer, and BEFORE any request object
    exists so no argv can be built from provisional data.

    The message quotes the entry's own ``outstanding`` text rather than a generic
    line: telling a caller that a client is unverified without telling them what
    would settle it leaves them with no next step, and a caller with no next step
    reaches for a bypass.
    """
    try:
        spec = client_spec_for(client)
    except UnknownClientError:
        raise DispatchResolutionError(
            f"client {client!r} has no dispatch client spec registered; no substitute was applied.",
            exit_code=2,
        ) from None
    if spec.verification.method == "unverified":
        raise DispatchResolutionError(
            f"client {client!r} is UNVERIFIED: TRW has not established its capabilities, so it "
            f"will not be launched. Outstanding verification: {spec.verification.outstanding}. "
            "No default or fallback client was substituted.",
            exit_code=2,
        )


def resolve_dispatch_request(
    *,
    client: str | None,
    prompt: str,
    role: str | None,
    model: str | None,
    effort: str | None = None,
    cwd: Path | None,
    timeout_s: int | None,
    read_only: bool | None = None,
    isolate: bool,
    use_pty: bool,
    posture: str = "default",
    with_trw: bool | None = None,
    verify_sandbox: bool = False,
    dispatch_cfg: object,
) -> DispatchRequest:
    """Build a validated :class:`DispatchRequest` from loose inputs + config.

    Raises :class:`DispatchResolutionError` (``exit_code=2``) when no client
    resolves or the resolved client is disabled.
    """
    resolved_client = _resolve_client(client=client, role=role, dispatch_cfg=dispatch_cfg)

    # Model and effort: explicit request > operator config > the role's task-class
    # row (PRD-CORE-290-FR03); the winning source is recorded on the request.
    # Only what the operator set counts as "config" (DispatchConfig.operator_set).
    cfg = cast("DispatchConfig", dispatch_cfg)
    models = cfg.dispatch_default_models if operator_set(cfg, "dispatch_default_models") else None
    config_effort = cfg.dispatch_default_effort if operator_set(cfg, "dispatch_default_effort") else None
    config_turns = cfg.dispatch_default_max_turns if operator_set(cfg, "dispatch_default_max_turns") else None
    resolved_model, model_source = resolve_model(model, resolved_client, role, models)
    try:
        resolved_effort, effort_source = resolve_effort(effort, role, config_effort)
    except ValueError as exc:
        raise DispatchResolutionError(str(exc), exit_code=2) from exc
    max_turns, max_turns_source = resolve_max_turns(config_turns, role)
    logger.info(
        "dispatch_policy_resolved",
        client=resolved_client,
        role=role,
        effort=resolved_effort,
        effort_source=effort_source,
        model=resolved_model,
        model_source=model_source,
    )

    # Timeout: an explicit value (any int) wins; None -> config default.
    resolved_timeout = timeout_s
    if resolved_timeout is None:
        resolved_timeout = int(getattr(dispatch_cfg, "dispatch_default_timeout_s", 600))

    # Read-only: an explicit caller value (True or False) is AUTHORITATIVE; only
    # ``None`` falls back to the config default. This is the F-03 safety fix — an
    # explicit ``read_only=True`` must never be silently overridden by a config
    # default of False.
    if read_only is None:
        effective_read_only = bool(getattr(dispatch_cfg, "dispatch_default_read_only", True))
    else:
        effective_read_only = read_only

    resolved_posture = _resolve_posture(posture, client=resolved_client, read_only=effective_read_only)

    # TRW access: an explicit caller value (True or False) is AUTHORITATIVE; only
    # ``None`` falls back to the config default — the same precedence read_only
    # uses, so a project that turns child access on cannot silently override a
    # caller who asked for an isolated child.
    effective_with_trw = (
        bool(getattr(dispatch_cfg, "dispatch_child_trw_access", False)) if with_trw is None else with_trw
    )
    explicit = with_trw is not None
    if effective_with_trw and resolved_posture == "reviewer":
        if not explicit:
            # A project default must not fight a posture the caller chose.
            effective_with_trw = False
        else:
            raise DispatchResolutionError(
                "with_trw cannot be combined with posture='reviewer': that posture already injects "
                "TRW's MCP server, bounded to the read-only reviewer surface. Choose one.",
                exit_code=2,
            )
    if effective_with_trw:
        # Only an EXPLICIT request is refused for an unsupported client; a
        # project-wide default degrades to "no TRW access for this client", and
        # the result's ``trw_access_enforced=False`` states that truthfully
        # rather than failing a dispatch the caller never asked to change.
        try:
            verify_trw_access(resolved_client, True)
        except TrwAccessError as exc:
            if explicit:
                raise DispatchResolutionError(str(exc), exit_code=2) from exc
            effective_with_trw = False

    resolved_prompt = apply_role(role, prompt)
    resolved_cwd = _resolve_cwd(cwd, client=resolved_client)

    return DispatchRequest(
        client=resolved_client,  # type: ignore[arg-type]  # validated against the Literal by Pydantic
        prompt=resolved_prompt,
        model=resolved_model,
        model_source=model_source,
        effort=resolved_effort,  # type: ignore[arg-type]  # validated against EFFORT_LEVELS above
        effort_source=effort_source,
        max_turns=max_turns,
        max_turns_source=max_turns_source,
        cwd=resolved_cwd,
        timeout_s=int(resolved_timeout),
        read_only=effective_read_only,
        isolate=isolate,
        use_pty=use_pty,
        posture=resolved_posture,
        with_trw=effective_with_trw,
        verify_sandbox=verify_sandbox,
    )


def _resolve_cwd(cwd: Path | None, *, client: str) -> Path | None:
    """Default the working directory for a client that carries it in argv.

    For a client with a ``cwd_flag``, ``cwd=None`` does not mean "the child runs
    in our directory" -- it means the flag is never emitted, so the child is
    given no workspace at all. Measured 2026-09-16: an agy dispatch without
    ``--add-dir`` loads none of the project's instruction files and can read
    none of its code, while still exiting 0 (PRD-CORE-277-FR02). Clients with no
    ``cwd_flag`` are untouched: they inherit the process working directory
    through ``Popen(cwd=...)`` as before.
    """
    if cwd is not None:
        return cwd
    try:
        spec = client_spec_for(client)
    except UnknownClientError:  # trw-fail-silent-allow: an unregistered id is refused upstream; no cwd to default here
        return None
    return Path.cwd().resolve() if spec.cwd_flag is not None else None


def _resolve_posture(posture: str, *, client: str, read_only: bool) -> DispatchPosture:
    """Validate the requested posture for this client, or refuse with exit_code=2.

    Refusal, not degradation, for the same reason ``_refuse_unverified`` refuses:
    a caller who asked for a bounded reviewer and silently received an unbounded
    child would have no way to know the bound was missing, and would then cite
    that child's output as contained evidence. The message names the client and
    the reason so the caller has a next step other than a bypass.
    """
    postures = get_args(DispatchPosture)
    if posture not in postures:
        raise DispatchResolutionError(
            f"unknown dispatch posture {posture!r}; expected one of {', '.join(postures)}.",
            exit_code=2,
        )
    try:
        verify_reviewer_posture(client, posture, read_only=read_only)
    except ReviewerPostureError as exc:
        raise DispatchResolutionError(str(exc), exit_code=2) from exc
    return cast("DispatchPosture", posture)
