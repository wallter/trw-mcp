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
from typing import TYPE_CHECKING, TypeVar

import structlog

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._main_fields import _TRWConfigFields
from trw_mcp.models.config._profiles import resolve_client_profile
from pydantic import Field

from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    MemoryConfig,
    MetaTuneConfig,
    OrchestrationConfig,
    PathsConfig,
    ScoringConfig,
    TelemetryConfig,
    ToolsConfig,
    TrustConfig,
)

if TYPE_CHECKING:
    from trw_mcp.models.config._surface_config import SurfaceConfig

_SubT = TypeVar("_SubT")


class TRWConfig(_TRWConfigFields):
    """TRW MCP server configuration.

    Inherits all 200+ field declarations from _TRWConfigFields.
    Adds domain sub-config properties for type-narrowed access
    and helper methods for profile resolution.
    """

    # -- Meta-Tune Safety (PRD-HPO-SAFE-001 FR-7) --
    # Nested sub-config (not projected from flat fields) because the meta-
    # tune pipeline owns its own config surface distinct from ceremony/
    # scoring/build settings. Kill switch defaults to False per NFR-7.
    meta_tune: MetaTuneConfig = Field(default_factory=MetaTuneConfig)

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

    @cached_property
    def tools(self) -> ToolsConfig:
        """Tool exposure and MCP server instruction sub-config."""
        return self._sub_config(ToolsConfig)

    @cached_property
    def surfaces(self) -> SurfaceConfig:
        """Unified surface configuration resolved from profile + flat fields.

        PRD-CORE-125 Phase 3 (FR13): Single frozen model for all surface
        control flags, eliminating scattered getattr calls at gate sites.
        """
        from trw_mcp.models.config._surface_config import (
            NudgeConfig,
            RecallConfig,
            SurfaceConfig,
            ToolExposureConfig,
        )

        return SurfaceConfig(
            nudge=NudgeConfig(
                enabled=self.effective_nudge_enabled,
                urgency_mode=self.nudge_urgency_mode,
                budget_chars=self.nudge_budget_chars,
                dedup_enabled=self.nudge_dedup_enabled,
            ),
            tool_exposure=ToolExposureConfig(
                mode=self.effective_tool_exposure_mode,
                custom_list=tuple(self.tool_exposure_list),
            ),
            recall=RecallConfig(
                enabled=self.effective_learning_recall_enabled,
                max_results=self.recall_max_results,
                injection_preview_chars=self.learning_injection_preview_chars,
                session_start_recall=self.session_start_recall_enabled
                if self.session_start_recall_enabled is not None
                else True,
            ),
            mcp_instructions_enabled=self.effective_mcp_instructions_enabled,
            hooks_enabled=self.effective_hooks_enabled,
            skills_enabled=self.effective_skills_enabled,
            agents_enabled=self.effective_agents_enabled,
            framework_ref_enabled=self.effective_framework_ref_enabled,
            tool_descriptions_variant=self.tool_descriptions_variant,
        )

    @property
    def effective_ceremony_mode(self) -> str:
        """Profile-aware ceremony mode. Explicit config wins over profile default."""
        if self.ceremony_mode != "full":
            return self.ceremony_mode

        # PRD-CORE-134: Adaptive ceremony for MINIMAL runs
        if self.active_run_complexity == "MINIMAL":
            return "light"

        return self.client_profile.ceremony_mode

    @property
    def effective_nudge_enabled(self) -> bool:
        """Profile-aware nudge gate. Explicit config=False wins."""
        if self.nudge_enabled is not None:
            return self.nudge_enabled

        # PRD-CORE-134: Suppress nudges for MINIMAL runs
        if self.active_run_complexity == "MINIMAL":
            return False

        return self.client_profile.nudge_enabled

    @cached_property
    def active_run_complexity(self) -> str | None:
        """Return the complexity_class of the active run if one exists."""
        try:
            from trw_mcp.state._paths import resolve_run_path
            from trw_mcp.state.persistence import FileStateReader

            run_path = resolve_run_path()
            if run_path and run_path.exists():
                reader = FileStateReader()
                state_data = reader.read_yaml(run_path / "meta" / "run.yaml")
                return str(state_data.get("complexity_class", ""))
        except Exception:  # justified: fail-open, active run metadata is optional for profile selection
            structlog.get_logger(__name__).debug("active_run_complexity_unavailable", exc_info=True)
        return None

    @property
    def effective_hooks_enabled(self) -> bool:
        """Profile-aware hook gate. Explicit config=False wins."""
        if self.hooks_enabled is not None:
            return self.hooks_enabled
        return self.client_profile.hooks_enabled

    @property
    def effective_tool_exposure_mode(self) -> str:
        """Profile-aware tool exposure. Non-default config wins."""
        if self.tool_exposure_mode != "all":
            return self.tool_exposure_mode
        return self.client_profile.tool_exposure_mode

    @property
    def effective_skills_enabled(self) -> bool:
        """Profile-aware skill loading. Explicit config=False wins."""
        if self.skills_enabled is not None:
            return self.skills_enabled
        return self.client_profile.skills_enabled

    @property
    def effective_learning_recall_enabled(self) -> bool:
        """Profile-aware recall gate. Explicit config=False wins."""
        if self.learning_recall_enabled is not None:
            return self.learning_recall_enabled
        return self.client_profile.learning_recall_enabled

    @property
    def effective_mcp_instructions_enabled(self) -> bool:
        """Profile-aware MCP instructions gate. Explicit config=False wins."""
        if self.mcp_server_instructions_enabled is not None:
            return self.mcp_server_instructions_enabled
        return self.client_profile.mcp_instructions_enabled

    @property
    def effective_agents_enabled(self) -> bool:
        """Profile-aware agent definitions gate.

        No profile field yet for agents; default is enabled.
        Explicit ``agents_enabled=False`` in config disables agent loading.
        """
        if self.agents_enabled is not None:
            return self.agents_enabled
        return True  # No profile field yet; default enabled

    @property
    def effective_framework_ref_enabled(self) -> bool:
        """Profile-aware framework reference gate.

        Delegates to ``client_profile.include_framework_ref`` when the
        config sentinel (None) is present.
        """
        if self.framework_md_enabled is not None:
            return self.framework_md_enabled
        return self.client_profile.include_framework_ref

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
    def resolved_backend_url(self) -> str:
        """Backend URL with fallback to ``platform_urls`` when unset.

        Returns ``backend_url`` if explicitly set (truthy); otherwise falls
        back to the last element of ``platform_urls`` (convention: local
        dev URLs are listed after production URLs in a multi-env config).
        Returns ``""`` when neither source yields a URL.
        """
        if self.backend_url:
            return self.backend_url
        if self.platform_urls:
            return self.platform_urls[-1]
        return ""

    @property
    def resolved_backend_api_key(self) -> str:
        """Backend API key with fallback to ``platform_api_key`` when unset.

        Returns ``backend_api_key`` if explicitly set (truthy); otherwise
        falls back to the secret value of ``platform_api_key``. Returns
        ``""`` when neither source yields a key.
        """
        if self.backend_api_key:
            return self.backend_api_key
        platform_key = self.platform_api_key.get_secret_value() if self.platform_api_key else ""
        return platform_key

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
