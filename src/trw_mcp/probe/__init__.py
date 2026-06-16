"""Empirical Probe Harness package (PRD-CORE-144).

A proposer-controlled, time-bounded, resource-bounded, structured-output
experiment primitive invoked during the PLAN phase to resolve disputed plan
assumptions that a rubric cannot adjudicate.

Public facade re-exports the harness entry point, budget accounting, the
run-scoped cache, verdict/contradiction detection, and telemetry builder.
Probes ALWAYS execute inside ``ProbeIsolationContext`` (the shared SAFE-001
sandbox) — never against live state — and the harness is fail-open: a probe
failure logs and returns a typed ``inconclusive`` result rather than raising
into the caller.
"""

from __future__ import annotations

from trw_mcp.probe.budget import (
    PLANNING_MODE_BUDGETS,
    ProbeBudget,
    ProbeBudgetExhausted,
)
from trw_mcp.probe.cache import ProbeCache, probe_cache_key
from trw_mcp.probe.harness import ProbeValidationError, run_probe
from trw_mcp.probe.linkage import (
    read_dissent_ledger,
    record_dissent_if_contradicted,
    write_verdict_back,
)
from trw_mcp.probe.telemetry import build_probe_event
from trw_mcp.probe.verdict import contradicts_claim, detect_dissent

__all__ = [
    "PLANNING_MODE_BUDGETS",
    "ProbeBudget",
    "ProbeBudgetExhausted",
    "ProbeCache",
    "ProbeValidationError",
    "build_probe_event",
    "contradicts_claim",
    "detect_dissent",
    "probe_cache_key",
    "read_dissent_ledger",
    "record_dissent_if_contradicted",
    "run_probe",
    "write_verdict_back",
]
