"""Per-client command (argv) builder for the dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. ``build_command`` is a function with no I/O, subprocess or PATH lookup that turns a
:class:`DispatchRequest` into the exact ``list[str]`` argv to execute. The prompt
is always passed as a single argv token — never shell-interpolated — so a
malicious prompt body cannot break out into shell metacharacters.

This module holds NO per-client knowledge (PRD-CORE-266-FR02). Every flag,
subcommand and binary name lives in ``_client_specs.CLIENT_SPECS``; the builder
only knows the ORDER the fragments are concatenated in. Adding a client is a data
entry there and no edit here, and there is no ``req.client == ...`` comparison
left to grow a second branch.

Default-posture command construction is deterministic, which lets the recorded baselines in
``tests/fixtures/dispatch_argv_baseline.json`` prove the registry migration was
behaviour-preserving. Injected Codex MCP transports are the deliberate exception:
the renderer generates a fresh server ID so project config cannot pre-populate
its env, cwd or tool filters through Codex's recursive configuration merge.
"""

from __future__ import annotations

from trw_mcp.dispatch._client_spec_types import EFFORT_LEVELS, ClientSpec
from trw_mcp.dispatch._client_specs import (
    SUPPORTED_CLIENTS,
    UnknownClientError,
    client_spec_for,
)
from trw_mcp.dispatch._posture import REVIEWER_POSTURE, render_reviewer_argv, render_trw_access_argv
from trw_mcp.dispatch._types import DispatchRequest

# ``SUPPORTED_CLIENTS`` is derived from the registry key set in ``_client_specs``
# so config defaults, the env allowlist, and this builder all read ONE source.
# Re-exported here for back-compat (``_commands.SUPPORTED_CLIENTS`` and the
# package facade import it from here).
__all__ = ["SUPPORTED_CLIENTS", "UnsupportedClientError", "build_command"]


class UnsupportedClientError(ValueError):
    """Raised for a client id outside :data:`SUPPORTED_CLIENTS`."""


def _client_effort(spec: ClientSpec, req: DispatchRequest) -> str | None:
    """The effort value to put on *spec*'s command line, or ``None`` to pass nothing.

    Nothing is passed when the request carries no effort, when the client documents
    no effort flag, or when the explicit model is a Haiku model: Haiku accepts no
    effort parameter at all, so sending one is an error rather than a no-op. A level
    the client does not accept is CLAMPED DOWN to the strongest level it does accept
    (``xhigh`` on a ``low|medium|high`` client runs at ``high``) -- the same clamp
    TRW's effort adapter applies, and never upward, so a request is not silently
    made more expensive than it asked for.
    """
    if req.effort is None or spec.effort_flag is None:
        return None
    if req.model and "haiku" in req.model.lower():
        return None
    ceiling = EFFORT_LEVELS.index(req.effort)
    supported = [level for level in spec.effort_levels if EFFORT_LEVELS.index(level) <= ceiling]
    return supported[-1] if supported else None


def build_command(req: DispatchRequest, *, confined: bool = False) -> list[str]:
    """Build the exact argv for *req*.

    Fragments are concatenated in a fixed order, each one supplied by the
    client's registry entry::

        base_argv always_argv structured_output_argv
        (reviewer_argv_template | trw_access_argv_template | [isolation_argv])
        (read_only_argv | allow_writes_argv) [model_flag MODEL]
        [effort_flag EFFORT] [cwd_flag CWD] *extra_args (prompt_flag PROMPT | PROMPT)

    ``EFFORT`` is :func:`_client_effort`'s client-specific value for the request's
    portable effort; a client with no documented flag gets none.

    ``read_only`` selects between two fragments rather than adding one, which is
    why an empty ``read_only_argv`` is a posture and not a gap: for a client that
    denies writes headlessly, read-only IS the omission of ``allow_writes_argv``.

    ``confined`` (keyword-only, default False) is the caller's ASSERTION that it
    has built a host write-denial wrapper around this child, and it is the only
    condition under which ``confined_read_only_argv`` is emitted. It is a
    parameter rather than a probe because command construction does no I/O: the
    same (request, confined) pair produces the same default-posture argv, which is what
    the recorded baselines in ``tests/fixtures/dispatch_argv_baseline.json``
    pin. The runner computes it (``_confine.confinement_prefix``); nothing else
    may pass True, and the runner REFUSES a read-only run for a host-confinement
    client when no wrapper exists (that client's bare read-only flag denies reads,
    and its read-enabling flag is safe only inside the wrapper).

    ``posture='reviewer'`` selects the rendered reviewer template INSTEAD of
    ``isolation_argv``, never in addition to it: both fragments configure the
    child's MCP wiring, and emitting claude's empty ``--mcp-config`` beside the
    reviewer one, or codex's ``--ignore-user-config`` beside the ``-c`` overrides
    it is measured to conflict with (L-VupD), would produce an ambiguous command
    line rather than a stronger one. The sandbox flag is NOT part of the template
    — it stays in ``read_only_argv``, its single source, and a reviewer request
    is validated read-only upstream, so a reviewer argv still carries it exactly
    once.

    ``with_trw=True`` selects the rendered TRW-access template in the SAME
    mutually-exclusive slot, for the same reason: both fragments decide which
    MCP servers the child sees, and emitting one beside ``isolation_argv``
    (claude's empty ``--mcp-config``, codex's ``--ignore-user-config``) would
    produce an ambiguous command line rather than a stronger one. The request
    model refuses ``with_trw`` together with ``posture='reviewer'``, so the two
    branches can never both apply.

    Raises:
        UnsupportedClientError: if ``req.client`` has no registered spec. There is
            no default spec — building some other client's argv would answer the
            caller with a different agent.
        ReviewerPostureError: if ``posture='reviewer'`` and this client cannot
            carry TRW's MCP server in its argv. Raising here (rather than
            degrading to isolation_argv) is what keeps an unsupported posture
            from becoming a silently unbounded run.
        TrwAccessError: if ``with_trw=True`` and this client exposes no argv
            channel for an MCP transport. Degrading to ``isolation_argv`` would
            hand the caller a child with no TRW tools while the request said it
            had them.
    """
    try:
        spec = client_spec_for(req.client)
    except UnknownClientError as exc:  # pragma: no cover - guarded by the Literal upstream
        raise UnsupportedClientError(f"No command spec for client {req.client!r}") from exc

    argv: list[str] = [*spec.base_argv, *spec.always_argv, *spec.structured_output_argv]
    if req.posture == REVIEWER_POSTURE:
        argv += render_reviewer_argv(spec)
    elif req.with_trw:
        argv += render_trw_access_argv(spec)
    elif req.isolate:
        argv += spec.isolation_argv
    if req.read_only:
        argv += spec.read_only_argv
        if confined:
            argv += spec.confined_read_only_argv
    else:
        argv += spec.allow_writes_argv
    if spec.model_flag is not None and req.model:
        argv += [spec.model_flag, req.model]
    effort = _client_effort(spec, req)
    if effort is not None and spec.effort_flag is not None:
        argv += [spec.effort_flag, effort]
    if spec.max_turns_flag is not None and req.max_turns is not None:
        argv += [spec.max_turns_flag, str(req.max_turns)]
    if spec.cwd_flag is not None and req.cwd is not None:
        argv += [spec.cwd_flag, str(req.cwd)]
    argv += list(req.extra_args)
    if spec.prompt_flag is not None:
        argv += [spec.prompt_flag, req.prompt]
    else:
        argv.append(req.prompt)
    return argv
