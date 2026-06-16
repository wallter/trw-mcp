"""Profile data model — PRD-HPO-PROF-001 §7.5.

Belongs to the ``trw_mcp.profile`` package facade (``profile/__init__.py``).
Re-exported there for back-compat and a single import point.

Defines the hierarchical-composition surface: a single ``Profile`` layer
(all fields optional — ``None`` means "inherit"), the ``RecallPolicy`` /
``ConfidenceBands`` sub-models, the ``ProfileLayer`` wrapper (a named layer
contributing overrides), and the ``ResolvedProfile`` (the composition of all
active layers plus per-field attribution).

The model is the FR-1 surface: exactly 10 optional override keys, ``extra=
"forbid"`` so unknown keys raise ``ValidationError``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The six TRW execution phases (uppercase, matching ``models/run.py`` and
#: ``telemetry/constants.py``).
PhaseName = Literal["RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"]

#: Sentinel value at a deeper layer that REMOVES an inherited field (FR-3).
#: A field set to this string is dropped from the resolved profile.
UNSET_SENTINEL = "__unset__"

#: The exact 10 override keys the Profile surface accepts (FR-1).
PROFILE_SURFACE_KEYS: tuple[str, ...] = (
    "ceremony_tier",
    "phase_enabled_set",
    "allowed_tools_by_phase",
    "recall_policy",
    "checkpoint_cadence",
    "review_threshold",
    "confidence_bands",
    "build_check_scope",
    "cost_budget_usd",
    "token_budget",
)

#: The persistent layer names that contribute to ``surface_snapshot_id``
#: (FR-13 / §7.2.1). ``session`` is intentionally excluded.
PERSISTENT_LAYER_NAMES: tuple[str, ...] = (
    "defaults",
    "org",
    "domain",
    "task-type",
    "client",
)

#: The canonical 6-layer composition order (FR-2). ``client`` is most-local
#: and applied last so transport concerns are not overridable by a session
#: pin or a task-type layer.
LAYER_ORDER: tuple[str, ...] = (
    "defaults",
    "org",
    "domain",
    "task-type",
    "session",
    "client",
)


class RecallPolicy(BaseModel):
    """Recall tuning sub-surface (FR-1, §7.5)."""

    model_config = ConfigDict(extra="forbid")

    k: int | None = None
    min_impact: float | None = None
    rerank_strategy: Literal["none", "mmr", "cross-encoder"] | None = None


class ConfidenceBands(BaseModel):
    """Confidence-band thresholds sub-surface (FR-1, §7.5)."""

    model_config = ConfigDict(extra="forbid")

    high: float | None = None
    medium: float | None = None
    low: float | None = None


class Profile(BaseModel):
    """A single profile layer's optional overrides (FR-1).

    Every field is optional. ``None`` at a layer means "inherit from the
    layer above". The string ``__unset__`` (``UNSET_SENTINEL``) at a deeper
    layer removes an inherited field (FR-3). ``extra="forbid"`` makes any
    unknown key a ``ValidationError`` (FR-1 assertion).
    """

    model_config = ConfigDict(extra="forbid")

    ceremony_tier: Literal["MINIMAL", "STANDARD", "COMPREHENSIVE"] | None = None
    phase_enabled_set: list[PhaseName] | None = None
    allowed_tools_by_phase: dict[PhaseName, list[str]] | None = None
    recall_policy: RecallPolicy | None = None
    checkpoint_cadence: Literal["minimal", "standard", "aggressive"] | None = None
    review_threshold: Literal["NONE", "MINIMAL", "STANDARD", "COMPREHENSIVE"] | None = None
    confidence_bands: ConfidenceBands | None = None
    build_check_scope: Literal["none", "targeted", "full"] | None = None
    cost_budget_usd: float | None = None
    token_budget: int | None = None
    # FR-9 invariant input: a profile may declare its environment so the
    # ``env=dev`` escape hatches (build_check_scope=none, review NONE) are
    # only honored in dev. Not part of the 10 override surface keys — it is a
    # validation-context field, kept off PROFILE_SURFACE_KEYS deliberately.
    env: Literal["dev", "prod"] | None = None


class ProfileLayer(BaseModel):
    """A named layer contributing overrides (one YAML file or pin).

    ``name`` is one of LAYER_ORDER. ``rationale`` satisfies RISK-001 (every
    org layer SHOULD explain itself); it is advisory and never composed into
    the resolved surface. ``source_path`` records provenance for explain.

    A layer may carry the ``__unset__`` removal sentinel (FR-3) on any field.
    Because the typed ``Profile`` model cannot hold ``"__unset__"`` on a
    non-string field, the sentinel is held in ``raw_overrides`` (the
    pre-validation dict) and stripped from the typed ``overrides`` view. The
    resolver reads ``raw_overrides`` so it can detect removals on any field
    while ``overrides`` stays a fully-typed surface.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    overrides: Profile = Field(default_factory=Profile)
    raw_overrides: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None
    source_path: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _split_sentinels(cls, data: Any) -> Any:
        """Derive the typed ``overrides`` from caller-supplied override fields.

        Accepts either a pre-built ``overrides`` Profile/dict OR raw surface
        kwargs. Any field whose value is ``__unset__`` is recorded in
        ``raw_overrides`` and removed before typed validation so the typed
        Profile only ever holds real values.
        """
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        # Gather the override source: explicit ``overrides`` wins, else any
        # surface-key kwargs passed directly.
        source = payload.get("overrides")
        if isinstance(source, Profile):
            override_dict: dict[str, Any] = source.model_dump(exclude_none=True)
        elif isinstance(source, dict):
            override_dict = dict(source)
        else:
            override_dict = {k: payload.pop(k) for k in list(payload) if k in PROFILE_SURFACE_KEYS}
        raw = dict(override_dict)
        typed = {k: v for k, v in override_dict.items() if v != UNSET_SENTINEL}
        payload["overrides"] = typed
        payload["raw_overrides"] = raw
        return payload


class LayerAttribution(BaseModel):
    """Per-field origin record for ``trw_profile_explain`` (FR-11)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    value: object | None = None
    origin_layer: str | None = None
    override_chain: list[str] = Field(default_factory=list)


class ResolvedProfile(BaseModel):
    """The composition of all active layers with per-field attribution.

    ``profile`` is the effective (merged) surface. ``layers_applied`` lists
    the names of layers that actually contributed at least one field, in
    composition order. ``surface_snapshot_id`` hashes only the persistent
    layers (FR-13); ``session_override_hash`` carries the session-layer delta
    separately. ``attribution`` maps each field name to its origin chain.
    """

    model_config = ConfigDict(extra="forbid")

    profile: Profile
    layers_applied: list[str] = Field(default_factory=list)
    surface_snapshot_id: str = ""
    session_override_hash: str = ""
    attribution: dict[str, LayerAttribution] = Field(default_factory=dict)


__all__ = [
    "LAYER_ORDER",
    "PERSISTENT_LAYER_NAMES",
    "PROFILE_SURFACE_KEYS",
    "UNSET_SENTINEL",
    "ConfidenceBands",
    "LayerAttribution",
    "PhaseName",
    "Profile",
    "ProfileLayer",
    "RecallPolicy",
    "ResolvedProfile",
]
