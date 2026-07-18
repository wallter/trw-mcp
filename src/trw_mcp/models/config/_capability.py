"""Provider-neutral capability tiers and legacy input normalization."""

from __future__ import annotations

from typing import Literal

CapabilityTier = Literal["frontier", "balanced", "local-large", "local-small"]
LegacyModelTier = Literal["cloud-opus", "cloud-sonnet", "local-30b", "local-8b"]
ModelTier = CapabilityTier | LegacyModelTier

_CAPABILITY_TIER_ALIASES: dict[ModelTier, CapabilityTier] = {
    "cloud-opus": "frontier",
    "cloud-sonnet": "balanced",
    "local-30b": "local-large",
    "local-8b": "local-small",
    "frontier": "frontier",
    "balanced": "balanced",
    "local-large": "local-large",
    "local-small": "local-small",
}


def normalize_capability_tier(model_tier: ModelTier) -> CapabilityTier:
    """Normalize compatibility tier names before persistence or hashing."""
    return _CAPABILITY_TIER_ALIASES[model_tier]


__all__ = ["CapabilityTier", "LegacyModelTier", "ModelTier", "normalize_capability_tier"]
