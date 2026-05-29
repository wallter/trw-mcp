"""Agent-installer support code.

This package owns code that operates on bundled agent files (under
``trw_mcp/data/agents/``) when they are installed into a target client
workspace. Today that is just ``tier_resolver`` — the per-client
translation of the framework's capability-tier vocabulary
(``frontier|balanced|local-large|local-small``) into the concrete model
identifiers each harness accepts.

PRD-INFRA-104.
"""

from __future__ import annotations

from .tier_resolver import (
    KNOWN_CLIENTS as KNOWN_CLIENTS,
)
from .tier_resolver import (
    KNOWN_TIERS as KNOWN_TIERS,
)
from .tier_resolver import (
    resolve_tier as resolve_tier,
)
from .tier_resolver import (
    rewrite_model_line as rewrite_model_line,
)

__all__ = ["KNOWN_CLIENTS", "KNOWN_TIERS", "resolve_tier", "rewrite_model_line"]
