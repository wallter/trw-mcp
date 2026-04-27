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
from pydantic import Field, model_validator

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._main_fields import _TRWConfigFields
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    MemoryConfig,
    MetaTuneConfig,
    OrchestrationConfig,
    PathsConfig,
    ScoringConfig,
    SecurityConfig,
    TelemetryConfig,
    ToolsConfig,
    TrustConfig,
)

if TYPE_CHECKING:
    from typing import Literal

    from trw_mcp.models.config._fields_ceremony import NudgeMessengerLiteral
    from trw_mcp.models.config._surface_config import SurfaceConfig

_SubT = TypeVar("_SubT")


class TRWConfig(_TRWConfigFields):
    """TRW MCP server configuration.

    Inherits all 200+ field declarations from _TRWConfigFields.
    Adds domain sub-config properties for type-narrowed access
    and helper methods for profile resolution.
    """

    # -- Static-analyzer-only field re-declarations (PRD-CORE-146 follow-up) --
    # ``_CeremonyFields`` is a plain-class mixin (not a BaseModel) so Pydantic's
    # metaclass picks up its annotations via MRO at runtime, but Pyright's
    # static analysis cannot see them on the composed ``TRWConfig`` type.
    # Declaring these under ``if TYPE_CHECKING:`` gives Pyright visibility
    # without triggering Pydantic v2's field-shadow ``UserWarning`` at import
    # time (and without changing the runtime field set).
    if TYPE_CHECKING:
        nudge_enabled: bool | None = None
        nudge_messenger: NudgeMessengerLiteral | None = None
        nudge_density: Literal["low", "medium", "high"] | None = None
        pricing_table_path: str = ""

    # -- Meta-Tune Safety (PRD-HPO-SAFE-001 FR-7) --
    # Nested sub-config (not projected from flat fields) because the meta-
    # tune pipeline owns its own config surface distinct from ceremony/
    # scoring/build settings. Kill switch defaults to False per NFR-7.
    meta_tune: MetaTuneConfig = Field(default_factory=MetaTuneConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @model_validator(mode="before")
    @classmethod
    def _normalize_meta_tune_compat(cls, data: object) -> object:
        """Keep legacy ``meta_tune_enabled`` and nested ``meta_tune.enabled`` aligned.

        SAFE-001 runtime code reads ``config.meta_tune.enabled`` while older
        config surfaces and env-vars still populate the flat
        ``meta_tune_enabled`` field. Normalize both directions so loader/env
        compatibility remains trustworthy end-to-end.
        """
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        legacy_enabled = payload.get("meta_tune_enabled")
        nested_meta_tune = payload.get("meta_tune")

        if isinstance(nested_meta_tune, MetaTuneConfig):
            nested_data: dict[str, object] = nested_meta_tune.model_dump()
        elif isinstance(nested_meta_tune, dict):
            nested_data = dict(nested_meta_tune)
        else:
            nested_data = {}

        if "enabled" not in nested_data and isinstance(legacy_enabled, bool):
            nested_data["enabled"] = legacy_enabled

        if nested_data:
            payload["meta_tune"] = nested_data
            if isinstance(nested_data.get("enabled"), bool):
                payload["meta_tune_enabled"] = nested_data["enabled"]
        elif isinstance(legacy_enabled, bool):
            payload["meta_tune"] = {"enabled": legacy_enabled}

        return payload

    @model_validator(mode="after")
    def _finalize_meta_tune_compat(self) -> TRWConfig:
        """Ensure env-populated flat fields still activate nested SAFE-001 config."""
        if self.meta_tune.enabled == self.meta_tune_enabled:
            return self
        self.meta_tune = self.meta_tune.model_copy(update={"enabled": self.meta_tune_enabled})
        return self

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

    @property
    def effective_nudge_messenger(self) -> str:
        """PRD-CORE-145 FR01: resolve messenger name (None → "standard").

        Returns one of {"standard", "minimal", "learning_injection", "contextual",
        "contextual_action"}.
        "standard" preserves the pre-PRD pool-based dispatch. "minimal"
        routes through compute_nudge_minimal. "learning_injection" surfaces
        task-relevant prior learnings via the ceremony-status nudge surface.
        "contextual" preserves the workflow scaffold while adding one
        phase-aware next-step instruction and an optional relevant caution.
        "contextual_action" keeps the same next-step scaffold but omits the
        caution line so evaluation can isolate caution-related cognitive load.
        """
        return self.nudge_messenger if self.nudge_messenger is not None else "standard"

    @property
    def effective_nudge_density(self) -> str | None:
        """PRD-CORE-146 FR04: resolve nudge injection density.

        Explicit TRWConfig.nudge_density wins; otherwise falls back to the
        client profile's nudge_density. All built-in profiles default to
        ``None`` today (no profile opts in) — this returns ``None`` in that
        case, which nudge selection interprets as "use built-in cooldown".
        """
        if self.nudge_density is not None:
            return self.nudge_density
        return self.client_profile.nudge_density

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
    def resolved_sync_targets(self) -> list[tuple[str, str]]:
        """All configured (url, api_key) sync targets in push priority order.

        Ordering:
          1. Explicit ``backend_url`` (if truthy) with ``backend_api_key`` or
             ``platform_api_key`` as its key.
          2. Every entry of ``platform_urls`` (in list order), each paired
             with ``platform_api_key``.

        Targets with empty url or empty api_key are dropped. Duplicate URLs
        (case-insensitive normalized compare) collapse to the first occurrence
        so the explicit override remains the winner for its slot.
        """
        platform_key = self.platform_api_key.get_secret_value() if self.platform_api_key else ""
        candidates: list[tuple[str, str]] = []
        if self.backend_url:
            explicit_key = self.backend_api_key or platform_key
            candidates.append((self.backend_url, explicit_key))
        candidates.extend((url, platform_key) for url in self.platform_urls)

        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for url, key in candidates:
            if not url or not key:
                continue
            normalized = url.rstrip("/").lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append((url, key))
        return result

    @property
    def resolved_backend_url(self) -> str:
        """First sync target's URL, for backward compatibility.

        Prefer :attr:`resolved_sync_targets` in new code — this accessor
        returns only the highest-priority target.
        """
        targets = self.resolved_sync_targets
        return targets[0][0] if targets else ""

    @property
    def resolved_backend_api_key(self) -> str:
        """First sync target's API key, for backward compatibility.

        Prefer :attr:`resolved_sync_targets` in new code — this accessor
        returns only the highest-priority target.
        """
        targets = self.resolved_sync_targets
        return targets[0][1] if targets else ""

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
