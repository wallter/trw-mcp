"""Intent-contract substrate threat controls v0 (PRD-SEC-013).

Facade for the shared loader, weaken predicate, git revision machinery, ledger,
telemetry, enrollment marker, and the two edit-time control points. Every module
here ships in the public BSL-1.1 trw-mcp package and must carry NO coupling to
the proprietary packages (trw-eval, trw-distill, trw-swarm, trw-loop) — NFR04's
import-boundary grep asserts zero matches.
"""

from __future__ import annotations

from trw_mcp.security.intent_contract._models import (
    BINDING_AUTHORITY,
    ArgvFalsifier,
    Contract,
    FalsifierRef,
    MustNotHappenClaim,
    PytestFalsifier,
    SignedCommitViolation,
    WeakenedClaim,
    WeakenEditFinding,
)
from trw_mcp.security.intent_contract.loader import (
    ContractLoadError,
    load_contract,
    load_contract_bytes,
)

__all__ = [
    "BINDING_AUTHORITY",
    "ArgvFalsifier",
    "Contract",
    "ContractLoadError",
    "FalsifierRef",
    "MustNotHappenClaim",
    "PytestFalsifier",
    "SignedCommitViolation",
    "WeakenEditFinding",
    "WeakenedClaim",
    "load_contract",
    "load_contract_bytes",
]
