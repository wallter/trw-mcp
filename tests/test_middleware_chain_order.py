"""Middleware chain relative ordering on the REAL chain built by ``_build_middleware``.

``MCPSecurityMiddleware`` runs first (the public allowlist applies before any
TRW masking), ``CeremonyMiddleware`` next (session state resolved first), then
``SurfaceAuthorityMiddleware`` (the only layer that masks TRW tools), and
``ResponseOptimizerMiddleware`` last among them (masking precedes response
shaping). No layer masks by run phase: phase exposure was deleted in
PRD-CORE-300 S11a, and ``test_phase_matrix.py`` holds that line.
"""

from __future__ import annotations

from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.middleware.mcp_security import MCPSecurityMiddleware
from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware
from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware
from trw_mcp.server._app import _build_middleware


def _index_of(chain: list[object], cls: type) -> int:
    for i, mw in enumerate(chain):
        if isinstance(mw, cls):
            return i
    raise AssertionError(f"{cls.__name__} not present in the built middleware chain")


def test_chain_relative_order_security_ceremony_surface_optimizer() -> None:
    chain = _build_middleware()

    i_security = _index_of(chain, MCPSecurityMiddleware)
    i_ceremony = _index_of(chain, CeremonyMiddleware)
    i_surface = _index_of(chain, SurfaceAuthorityMiddleware)
    i_optimizer = _index_of(chain, ResponseOptimizerMiddleware)

    assert i_security < i_ceremony, "MCPSecurity must precede Ceremony"
    assert i_ceremony < i_surface, "Ceremony must precede SurfaceAuthority"
    assert i_surface < i_optimizer, "SurfaceAuthority must precede ResponseOptimizer"
