"""Per-client command (argv) builder for the dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. ``build_command`` is a *pure*
function (no I/O, no subprocess, no PATH lookup) that turns a
:class:`DispatchRequest` into the exact ``list[str]`` argv to execute. The prompt
is always passed as a single argv token — never shell-interpolated — so a
malicious prompt body cannot break out into shell metacharacters.

This module holds NO per-client knowledge (PRD-CORE-266-FR02). Every flag,
subcommand and binary name lives in ``_client_specs.CLIENT_SPECS``; the builder
only knows the ORDER the fragments are concatenated in. Adding a client is a data
entry there and no edit here, and there is no ``req.client == ...`` comparison
left to grow a second branch.

Purity is load-bearing rather than stylistic: it is what makes the same request
produce the same argv on every box, and what lets the 64 recorded baselines in
``tests/fixtures/dispatch_argv_baseline.json`` prove the registry migration was
behaviour-preserving.
"""

from __future__ import annotations

from trw_mcp.dispatch._client_specs import (
    SUPPORTED_CLIENTS,
    UnknownClientError,
    client_spec_for,
)
from trw_mcp.dispatch._posture import REVIEWER_POSTURE, render_reviewer_argv
from trw_mcp.dispatch._types import DispatchRequest

# ``SUPPORTED_CLIENTS`` is derived from the registry key set in ``_client_specs``
# so config defaults, the env allowlist, and this builder all read ONE source.
# Re-exported here for back-compat (``_commands.SUPPORTED_CLIENTS`` and the
# package facade import it from here).
__all__ = ["SUPPORTED_CLIENTS", "UnsupportedClientError", "build_command"]


class UnsupportedClientError(ValueError):
    """Raised for a client id outside :data:`SUPPORTED_CLIENTS`."""


def build_command(req: DispatchRequest, *, confined: bool = False) -> list[str]:
    """Build the exact argv for *req*.

    Fragments are concatenated in a fixed order, each one supplied by the
    client's registry entry::

        base_argv always_argv structured_output_argv
        (reviewer_argv_template | [isolation_argv])
        (read_only_argv | allow_writes_argv) [model_flag MODEL]
        [cwd_flag CWD] *extra_args (prompt_flag PROMPT | PROMPT)

    ``read_only`` selects between two fragments rather than adding one, which is
    why an empty ``read_only_argv`` is a posture and not a gap: for a client that
    denies writes headlessly, read-only IS the omission of ``allow_writes_argv``.

    ``confined`` (keyword-only, default False) is the caller's ASSERTION that it
    has built a host write-denial wrapper around this child, and it is the only
    condition under which ``confined_read_only_argv`` is emitted. It is a
    parameter rather than a probe because this function must stay pure: the same
    (request, confined) pair produces the same argv on every box, which is what
    the recorded baselines in ``tests/fixtures/dispatch_argv_baseline.json``
    pin. The runner computes it (``_confine.confinement_prefix``); nothing else
    may pass True.

    ``posture='reviewer'`` selects the rendered reviewer template INSTEAD of
    ``isolation_argv``, never in addition to it: both fragments configure the
    child's MCP wiring, and emitting claude's empty ``--mcp-config`` beside the
    reviewer one, or codex's ``--ignore-user-config`` beside the ``-c`` overrides
    it is measured to conflict with (L-VupD), would produce an ambiguous command
    line rather than a stronger one. The sandbox flag is NOT part of the template
    — it stays in ``read_only_argv``, its single source, and a reviewer request
    is validated read-only upstream, so a reviewer argv still carries it exactly
    once.

    Raises:
        UnsupportedClientError: if ``req.client`` has no registered spec. There is
            no default spec — building some other client's argv would answer the
            caller with a different agent.
        ReviewerPostureError: if ``posture='reviewer'`` and this client cannot
            carry TRW's MCP server in its argv. Raising here (rather than
            degrading to isolation_argv) is what keeps an unsupported posture
            from becoming a silently unbounded run.
    """
    try:
        spec = client_spec_for(req.client)
    except UnknownClientError as exc:  # pragma: no cover - guarded by the Literal upstream
        raise UnsupportedClientError(f"No command spec for client {req.client!r}") from exc

    argv: list[str] = [*spec.base_argv, *spec.always_argv, *spec.structured_output_argv]
    if req.posture == REVIEWER_POSTURE:
        argv += render_reviewer_argv(spec)
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
    if spec.cwd_flag is not None and req.cwd is not None:
        argv += [spec.cwd_flag, str(req.cwd)]
    argv += list(req.extra_args)
    if spec.prompt_flag is not None:
        argv += [spec.prompt_flag, req.prompt]
    else:
        argv.append(req.prompt)
    return argv
