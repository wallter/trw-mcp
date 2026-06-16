"""Empirical probe harness — sandboxed command runner (PRD-CORE-144).

Belongs to the ``probe`` facade. Re-exported from ``probe/__init__.py``.

FR-04 / §D13-P1-12: ``run_probe`` executes the probe command INSIDE the
shared SAFE-001 sandbox primitive — it imports ``ProbeIsolationContext``
from ``trw_mcp.meta_tune.sandbox`` and does NOT instantiate its own
subprocess + seccomp stack. If SAFE-001 upgrades isolation (Docker/gVisor),
CORE-144 inherits it without code change.

Fail-open contract: a probe failure (sandbox unavailable, OOM, timeout,
bad input post-validation) is folded into a typed ``inconclusive``
``ProbeResult`` and logged — it never raises into the caller. The ONE
exception is *input validation* (empty command, out-of-range timeout,
missing hypothesis), which raises :class:`ProbeValidationError` BEFORE any
subprocess spawn (FR-01 A2/A3) so the agent fixes the call.
"""

from __future__ import annotations

import shlex
from datetime import datetime, timezone

import structlog

from trw_mcp.meta_tune.errors import MetaTuneSafetyUnavailableError

# FR-04 Assertion A4 (grep assertion target): the harness consumes the shared
# SAFE-001 sandbox primitive rather than rolling its own.
from trw_mcp.meta_tune.sandbox import ProbeIsolationContext
from trw_mcp.models.probe import ProbeEvidence, ProbeResult, ResourceBudget

logger = structlog.get_logger(__name__)

_TIMEOUT_MAX_S = 300
_TIMEOUT_DEFAULT_S = 30


class ProbeValidationError(ValueError):
    """Raised for invalid probe input BEFORE any subprocess spawn (FR-01)."""


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _validate(hypothesis: str, command: str, timeout_s: int) -> list[str]:
    """Validate inputs and return the parsed argv. Raises pre-spawn (FR-01)."""
    if not hypothesis or not hypothesis.strip():
        raise ProbeValidationError("hypothesis must be a non-empty string")
    if not command or not command.strip():
        raise ProbeValidationError("command must be a non-empty string")
    if timeout_s <= 0:
        raise ProbeValidationError("timeout_s must be > 0")
    if timeout_s > _TIMEOUT_MAX_S:
        raise ProbeValidationError(f"timeout_s must be <= {_TIMEOUT_MAX_S}")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ProbeValidationError(f"command is not a valid shell command: {exc}") from exc
    if not argv:
        raise ProbeValidationError("command parsed to an empty argument vector")
    return argv


def _classify_verdict(exit_code: int | None, timed_out: bool) -> tuple[str, float]:
    """Map sandbox outcome to a (verdict, confidence) tuple.

    A clean exit (0) SUPPORTS the hypothesis; a non-zero exit REFUTES it;
    timeout / OOM / sandbox kill is INCONCLUSIVE (FR-03 A2) so plan
    adjudication never treats a flaky probe as decisive (RISK-005).
    """
    if timed_out:
        return "inconclusive", 0.5
    if exit_code == 0:
        return "supports", 0.9
    if exit_code is None:
        return "inconclusive", 0.5
    return "refutes", 0.9


def _inconclusive(
    *,
    hypothesis: str,
    hypothesis_id: str | None,
    run_id: str,
    reason: str,
    wall_ms: int = 0,
    budget_override: bool = False,
) -> ProbeResult:
    """Build a fail-open inconclusive result (never raises into the caller)."""
    return ProbeResult(
        hypothesis=hypothesis,
        hypothesis_id=hypothesis_id,
        verdict="inconclusive",
        evidence=ProbeEvidence(stderr=reason, wall_ms=wall_ms),
        confidence=0.5,
        ts=_now(),
        run_id=run_id,
        budget_override=budget_override,
    )


def run_probe(
    *,
    hypothesis: str,
    command: str,
    run_id: str,
    timeout_s: int = _TIMEOUT_DEFAULT_S,
    resource_budget: ResourceBudget | None = None,
    allow_network: bool = False,
    hypothesis_id: str | None = None,
    budget_override: bool = False,
) -> ProbeResult:
    """Run ``command`` inside ``ProbeIsolationContext`` and return a ``ProbeResult``.

    Input validation raises :class:`ProbeValidationError` pre-spawn. Every
    other failure mode (sandbox unavailable, subprocess error) is folded into
    an ``inconclusive`` result — the harness is fail-open by contract.
    """
    argv = _validate(hypothesis, command, timeout_s)
    budget = resource_budget or ResourceBudget()

    try:
        with ProbeIsolationContext(
            timeout_s=float(timeout_s),
            memory_cap_mb=budget.memory_mb,
            allow_network=allow_network,
            # Domain probes get a generic sandbox with no path allowlist; the
            # sandbox's own filesystem audit flags writes outside /tmp.
            readonly_paths=(),
            writable_paths=(),
            # strict=False so a degraded host (non-Linux / no seccomp) runs
            # the probe in degraded mode rather than refusing — the harness is
            # a best-effort evidence producer, not a security gate. SAFE-001's
            # candidate-replay path keeps strict=True; domain probes do not.
            strict=False,
        ) as runner:
            sandbox_result = runner.run(argv)
    except MetaTuneSafetyUnavailableError as exc:
        logger.warning(
            "probe_sandbox_unavailable",
            component="probe.harness",
            op="run_probe",
            outcome="inconclusive",
            reason=exc.activation_gate_blocked_reason,
            hypothesis_id=hypothesis_id,
        )
        return _inconclusive(
            hypothesis=hypothesis,
            hypothesis_id=hypothesis_id,
            run_id=run_id,
            reason=f"sandbox_unavailable: {exc.activation_gate_blocked_reason}",
            budget_override=budget_override,
        )
    except Exception as exc:  # fail-open: any sandbox error -> inconclusive
        logger.exception(
            "probe_harness_error",
            component="probe.harness",
            op="run_probe",
            outcome="inconclusive",
            error=str(exc),
            hypothesis_id=hypothesis_id,
        )
        return _inconclusive(
            hypothesis=hypothesis,
            hypothesis_id=hypothesis_id,
            run_id=run_id,
            reason=f"harness_error: {exc}",
            budget_override=budget_override,
        )

    verdict, confidence = _classify_verdict(sandbox_result.exit_code, sandbox_result.timed_out)
    resource_use: dict[str, float] = {"peak_rss_mb": round(sandbox_result.rss_peak_mb, 3)}
    evidence = ProbeEvidence(
        stdout=sandbox_result.stdout,
        stderr=sandbox_result.stderr,
        exit_code=sandbox_result.exit_code,
        wall_ms=int(sandbox_result.wall_ms),
        resource_use=resource_use,
        timed_out=sandbox_result.timed_out,
        network_attempted=sandbox_result.network_attempted,
        writes_outside_tmp=list(sandbox_result.writes_outside_tmp),
    )
    return ProbeResult(
        hypothesis=hypothesis,
        hypothesis_id=hypothesis_id,
        verdict=verdict,  # type: ignore[arg-type]
        evidence=evidence,
        confidence=confidence,
        ts=_now(),
        run_id=run_id,
        budget_override=budget_override,
    )


__all__ = ["ProbeValidationError", "run_probe"]
