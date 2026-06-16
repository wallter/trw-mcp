"""``trw_probe`` + ``trw_probe_budget_status`` MCP tools (PRD-CORE-144 FR-01, FR-10).

Registers the proposer-facing probe tool and the operator-facing budget
observability tool. Per-session budget and run-scoped cache live in a
process-local registry keyed on ``run_id`` so the same identical probe within
a run is served from cache (FR-08) and budget is enforced per session (FR-07).

The tools are fail-open at the boundary: validation errors surface as a typed
error dict (so the agent fixes the call); every other failure mode is folded
into an ``inconclusive`` ProbeResult by the harness.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastmcp import FastMCP

from trw_mcp.models.probe import ProbeBudgetStatus, ResourceBudget
from trw_mcp.probe.budget import ProbeBudget, ProbeBudgetExhausted
from trw_mcp.probe.cache import ProbeCache, probe_cache_key
from trw_mcp.probe.harness import ProbeValidationError, run_probe
from trw_mcp.probe.telemetry import ProbeEvent, build_probe_event

logger = structlog.get_logger(__name__)

_DEFAULT_MODE = "TRIANGULATED_WITH_PROBE"

#: Process-local per-run state: run_id -> (budget, cache). One entry per run.
_RUN_STATE: dict[str, tuple[ProbeBudget, ProbeCache]] = {}


def _state_for(run_id: str, planning_mode: str) -> tuple[ProbeBudget, ProbeCache]:
    """Return (budget, cache) for a run, creating them on first use.

    Used by the PROBE path (a mutating operation): a probe legitimately
    creates per-run state. The STATUS path must NOT use this — see
    :func:`_read_only_budget_for` (FR-10 read-only contract).
    """
    state = _RUN_STATE.get(run_id)
    if state is None:
        state = (ProbeBudget(planning_mode), ProbeCache())
        _RUN_STATE[run_id] = state
    return state


def _read_only_budget_for(run_id: str, planning_mode: str) -> ProbeBudget:
    """Return the budget for ``run_id`` WITHOUT creating run state (FR-10).

    ``trw_probe_budget_status`` is a read-only observability tool: querying a
    run that has not yet probed must NOT insert a state entry into
    ``_RUN_STATE`` (that would be a write-side effect from a read, and would
    also pin a budget to whatever ``planning_mode`` the *status* call happened
    to pass). For an unknown run_id we synthesize a fresh zero-usage view
    instead — never mutating the registry.
    """
    state = _RUN_STATE.get(run_id)
    if state is not None:
        return state[0]
    return ProbeBudget(planning_mode)


def _probe_enabled() -> bool:
    """Feature flag — CORE-144 §9 Phase 1 rollout (default OFF).

    The PRD ships ``trw_probe`` behind ``TRW_PROBE_ENABLED=false`` so the tool
    is registered (stable surface for clients + tests) but inert until an
    operator opts in. We gate at the TOOL layer (not registration) so the
    tool stays discoverable and returns a typed ``probe_disabled`` error,
    rather than vanishing from the tool list when the flag is off.
    """
    return os.environ.get("TRW_PROBE_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _override_active() -> bool:
    """Operator budget override via env (FR-07)."""
    return os.environ.get("TRW_PROBE_BUDGET_OVERRIDE", "").strip().lower() in ("1", "true", "yes")


def _publish_probe_event(event: ProbeEvent) -> None:
    """Publish a ProbeEvent through the real telemetry pipeline (FR-09).

    Fire-and-forget: the unified telemetry pipeline owns batching + async
    delivery on its worker thread, so emission never blocks the probe tool.
    Any pipeline failure is swallowed (a probe must never fail because
    telemetry is unavailable) — the structured ``probe_event`` log line still
    records the verdict for local observability.
    """
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        TelemetryPipeline.get_instance().enqueue(event.model_dump(mode="json"))
    except Exception:  # justified: fail-open, telemetry must not block the probe
        logger.debug("probe_event_publish_failed", exc_info=True)


def _consult(tool_name: str, args: dict[str, Any]) -> None:
    try:
        from trw_mcp.server._security_hook import consult_mcp_security
    except Exception:
        return
    consult_mcp_security(tool_name, args, "", None)


def register_probe_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_probe(
        hypothesis: str,
        command: str,
        timeout_s: int = 30,
        memory_mb: int = 256,
        allow_network: bool = False,
        hypothesis_id: str | None = None,
        run_id: str = "unknown",
        planning_mode: str = _DEFAULT_MODE,
    ) -> dict[str, Any]:
        """Run a bounded, sandboxed experiment to resolve a disputed plan assumption.

        Use when, during the PLAN phase, two plan branches disagree on a
        load-bearing, empirically resolvable claim a rubric cannot adjudicate
        (e.g. "this parser handles a 50MB JSONL stream without OOM"). The
        ``command`` runs inside the shared SAFE-001 sandbox (subprocess +
        seccomp + no-network default), bounded by ``timeout_s`` and
        ``memory_mb``, and a typed ``ProbeResult`` with ``verdict`` in
        {supports, refutes, inconclusive} comes back.

        Budget is enforced per ``planning_mode`` (DIRECT=0, DUAL_DRAFT=1,
        TRIANGULATED=2, TRIANGULATED_WITH_PROBE=3); exhaustion returns a typed
        budget error. Identical probes within a run are served from cache.

        Returns: dict serialization of ``ProbeResult`` (or a typed error dict
        on validation failure / budget exhaustion / feature-flag disabled).
        """
        # FR-06 / §9 Phase 1: gated OFF by default at the tool layer — the tool
        # stays registered (stable surface) but inert until the operator opts
        # in via TRW_PROBE_ENABLED.
        if not _probe_enabled():
            return {
                "error": "probe_disabled",
                "reason": "trw_probe is gated OFF (CORE-144 §9 Phase 1)",
                "remediation": "set TRW_PROBE_ENABLED=1 to enable empirical probes",
            }
        _consult("trw_probe", {"hypothesis_id": hypothesis_id, "run_id": run_id})
        budget, cache = _state_for(run_id, planning_mode)

        # Cache check first (run-scoped; cross-session OFF by default — FR-08).
        key = probe_cache_key(command=command, hypothesis=hypothesis, hypothesis_id=hypothesis_id)
        cached = cache.get(key)
        if cached is not None:
            return cached.model_dump(mode="json")

        # Budget decrement BEFORE spawn (FR-07 A1).
        try:
            used_override = budget.consume(hypothesis_id=hypothesis_id, override=_override_active())
        except ProbeBudgetExhausted as exc:
            return {
                "error": "probe_budget_exhausted",
                "planning_mode": exc.planning_mode,
                "total": exc.total,
                "remaining": exc.remaining,
                "override_hint": exc.override_hint,
            }

        try:
            result = run_probe(
                hypothesis=hypothesis,
                command=command,
                run_id=run_id,
                timeout_s=timeout_s,
                resource_budget=ResourceBudget(memory_mb=memory_mb),
                allow_network=allow_network,
                hypothesis_id=hypothesis_id,
                budget_override=used_override,
            )
        except ProbeValidationError as exc:
            # Validation failed AFTER reserving a slot — refund it so a typo
            # does not burn the agent's budget.
            budget.used = max(0, budget.used - 1)
            return {"error": "probe_validation_error", "detail": str(exc)}

        cache.put(key, result)

        # FR-09: build the ProbeEvent and PUBLISH it through the real unified
        # telemetry pipeline (fire-and-forget) so the H4 meta-proposer can
        # measure probe yield. The local log line is kept for observability.
        event = build_probe_event(result, session_id=run_id, planning_mode=planning_mode)
        _publish_probe_event(event)
        logger.info(
            "probe_event",
            component="probe.tool",
            op="trw_probe",
            outcome=result.verdict,
            event_id=event.event_id,
            decisive=event.payload["decisive"],
            hypothesis_id=hypothesis_id,
        )
        return result.model_dump(mode="json")

    @server.tool()
    def trw_probe_budget_status(
        run_id: str = "unknown",
        planning_mode: str = _DEFAULT_MODE,
    ) -> dict[str, Any]:
        """Report live probe budget usage for a session (read-only, FR-10).

        Use when you need to detect runaway probe usage before it becomes
        cost/latency creep. Returns ``{used, remaining, total, planning_mode,
        by_hypothesis_id, by_mode}`` consistent with emitted ProbeEvents in
        the same run. Read-only — never mutates budget state.
        """
        _consult("trw_probe_budget_status", {"run_id": run_id})
        # FR-10 read-only: never create run state from a status query.
        budget = _read_only_budget_for(run_id, planning_mode)
        status = ProbeBudgetStatus(
            used=budget.used,
            remaining=budget.remaining,
            total=budget.total,
            planning_mode=budget.planning_mode,
            by_hypothesis_id=dict(budget.by_hypothesis_id),
            by_mode={budget.planning_mode: budget.used},
        )
        return status.model_dump(mode="json")


__all__ = ["register_probe_tools"]
