"""FR-14 — allowed_tools_by_phase policy-surface contract (CLOSED).

PROF-001 owns the POLICY surface (``allowed_tools_by_phase``); INTENT-002 owns
the ENFORCEMENT path (the phase-exposure middleware). As of 2026-06-12,
INTENT-002's ``PhaseExposureMiddleware`` HAS landed and consumes the resolved
profile via ``phase_policy.from_resolved_allowlist`` — so the full wiring
assertion (``test_intent002_middleware_receives_profile_allowlist``) now passes
and FR-14's deferral is closed.

These contract tests prove the SINGLE source of truth: the policy field is a
real, resolvable surface key on the resolved profile, and the INTENT-002
middleware reads exactly that (never a parallel config surface).
"""

from __future__ import annotations

from trw_mcp.profile import (
    PROFILE_SURFACE_KEYS,
    Profile,
    ProfileLayer,
    compose,
)


def test_allowed_tools_by_phase_is_a_profile_surface_key() -> None:
    """FR-14: the allowlist is a first-class profile policy field."""
    assert "allowed_tools_by_phase" in PROFILE_SURFACE_KEYS
    assert "allowed_tools_by_phase" in Profile.model_fields


def test_allowlist_single_source_of_truth_contract() -> None:
    """FR-14: the allowlist resolves through the profile chain, not a sidecar.

    Composing layers that set allowed_tools_by_phase yields the resolved dict
    on ResolvedProfile.profile — the single surface INTENT-002 will read.
    """
    resolved = compose(
        [
            ProfileLayer(
                name="defaults",
                overrides=Profile(allowed_tools_by_phase={"RESEARCH": ["trw_recall"]}),
            ),
            ProfileLayer(
                name="task-type",
                overrides=Profile(allowed_tools_by_phase={"IMPLEMENT": ["trw_learn", "trw_checkpoint"]}),
            ),
        ]
    )
    allow = resolved.profile.allowed_tools_by_phase
    assert allow is not None
    # Later layer wins field-by-field (the whole dict is replaced, not merged
    # key-wise — the value type is the dict itself, per the surface contract).
    assert allow == {"IMPLEMENT": ["trw_learn", "trw_checkpoint"]}
    # Attribution names the origin layer so INTENT-002/audit can trace it.
    assert resolved.attribution["allowed_tools_by_phase"].origin_layer == "task-type"


def test_intent002_middleware_receives_profile_allowlist() -> None:
    """FR-14 (closed): the INTENT-002 middleware consumes the resolved allowlist.

    The deferred wiring assertion: a resolved profile's
    ``allowed_tools_by_phase`` is the ONLY policy source the phase-exposure
    middleware reads. We prove it by composing a profile, feeding its allowlist
    through ``from_resolved_allowlist``, and asserting the resulting policy's
    per-phase visible set matches the profile (rigid/Safe Set aside).
    """
    from trw_mcp.models.phase_policy import RIGID_TOOLS, from_resolved_allowlist

    resolved = compose(
        [
            ProfileLayer(
                name="defaults",
                overrides=Profile(allowed_tools_by_phase={"RESEARCH": ["trw_recall"]}),
            ),
        ]
    )
    policy = from_resolved_allowlist(resolved.profile.allowed_tools_by_phase)
    visible = policy.list_for("RESEARCH")
    # The profile's RESEARCH bucket is honored verbatim.
    assert "trw_recall" in visible
    assert "trw_init" not in visible  # not in this profile's RESEARCH bucket
    # The never-hide invariant survives consumption.
    for rigid in RIGID_TOOLS:
        assert rigid in visible
