"""Tests for the trusted Anthropic model-capability catalog (PRD-CORE-209).

The catalog is an adapter-edge table: it stops the safe-base clamp only when
the caller supplies a trusted active-model identity that declares support.
TRW still never auto-selects xhigh/max — recommendation happens upstream.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import (
    ANTHROPIC_MODEL_CATALOG_VERSION,
    lookup_model_effort_capabilities,
    resolve_effort_adapter,
)


class TestCatalogLookup:
    """PRD-CORE-209-FR01: model identity -> declared effort capability set."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-fable-5",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-sonnet-5",
        ],
    )
    def test_frontier_and_balanced_models_declare_xhigh_and_max(self, model_id: str) -> None:
        capabilities = lookup_model_effort_capabilities(model_id)
        assert capabilities == frozenset({"low", "medium", "high", "xhigh", "max"})

    @pytest.mark.parametrize("model_id", ["claude-opus-4-6", "claude-sonnet-4-6"])
    def test_previous_generation_models_lack_xhigh(self, model_id: str) -> None:
        capabilities = lookup_model_effort_capabilities(model_id)
        assert capabilities == frozenset({"low", "medium", "high", "max"})

    def test_haiku_declares_no_effort_support(self) -> None:
        assert lookup_model_effort_capabilities("claude-haiku-4-5") == frozenset()

    def test_unknown_model_returns_none(self) -> None:
        assert lookup_model_effort_capabilities("gpt-5.6-sol") is None
        assert lookup_model_effort_capabilities("") is None
        assert lookup_model_effort_capabilities("claude") is None

    def test_date_suffixed_full_id_matches_family(self) -> None:
        assert lookup_model_effort_capabilities("claude-haiku-4-5-20251001") == frozenset()

    def test_provider_prefix_and_case_are_normalized(self) -> None:
        capabilities = lookup_model_effort_capabilities("anthropic.claude-opus-4-8")
        assert capabilities == frozenset({"low", "medium", "high", "xhigh", "max"})
        assert lookup_model_effort_capabilities("Claude-Opus-4-8") == capabilities

    def test_long_context_variant_matches_family(self) -> None:
        capabilities = lookup_model_effort_capabilities("claude-opus-4-8[1m]")
        assert capabilities == frozenset({"low", "medium", "high", "xhigh", "max"})

    def test_similar_prefix_does_not_false_match(self) -> None:
        # A hypothetical distinct model must not inherit a shorter key's caps.
        assert lookup_model_effort_capabilities("claude-opus-4-80") is None

    def test_region_prefixed_bedrock_inference_profile_matches(self) -> None:
        # Adversarial-audit F4: "us.anthropic.claude-…" is the standard
        # Bedrock cross-region invocation form.
        capabilities = lookup_model_effort_capabilities("us.anthropic.claude-opus-4-8")
        assert capabilities == frozenset({"low", "medium", "high", "xhigh", "max"})
        assert lookup_model_effort_capabilities("eu.anthropic.claude-haiku-4-5") == frozenset()

    def test_opus_4_5_declares_no_xhigh_or_max(self) -> None:
        assert lookup_model_effort_capabilities("claude-opus-4-5") == frozenset({"low", "medium", "high"})
        # Vertex dated-snapshot form.
        assert lookup_model_effort_capabilities("claude-opus-4-5@20251101") == frozenset({"low", "medium", "high"})


class TestAdapterWithActiveModel:
    """PRD-CORE-209-FR02/FR03: resolve_effort_adapter consumes the catalog."""

    def test_xhigh_maps_on_declared_model(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="xhigh",
            active_model="claude-opus-4-8",
        )
        assert decision.status == "mapped"
        assert decision.harness_value == "xhigh"
        assert ANTHROPIC_MODEL_CATALOG_VERSION in decision.adapter_id

    def test_max_maps_on_fable(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="max",
            active_model="claude-fable-5",
        )
        assert decision.status == "mapped"
        assert decision.harness_value == "max"

    def test_xhigh_clamps_on_previous_generation_model(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="xhigh",
            active_model="claude-opus-4-6",
        )
        assert decision.status == "clamped"
        # Ties break DOWNWARD: xhigh is equidistant from high and max, and
        # the adapter must never escalate above the recommendation.
        assert decision.harness_value == "high"

    def test_max_maps_on_previous_generation_model(self) -> None:
        # Adversarial-audit F3 coverage: `max` predates `xhigh` — the 4.6
        # generation officially supports max but not xhigh (effort doc,
        # 2026-07-09). This is deliberate catalog content, not a typo.
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="max",
            active_model="claude-sonnet-4-6",
        )
        assert decision.status == "mapped"
        assert decision.harness_value == "max"

    def test_xhigh_on_opus_4_5_clamps_to_high(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="xhigh",
            active_model="claude-opus-4-5",
        )
        assert decision.status == "clamped"
        assert decision.harness_value == "high"

    def test_haiku_is_unsupported_never_clamped(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="medium",
            active_model="claude-haiku-4-5",
        )
        assert decision.status == "unsupported"
        assert decision.harness_value is None

    def test_unknown_model_falls_back_to_safe_base(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="xhigh",
            active_model="some-unrecognized-model",
        )
        assert decision.status == "clamped"
        assert decision.harness_value == "high"
        assert decision.adapter_id == "claude-code-safe-2026-07-10"

    def test_explicit_supported_efforts_outranks_catalog(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="xhigh",
            active_model="claude-opus-4-8",
            supported_efforts=frozenset({"low", "medium", "high"}),
        )
        assert decision.status == "clamped"
        assert decision.harness_value == "high"
        assert decision.adapter_id == "claude-code:explicit"

    def test_inherit_is_unchanged_by_active_model(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="inherit",
            active_model="claude-opus-4-8",
        )
        assert decision.status == "inherited"
        assert decision.harness_value is None

    def test_catalog_applies_to_any_client_running_the_model(self) -> None:
        # The catalog is a model table, not a claude-code special case.
        decision = resolve_effort_adapter(
            client_id="codex",
            recommended_effort="xhigh",
            active_model="claude-sonnet-5",
        )
        assert decision.status == "mapped"
        assert decision.harness_value == "xhigh"

    def test_no_active_model_preserves_legacy_behavior(self) -> None:
        decision = resolve_effort_adapter(
            client_id="claude-code",
            recommended_effort="xhigh",
        )
        assert decision.status == "clamped"
        assert decision.harness_value == "high"
        assert decision.adapter_id == "claude-code-safe-2026-07-10"
