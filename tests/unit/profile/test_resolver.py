"""FR-2 / FR-3 / FR-4 / FR-13 — resolver composition tests.

Covers the 6-layer order (FR-2), field-wise merge + __unset__ + inherit
(FR-3), the resolved-profile handoff shape (FR-4), and the snapshot/session
hash split (FR-13).
"""

from __future__ import annotations

from trw_mcp.profile import (
    LAYER_ORDER,
    UNSET_SENTINEL,
    ProfileLayer,
    compose,
)


def _layer(name: str, **overrides: object) -> ProfileLayer:
    return ProfileLayer(name=name, overrides=overrides)


def test_resolver_layer_order_6layer() -> None:
    """FR-2: the canonical chain is the documented 6-layer order."""
    assert LAYER_ORDER == (
        "defaults",
        "org",
        "domain",
        "task-type",
        "session",
        "client",
    )


def test_resolver_order_independent_of_dict_iteration() -> None:
    """FR-2: result is deterministic regardless of input list order.

    The deeper layer (task-type) wins over the shallower (defaults) no matter
    which order they are passed in.
    """
    a = _layer("defaults", review_threshold="MINIMAL")
    b = _layer("task-type", review_threshold="COMPREHENSIVE")
    forward = compose([a, b])
    reversed_ = compose([b, a])
    assert forward.profile.review_threshold == "COMPREHENSIVE"
    assert reversed_.profile.review_threshold == "COMPREHENSIVE"
    assert forward.surface_snapshot_id == reversed_.surface_snapshot_id


def test_resolver_skips_missing_layers() -> None:
    """FR-2/FR-5: only layers that contribute appear in layers_applied."""
    resolved = compose([_layer("defaults", ceremony_tier="STANDARD")])
    assert resolved.layers_applied == ["defaults"]
    assert "org" not in resolved.layers_applied


def test_layers_applied_in_canonical_layer_order() -> None:
    """F6 (round-2 transport e2e): layers_applied is reported in canonical
    LAYER_ORDER position, not surface-key-iteration order.

    ``session`` here sets the FIRST-iterated surface key (ceremony_tier) while
    ``org`` sets a LATER key (checkpoint_cadence). Before the fix, contributed
    insertion order yielded ['defaults', 'session', 'org'] (the e2e-observed
    bug); ``org`` (rank 1) must sort before ``session`` (rank 4).
    """
    resolved = compose(
        [
            _layer("defaults", ceremony_tier="MINIMAL"),
            _layer("org", checkpoint_cadence="aggressive"),
            _layer("session", ceremony_tier="COMPREHENSIVE"),
        ]
    )
    assert resolved.layers_applied == ["defaults", "org", "session"]
    # And the merge itself is unchanged (session is the deepest contributor).
    assert resolved.profile.ceremony_tier == "COMPREHENSIVE"
    assert resolved.profile.checkpoint_cadence == "aggressive"


def test_composition_field_merge() -> None:
    """FR-3: later layer overrides field-by-field, not object-level replace."""
    resolved = compose(
        [
            _layer("defaults", ceremony_tier="MINIMAL", build_check_scope="targeted"),
            _layer("org", ceremony_tier="STANDARD"),
        ]
    )
    # org overrode ceremony_tier; build_check_scope inherited from defaults.
    assert resolved.profile.ceremony_tier == "STANDARD"
    assert resolved.profile.build_check_scope == "targeted"


def test_none_at_layer_means_inherit() -> None:
    """FR-3: a None override at a layer inherits the shallower value."""
    resolved = compose(
        [
            _layer("defaults", ceremony_tier="STANDARD"),
            _layer("org"),  # all None
        ]
    )
    assert resolved.profile.ceremony_tier == "STANDARD"


def test_unset_sentinel_removes_inherited_field() -> None:
    """FR-3: __unset__ at a deeper layer removes the inherited field."""
    resolved = compose(
        [
            _layer("defaults", cost_budget_usd=10.0),
            _layer("org", cost_budget_usd=UNSET_SENTINEL),
        ]
    )
    assert resolved.profile.cost_budget_usd is None
    chain = resolved.attribution["cost_budget_usd"].override_chain
    assert any("__unset__" in c for c in chain)


def test_session_start_returns_resolved_profile_shape() -> None:
    """FR-4: a resolved profile carries the four handoff fields."""
    resolved = compose([_layer("defaults", review_threshold="STANDARD")])
    assert resolved.profile.review_threshold == "STANDARD"
    assert isinstance(resolved.layers_applied, list)
    assert resolved.surface_snapshot_id.startswith("surf_")
    assert resolved.session_override_hash.startswith("sess_")
    assert "review_threshold" in resolved.attribution


def test_attribution_records_origin_layer() -> None:
    """FR-11 input: attribution records the winning layer per field."""
    resolved = compose(
        [
            _layer("defaults", review_threshold="MINIMAL"),
            _layer("org", review_threshold="STANDARD"),
            _layer("task-type", review_threshold="COMPREHENSIVE"),
        ]
    )
    attr = resolved.attribution["review_threshold"]
    assert attr.origin_layer == "task-type"
    assert attr.value == "COMPREHENSIVE"
    # Override chain shows the full progression.
    assert attr.override_chain == [
        "defaults:MINIMAL",
        "org:STANDARD",
        "task-type:COMPREHENSIVE",
    ]


def test_snapshot_id_stable_across_processes() -> None:
    """FR-4/NFR-7: identical persistent content → identical snapshot id."""
    layers = [_layer("defaults", review_threshold="STANDARD"), _layer("org", build_check_scope="full")]
    first = compose(layers)
    second = compose([_layer("org", build_check_scope="full"), _layer("defaults", review_threshold="STANDARD")])
    assert first.surface_snapshot_id == second.surface_snapshot_id


def test_snapshot_excludes_session_overrides() -> None:
    """FR-13: session-layer changes do not move surface_snapshot_id."""
    base = [_layer("defaults", review_threshold="STANDARD")]
    without_session = compose(base)
    with_session = compose([*base, _layer("session", cost_budget_usd=5.0)])
    assert without_session.surface_snapshot_id == with_session.surface_snapshot_id


def test_session_override_hash_is_separate_sibling() -> None:
    """FR-13: the session delta lives in session_override_hash, not snapshot."""
    no_session = compose([_layer("defaults", review_threshold="STANDARD")])
    with_session = compose([_layer("defaults", review_threshold="STANDARD"), _layer("session", cost_budget_usd=5.0)])
    # Persistent snapshot unchanged; session hash differs.
    assert no_session.surface_snapshot_id == with_session.surface_snapshot_id
    assert no_session.session_override_hash != with_session.session_override_hash


def test_snapshot_stable_when_only_session_layer_changes() -> None:
    """FR-13: two sessions sharing a persistent surface aggregate together."""
    persistent = [_layer("defaults", review_threshold="STANDARD"), _layer("client")]
    s1 = compose([*persistent, _layer("session", cost_budget_usd=1.0)])
    s2 = compose([*persistent, _layer("session", cost_budget_usd=99.0)])
    assert s1.surface_snapshot_id == s2.surface_snapshot_id
    assert s1.session_override_hash != s2.session_override_hash


def test_client_layer_contributes_to_snapshot() -> None:
    """FR-13 / §7.2.1: client is a persistent layer and moves the snapshot."""
    without_client = compose([_layer("defaults", review_threshold="STANDARD")])
    with_client = compose([_layer("defaults", review_threshold="STANDARD"), _layer("client", cost_budget_usd=3.0)])
    assert without_client.surface_snapshot_id != with_client.surface_snapshot_id
