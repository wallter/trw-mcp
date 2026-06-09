"""RecallContext and the intel-cache protocol for recall scoring.

PRD-CORE-102: Enhanced recall scoring with contextual boosts.
PRD-CORE-116: Multi-dimensional boost factors and client-aware context.

Belongs to the ``_recall.py`` facade. Re-exported there for back-compat --
existing ``from trw_mcp.scoring._recall import RecallContext`` imports continue
to work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog

_logger = structlog.get_logger(__name__)


class _IntelCacheProtocol(Protocol):
    """Minimal cache protocol used by recall scoring."""

    def get_bandit_params(self) -> dict[str, float] | None: ...


@dataclass(frozen=True, init=False)
class RecallContext:
    """Contextual information for recall scoring boosts.

    All fields are optional — when absent/empty, the corresponding boost
    defaults to 1.0 (neutral). This preserves backward compatibility.

    PRD-CORE-116: Extended with client_profile, model_family, inferred_domains,
    team, prd_knowledge_ids. Old field names kept as deprecated aliases.
    """

    current_phase: str | None
    inferred_domains: set[str]
    team: str
    prd_knowledge_ids: set[str]
    modified_files: list[str]
    client_profile: str
    model_family: str
    intel_cache: _IntelCacheProtocol | None

    def __init__(
        self,
        *,
        current_phase: str | None = None,
        inferred_domains: set[str] | None = None,
        team: str = "",
        prd_knowledge_ids: set[str] | None = None,
        modified_files: list[str] | None = None,
        client_profile: str = "",
        model_family: str = "",
        intel_cache: _IntelCacheProtocol | None = None,
        # Deprecated aliases (backward compat)
        active_domains: list[str] | set[str] | None = None,
        team_id: str | None = None,
        active_prd_ids: list[str] | set[str] | None = None,
    ) -> None:
        # Handle deprecated aliases
        if active_domains is not None:
            _logger.warning("recall_context_deprecated_field", field="active_domains", use_instead="inferred_domains")
            if inferred_domains is None:
                inferred_domains = set(active_domains)
        if team_id is not None:
            _logger.warning("recall_context_deprecated_field", field="team_id", use_instead="team")
            if not team:
                team = team_id
        if active_prd_ids is not None:
            _logger.warning("recall_context_deprecated_field", field="active_prd_ids", use_instead="prd_knowledge_ids")
            if prd_knowledge_ids is None:
                prd_knowledge_ids = set(active_prd_ids)

        object.__setattr__(self, "current_phase", current_phase)
        object.__setattr__(self, "inferred_domains", inferred_domains if inferred_domains is not None else set())
        object.__setattr__(self, "team", team)
        object.__setattr__(self, "prd_knowledge_ids", prd_knowledge_ids if prd_knowledge_ids is not None else set())
        object.__setattr__(self, "modified_files", modified_files if modified_files is not None else [])
        object.__setattr__(self, "client_profile", client_profile)
        object.__setattr__(self, "model_family", model_family)
        object.__setattr__(self, "intel_cache", intel_cache)

    @property
    def active_domains(self) -> set[str]:
        """Deprecated: use ``inferred_domains`` instead."""
        _logger.warning("recall_context_deprecated_field", field="active_domains", use_instead="inferred_domains")
        return self.inferred_domains
