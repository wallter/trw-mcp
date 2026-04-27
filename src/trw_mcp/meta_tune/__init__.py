"""Meta-tune safety gates package (PRD-HPO-SAFE-001)."""

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
