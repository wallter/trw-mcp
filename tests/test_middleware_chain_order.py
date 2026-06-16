"""Middleware chain relative ordering — PRD-INTENT-002 FR08 (round-2 audit I2-F02).

The phase-exposure layer composes with the existing chain: it MUST sit AFTER
``MCPSecurityMiddleware`` (the public allowlist already applied — we compose, not
bypass) and ``CeremonyMiddleware`` (session state resolved first), and BEFORE
``ContextBudgetMiddleware`` + ``ResponseOptimizerMiddleware`` (phase filtering
precedes context/observation masking). This asserts the relative positions on
the REAL chain built by ``_build_middleware`` — not a hand-rolled list.
"""

from __future__ import annotations

from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.middleware.context_budget import ContextBudgetMiddleware
from trw_mcp.middleware.mcp_security import MCPSecurityMiddleware
from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware
from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware
from trw_mcp.server._app import _build_middleware


def _index_of(chain: list[object], cls: type) -> int:
    for i, mw in enumerate(chain):
        if isinstance(mw, cls):
            return i
    raise AssertionError(f"{cls.__name__} not present in the built middleware chain")


def test_chain_relative_order_security_ceremony_phase_budget_optimizer() -> None:
    """FR08: MCPSecurity < Ceremony < PhaseExposure < ContextBudget < ResponseOptimizer."""
    chain = _build_middleware()

    i_security = _index_of(chain, MCPSecurityMiddleware)
    i_ceremony = _index_of(chain, CeremonyMiddleware)
    i_phase = _index_of(chain, PhaseExposureMiddleware)
    i_budget = _index_of(chain, ContextBudgetMiddleware)
    i_optimizer = _index_of(chain, ResponseOptimizerMiddleware)

    # MCPSecurity runs first so the public allowlist filter applies before phase
    # masking composes on top of it.
    assert i_security < i_ceremony, "MCPSecurity must precede Ceremony"
    assert i_ceremony < i_phase, "Ceremony must precede PhaseExposure"
    assert i_phase < i_budget, "PhaseExposure must precede ContextBudget"
    assert i_budget < i_optimizer, "ContextBudget must precede ResponseOptimizer"
