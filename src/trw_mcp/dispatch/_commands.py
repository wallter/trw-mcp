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
from trw_mcp.dispatch._types import DispatchRequest

# ``SUPPORTED_CLIENTS`` is derived from the registry key set in ``_client_specs``
# so config defaults, the env allowlist, and this builder all read ONE source.
# Re-exported here for back-compat (``_commands.SUPPORTED_CLIENTS`` and the
# package facade import it from here).
__all__ = ["SUPPORTED_CLIENTS", "UnsupportedClientError", "build_command"]


class UnsupportedClientError(ValueError):
    """Raised for a client id outside :data:`SUPPORTED_CLIENTS`."""


def build_command(req: DispatchRequest) -> list[str]:
    """Build the exact argv for *req*.

    Fragments are concatenated in a fixed order, each one supplied by the
    client's registry entry::

        base_argv always_argv structured_output_argv [isolation_argv]
        (read_only_argv | allow_writes_argv) [model_flag MODEL]
        [cwd_flag CWD] *extra_args (prompt_flag PROMPT | PROMPT)

    ``read_only`` selects between two fragments rather than adding one, which is
    why an empty ``read_only_argv`` is a posture and not a gap: for a client that
    denies writes headlessly, read-only IS the omission of ``allow_writes_argv``.

    Raises:
        UnsupportedClientError: if ``req.client`` has no registered spec. There is
            no default spec — building some other client's argv would answer the
            caller with a different agent.
    """
    try:
        spec = client_spec_for(req.client)
    except UnknownClientError as exc:  # pragma: no cover - guarded by the Literal upstream
        raise UnsupportedClientError(f"No command spec for client {req.client!r}") from exc

    argv: list[str] = [*spec.base_argv, *spec.always_argv, *spec.structured_output_argv]
    if req.isolate:
        argv += spec.isolation_argv
    argv += spec.read_only_argv if req.read_only else spec.allow_writes_argv
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
