"""Meta-tune safety gates package (PRD-HPO-SAFE-001).

The MCP server imports ``trw_mcp.meta_tune.boot_checks`` during startup.
Keep this package initializer side-effect free so that boot-check imports do
not eagerly load the full dispatch/sandbox stack before configuration and
server modules finish initializing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time only typing aid
    from trw_mcp.meta_tune.dispatch import DispatchResult, promote_candidate
    from trw_mcp.meta_tune.errors import (
        KillSwitchNotFoundError,
        MetaTuneBootValidationError,
        MetaTuneSafetyUnavailableError,
    )
    from trw_mcp.meta_tune.sandbox import (
        ProbeIsolationContext,
        SandboxResult,
        SandboxRunner,
        run_sandboxed,
    )

__all__ = [
    "DispatchResult",
    "KillSwitchNotFoundError",
    "MetaTuneBootValidationError",
    "MetaTuneSafetyUnavailableError",
    "ProbeIsolationContext",
    "SandboxResult",
    "SandboxRunner",
    "promote_candidate",
    "run_sandboxed",
]


def __getattr__(name: str) -> Any:
    """Lazily expose legacy package-level conveniences.

    Historically this module imported dispatch, errors, and sandbox eagerly.
    That made a clean ``import trw_mcp.server`` fail with a circular import:
    server -> meta_tune.boot_checks -> package __init__ -> dispatch ->
    boot_checks/errors -> telemetry -> state._paths -> models -> scoring ->
    state._paths.  Lazy exports preserve the public convenience API without
    making server startup pay for unrelated meta-tune modules.
    """
    if name in {"DispatchResult", "promote_candidate"}:
        from trw_mcp.meta_tune.dispatch import DispatchResult, promote_candidate

        return {"DispatchResult": DispatchResult, "promote_candidate": promote_candidate}[name]
    if name in {"KillSwitchNotFoundError", "MetaTuneBootValidationError", "MetaTuneSafetyUnavailableError"}:
        from trw_mcp.meta_tune.errors import (
            KillSwitchNotFoundError,
            MetaTuneBootValidationError,
            MetaTuneSafetyUnavailableError,
        )

        return {
            "KillSwitchNotFoundError": KillSwitchNotFoundError,
            "MetaTuneBootValidationError": MetaTuneBootValidationError,
            "MetaTuneSafetyUnavailableError": MetaTuneSafetyUnavailableError,
        }[name]
    if name in {"ProbeIsolationContext", "SandboxResult", "SandboxRunner", "run_sandboxed"}:
        from trw_mcp.meta_tune.sandbox import ProbeIsolationContext, SandboxResult, SandboxRunner, run_sandboxed

        return {
            "ProbeIsolationContext": ProbeIsolationContext,
            "SandboxResult": SandboxResult,
            "SandboxRunner": SandboxRunner,
            "run_sandboxed": run_sandboxed,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
