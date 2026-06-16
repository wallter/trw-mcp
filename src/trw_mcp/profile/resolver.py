"""Deterministic 6-layer profile resolver — PRD-HPO-PROF-001 FR-2/3/4/13.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

``compose(layers)`` applies layers in the fixed canonical order
``defaults → org → domain → task-type → session → client`` (FR-2), merges
field-by-field with later-wins semantics (FR-3), honors the ``__unset__``
removal sentinel, records per-field attribution for explain (FR-11 input),
computes the persistent ``surface_snapshot_id`` + the sibling
``session_override_hash`` (FR-13), and enforces governance invariants
fail-closed (FR-9/FR-15) on the effective surface.

Determinism (FR-2 assertion / NFR-7): layers are sorted into ``LAYER_ORDER``
position before composition, so the result is independent of the caller's
dict/list iteration order.
"""

from __future__ import annotations

from trw_mcp.profile.invariants import enforce_invariants
from trw_mcp.profile.model import (
    LAYER_ORDER,
    PROFILE_SURFACE_KEYS,
    UNSET_SENTINEL,
    LayerAttribution,
    Profile,
    ProfileLayer,
    ResolvedProfile,
)
from trw_mcp.profile.snapshot import (
    compute_session_override_hash,
    compute_surface_snapshot_id,
)

_LAYER_RANK: dict[str, int] = {name: i for i, name in enumerate(LAYER_ORDER)}


def _ordered(layers: list[ProfileLayer]) -> list[ProfileLayer]:
    """Return ``layers`` sorted into canonical LAYER_ORDER position (FR-2).

    Unknown layer names sort last (stable) so a stray layer never silently
    reorders the governed chain. Within the same name, input order is kept.
    """
    return sorted(
        layers,
        key=lambda layer: (_LAYER_RANK.get(layer.name, len(LAYER_ORDER)),),
    )


def compose(layers: list[ProfileLayer]) -> ResolvedProfile:
    """Compose ``layers`` into a ``ResolvedProfile`` (FR-2/3/4/9/13).

    Steps:
      1. Order layers canonically (FR-2 determinism).
      2. For each surface key, walk layers low→high. ``None`` inherits;
         a concrete value overrides; ``__unset__`` removes the inherited
         field. Track the origin chain for attribution (FR-11 input).
      3. Build the effective ``Profile`` and enforce invariants fail-closed.
      4. Compute persistent snapshot id + session override hash (FR-13).
    """
    ordered = _ordered(layers)

    effective: dict[str, object] = {}
    attribution: dict[str, LayerAttribution] = {}
    contributed: dict[str, None] = {}  # ordered set of layer names that set a field

    for key in PROFILE_SURFACE_KEYS:
        chain: list[str] = []
        current_value: object | None = None
        origin: str | None = None
        present = False
        for layer in ordered:
            # Read from raw_overrides so the __unset__ sentinel (which the
            # typed Profile cannot hold on a non-string field) is visible.
            if key not in layer.raw_overrides:
                continue  # inherit (field not set at this layer)
            raw = layer.raw_overrides[key]
            if raw is None:
                continue  # explicit None also means inherit
            contributed.setdefault(layer.name, None)
            if raw == UNSET_SENTINEL:
                # Remove the inherited field at this deeper layer (FR-3).
                present = False
                current_value = None
                origin = layer.name
                chain.append(f"{layer.name}:__unset__")
                continue
            # Use the typed value from the validated overrides view.
            typed_value = getattr(layer.overrides, key)
            present = True
            current_value = typed_value
            origin = layer.name
            chain.append(f"{layer.name}:{_chain_repr(typed_value)}")
        if present:
            effective[key] = current_value
        attribution[key] = LayerAttribution(
            field=key,
            value=current_value if present else None,
            origin_layer=origin if present else None,
            override_chain=chain,
        )

    profile = Profile.model_validate(effective)
    # FR-9 / FR-15: enforce governance invariants on the effective surface,
    # fail-closed. A violation aborts resolution (no silent degradation).
    enforce_invariants(profile)

    # F6 (round-2 transport e2e): report contributing layers in canonical
    # LAYER_ORDER position, not surface-key-iteration order. ``contributed`` is
    # populated as each layer first sets SOME field, so a deeper layer that sets
    # an earlier-processed key could be inserted ahead of a shallower one (the
    # e2e observed ['defaults', 'session', 'org'] where 'org' outranks
    # 'session'). This is REPORTING-ONLY — the merge above already walks
    # ``ordered`` (canonical), so effective values + attribution are unchanged.
    # Unknown layer names sort last (stable), matching ``_ordered``.
    layers_applied = sorted(
        contributed,
        key=lambda name: (_LAYER_RANK.get(name, len(LAYER_ORDER)),),
    )

    return ResolvedProfile(
        profile=profile,
        layers_applied=layers_applied,
        surface_snapshot_id=compute_surface_snapshot_id(ordered),
        session_override_hash=compute_session_override_hash(ordered),
        attribution=attribution,
    )


def _chain_repr(value: object) -> str:
    """Compact string form of an override value for the attribution chain."""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return type(value).__name__


__all__ = ["compose"]
