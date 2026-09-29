"""opencode tool-return enrichment helpers for trw_code and friends.

# Managed by TRW — no trw_distill imports permitted.

Provides client-profile detection and transport resolution when
TRW_CLIENT_PROFILE=opencode is set.  Default tier is T2 per audit fix P1-11
(NOT T3 which consumed ~2.5% per call).

There is no T3 builder and no config field that selects one; an unbuilt tier
reaching ``enrich_response`` is logged at WARNING and returned unenriched.

Transport resolution uses TRW_CLIENT_PROFILE + TRW_MCP_TRANSPORT env vars
(P0-13 audit fix — ctx.session_id is NOT used for client discrimination).

T2 tool-return payload construction is handled by the shared substrate
``channels/_tool_return_tiers.py::enrich_response()``, which is called
directly from ``tools/before_edit_hint.py``,
and ``tools/codebase_risk_report.py``.  No per-client payload builder is
needed here.

PRD-DIST-2403 FR16-FR19.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

__all__ = []

# Module-level flag to rate-limit the "client=unknown" warning (one per process)
_unknown_client_warned: bool = False
