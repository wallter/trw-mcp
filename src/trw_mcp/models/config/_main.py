"""TRWConfig -- single source of truth for all TRW defaults.

All configuration values are centralized in _main_fields.py.
This module adds domain sub-config properties and helper methods.

PRD-CORE-071 Phase 1: Domain sub-configs provide type-narrowed access
(e.g. ``config.build`` returns a ``BuildConfig``). Flat field access
(``config.build_check_enabled``) is preserved -- all flat fields remain
via inheritance from _TRWConfigFields.

PRD-CORE-090: Decomposed from 790-line god-class into fields base class
+ thin shell with properties and methods.
"""

from __future__ import annotations

from functools import cached_property
from typing import TypeVar

import structlog

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._main_fields import _TRWConfigFields
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    MemoryConfig,
    OrchestrationConfig,
    PathsConfig,
    ScoringConfig,
    TelemetryConfig,
    TrustConfig,
)

_SubT = TypeVar("_SubT")


class TRWConfig(_TRWConfigFields):
    """TRW MCP server configuration.

    Inherits all 200+ field declarations from _TRWConfigFields.
    Adds domain sub-config properties for type-narrowed access
    and helper methods for profile resolution.
    """

    # -- Domain Sub-Config Properties (PRD-CORE-071-FR01) --
    # Type-narrowed access: ``config.build.build_check_enabled``
    # Flat access preserved: ``config.build_check_enabled``

    def _sub_config(self, cls: type[_SubT]) -> _SubT:
        """Project this config's fields into a domain sub-config model.

        Only copies fields that exist on both TRWConfig and the target model.
        """
        fields: dict[str, object] = getattr(cls, "model_fields", {})
        return cls(**{name: getattr(self, name) for name in fields if hasattr(self, name)})

    @cached_property
    def build(self) -> BuildConfig:
        """Build verification and mutation testing sub-config."""
        return self._sub_config(BuildConfig)

    @cached_property
    def memory(self) -> MemoryConfig:
        """Learning storage and retrieval sub-config."""
        return self._sub_config(MemoryConfig)

    @cached_property
    def telemetry_settings(self) -> TelemetryConfig:
        """Telemetry and OTEL sub-config (avoids ``telemetry`` field conflict)."""
        return self._sub_config(TelemetryConfig)

    @cached_property
    def orchestration(self) -> OrchestrationConfig:
        """Wave/shard orchestration sub-config."""
        return self._sub_config(OrchestrationConfig)

    @cached_property
    def scoring(self) -> ScoringConfig:
        """Scoring weights and decay parameters sub-config."""
        return self._sub_config(ScoringConfig)

    @cached_property
    def trust(self) -> TrustConfig:
        """Progressive trust model sub-config."""
        return self._sub_config(TrustConfig)

    @cached_property
    def ceremony_feedback(self) -> CeremonyFeedbackConfig:
        """Self-improving ceremony feedback sub-config."""
        return self._sub_config(CeremonyFeedbackConfig)

    @cached_property
    def paths(self) -> PathsConfig:
        """Directory structure and path defaults sub-config."""
        return self._sub_config(PathsConfig)

    @property
    def effective_ceremony_mode(self) -> str:
        """Profile-aware ceremony mode. Explicit config wins over profile default."""
        if self.ceremony_mode != "full":
            return self.ceremony_mode
        return self.client_profile.ceremony_mode

    @property
    def client_profile(self) -> ClientProfile:
        """Resolve the active client profile from target_platforms.

        Uses first platform as primary (F08: first-wins-with-warning for multi-platform).
        Uses @property (not @cached_property) because resolve_client_profile is a
        cheap dict lookup, and caching on a non-frozen BaseSettings risks stale data.
        """
        primary = self.target_platforms[0] if self.target_platforms else "claude-code"
        if len(self.target_platforms) > 1:
            structlog.get_logger(__name__).warning(
                "multi_platform_profile_resolution",
                primary=primary,
                all_platforms=self.target_platforms,
                detail="Using first platform as primary profile; instruction sync writes to all targets",
            )
        return resolve_client_profile(primary)

    @property
    def effective_platform_urls(self) -> list[str]:
        """Merged list of all configured platform URLs (deduped, non-empty)."""
        urls: list[str] = []
        if self.platform_url:
            urls.append(self.platform_url)
        urls.extend(self.platform_urls)
        seen: set[str] = set()
        result: list[str] = []
        for u in urls:
            normalized = u.rstrip("/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result
