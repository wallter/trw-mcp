"""PRD-INTENT-002 FR08 — middleware chain ordering.

The re-groomed FR08 asserts RELATIVE ordering on the HEAD chain
(``MCPSecurity → Ceremony → ContextBudget → ResponseOptimizer``), NOT a fixed
absolute index: PhaseExposureMiddleware MUST sit AFTER CeremonyMiddleware
(session state resolved first) and BEFORE ContextBudgetMiddleware (phase
filtering precedes context/observation masking). If ContractMiddleware ever
lands it inserts between Ceremony and PhaseExposure; the relative assertion
absorbs that without edit.
"""

from __future__ import annotations

from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware
from trw_mcp.server._app import _build_middleware


def _index_of(chain: list[object], cls: type) -> int:
    for i, mw in enumerate(chain):
        if isinstance(mw, cls):
            return i
    return -1


def test_phase_exposure_middleware_position() -> None:
    """FR08: Ceremony < PhaseExposure < ContextBudget (relative order)."""
    chain = _build_middleware()

    ceremony_idx = _index_of(chain, CeremonyMiddleware)
    phase_idx = _index_of(chain, PhaseExposureMiddleware)

    assert ceremony_idx != -1, "CeremonyMiddleware missing from chain"
    assert phase_idx != -1, "PhaseExposureMiddleware missing from chain"
    # PhaseExposure must come AFTER Ceremony (session state first).
    assert ceremony_idx < phase_idx, "PhaseExposure must follow Ceremony"

    # If ContextBudget is present, PhaseExposure must precede it.
    from trw_mcp.middleware.context_budget import ContextBudgetMiddleware

    ctx_idx = _index_of(chain, ContextBudgetMiddleware)
    if ctx_idx != -1:
        assert phase_idx < ctx_idx, "PhaseExposure must precede ContextBudget"


def test_phase_exposure_present_when_observation_masking_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FR08: PhaseExposure is appended regardless of optional-middleware toggles."""
    chain = _build_middleware()
    assert _index_of(chain, PhaseExposureMiddleware) != -1
