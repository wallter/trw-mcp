"""Read-only reporting helpers for the profile sync dispatcher.

Belongs to the ``_profile_dispatcher.py`` facade. Split out to keep the
dispatcher under the 350-line ceiling enforced by
``tests/test_module_loc_gate.py``. It writes nothing.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def _capability_parity_drift(write_agents: bool, client: str) -> list[str]:
    """Return capability-projection parity drift detail strings for the sync.

    PRD-CORE-218-FR06: surface capability/lifecycle/count drift loudly in the
    sync result. Returns an empty list when AGENTS.md is not written (no
    capability appendix is generated) or when the generated projection matches
    the resolved surface manifest. A non-empty list is the same drift that
    causes the capability block to be dropped from the generated instructions.
    """
    if not write_agents:
        return []
    from trw_mcp.bootstrap._client_integration_appendix import (
        build_client_integration_appendix,
    )

    surface_id = "codex" if client == "codex" else "agents"
    appendix = build_client_integration_appendix(surface_id)
    return [f.detail for f in appendix.parity_failures]


__all__ = ["_capability_parity_drift"]
