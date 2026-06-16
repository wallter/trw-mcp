"""§8.2 — property-based composition tests (PRD-HPO-PROF-001).

Covers override determinism (same layers → same ResolvedProfile + snapshot id)
and invariants-fail-closed (any review_threshold=NONE prod profile raises).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from trw_mcp.profile import (
    InvariantViolationError,
    Profile,
    ProfileLayer,
    compose,
)

_TIERS = st.sampled_from(["MINIMAL", "STANDARD", "COMPREHENSIVE"])
_SCOPES = st.sampled_from(["targeted", "full"])  # 'none' needs env=dev; excluded
_REVIEWS = st.sampled_from(["MINIMAL", "STANDARD", "COMPREHENSIVE"])


def _layer_strategy(name: str) -> st.SearchStrategy[ProfileLayer]:
    return st.builds(
        lambda tier, scope, review: ProfileLayer(
            name=name,
            overrides=Profile(
                ceremony_tier=tier,
                build_check_scope=scope,
                review_threshold=review,
            ),
        ),
        tier=st.none() | _TIERS,
        scope=st.none() | _SCOPES,
        review=st.none() | _REVIEWS,
    )


@given(
    defaults=_layer_strategy("defaults"),
    org=_layer_strategy("org"),
    task=_layer_strategy("task-type"),
)
def test_override_determinism(defaults: ProfileLayer, org: ProfileLayer, task: ProfileLayer) -> None:
    """Two compositions of the same layers are identical (determinism)."""
    layers = [defaults, org, task]
    first = compose(layers)
    # Reverse the input order; canonical ordering must yield the same result.
    second = compose(list(reversed(layers)))
    assert first.profile == second.profile
    assert first.surface_snapshot_id == second.surface_snapshot_id
    assert first.session_override_hash == second.session_override_hash


@given(env=st.none() | st.just("prod"))
def test_invariants_fail_closed_review_none(env: str | None) -> None:
    """review_threshold=NONE outside dev always raises — no silent path."""
    layer = ProfileLayer(
        name="org",
        overrides=Profile(review_threshold="NONE", env=env),
    )
    try:
        compose([layer])
        raised = False
    except InvariantViolationError:
        raised = True
    assert raised, "non-dev review_threshold=NONE must fail closed"
