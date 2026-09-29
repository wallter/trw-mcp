"""Parity tests for client-profile documentation generated from runtime code."""

from __future__ import annotations

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

# The matrix/quick-reference renderer parity tests moved with the renderer to
# scripts/tests/test_render_client_profile_docs.py (PRD-CORE-313 FR05).
DOC_ROOT = (MONOREPO_ROOT or PACKAGE_ROOT.parent) / "docs"
OVERVIEW_DOC = DOC_ROOT / "CLIENT-PROFILES.md"


@requires_monorepo
def test_overview_doc_stays_within_350_loc() -> None:
    assert len(OVERVIEW_DOC.read_text(encoding="utf-8").splitlines()) <= 350


# --------------------------------------------------------------------------- #
# PRD-CORE-215-FR06: client-visible transport-loss retry protocol
# --------------------------------------------------------------------------- #

import re

from trw_mcp.bootstrap._client_integrations import (
    TRANSPORT_LOSS_PROTOCOL,
    TransportLossSafeAction,
    client_transport_guidance,
    render_transport_loss_guidance,
)
from trw_mcp.bootstrap._utils import SUPPORTED_IDES

# An *affirmative* claim that the server observed client receipt of bytes. The
# real guidance must never match this; the middleware only reports what it
# committed, never what the client received.
_SERVER_RECEIPT_CLAIM = re.compile(
    r"server\s+(?:observed|observes|knows|knew|saw|sees|confirmed|confirms|"
    r"detected|detects|verified|verifies|guarantees?)\b[^.]*\bclient\b[^.]*receiv",
    re.IGNORECASE,
)


def test_prd_core_215_fr06() -> None:
    """Each client-observed loss boundary maps to a safe, idempotent recovery."""
    guidance = render_transport_loss_guidance()

    # The protocol classifies exactly the four client-observed loss boundaries.
    keys = {b.key for b in TRANSPORT_LOSS_PROTOCOL}
    assert keys == {
        "loss_before_known_acceptance",
        "loss_after_returned_handle",
        "malformed_response",
        "server_restart_new_nonce",
    }

    by_key = {b.key: b for b in TRANSPORT_LOSS_PROTOCOL}
    for boundary in TRANSPORT_LOSS_PROTOCOL:
        # Every boundary is client-observed and picks one of the two safe actions.
        assert boundary.observed_by == "client"
        assert boundary.safe_action in {
            TransportLossSafeAction.REUSE_REQUEST_IDENTITY,
            TransportLossSafeAction.QUERY_OWNER_STATUS,
        }
        # ...and the guidance states that recovery via request identity or owner status.
        if boundary.safe_action is TransportLossSafeAction.REUSE_REQUEST_IDENTITY:
            assert "request identity" in boundary.safe_action_text
        else:
            assert "owner status locator" in boundary.safe_action_text
        # Each boundary is rendered into the guidance text.
        assert boundary.title in guidance

    # The post-acceptance-unknowable boundary preserves an `uncertain` outcome...
    assert by_key["loss_before_known_acceptance"].uncertainty_preserved is True
    assert by_key["malformed_response"].uncertainty_preserved is True
    assert by_key["server_restart_new_nonce"].uncertainty_preserved is True
    # ...while a returned durable handle is a KNOWN acceptance (not uncertain).
    assert by_key["loss_after_returned_handle"].uncertainty_preserved is False
    assert "uncertain" in guidance.lower()

    # Negative: no generated text attributes client-receipt knowledge to the server.
    assert _SERVER_RECEIPT_CLAIM.search(guidance) is None
    # The disclaimer explicitly DENIES server receipt knowledge (negation present).
    assert "never observes whether your client received" in guidance
    # And the detector actually discriminates — a fabricated affirmative claim matches.
    bogus = "The server observed that the client received every byte."
    assert _SERVER_RECEIPT_CLAIM.search(bogus) is not None

    # Every supported client profile's generated guidance includes the full protocol.
    for client_id in SUPPORTED_IDES:
        snippet = client_transport_guidance(client_id)
        assert "MCP transport-loss retry protocol" in snippet
        for boundary in TRANSPORT_LOSS_PROTOCOL:
            assert boundary.title in snippet
        assert _SERVER_RECEIPT_CLAIM.search(snippet) is None


# --------------------------------------------------------------------------- #
# PRD-CORE-218-FR06: truthful generated capability instructions
# --------------------------------------------------------------------------- #

from dataclasses import replace as _dc_replace

from trw_mcp.bootstrap._client_integrations import (
    CapabilityClass,
    ProjectionDriftKind,
    ProjectionFormat,
    ResolvedCapability,
    ResolvedProfile,
    SurfaceLifecycle,
    check_projection_parity,
    render_capability_projection,
    render_client_capability_instructions,
    resolved_profile_from_manifest_seam,
)


def _coding_profile() -> ResolvedProfile:
    """A resolved profile spanning both classes plus a retired capability.

    PRD-CORE-300 S11b flattened the surface to two classes: AVAILABLE (kernel +
    every always-on pack) and GATED (behind a config flag). The former
    DISCOVERABLE class (skill discovery / request_tool_access) is gone with
    those tools, so trw_prd_validate/trw_code are AVAILABLE (both are kernel).
    """
    return ResolvedProfile(
        task_type="coding",
        capabilities=(
            # available: kernel + every always-on pack
            ResolvedCapability("trw_session_start", "kernel", CapabilityClass.AVAILABLE),
            ResolvedCapability("trw_recall", "kernel", CapabilityClass.AVAILABLE),
            ResolvedCapability("trw_build_check", "kernel", CapabilityClass.AVAILABLE),
            ResolvedCapability("trw_prd_validate", "kernel", CapabilityClass.AVAILABLE),
            ResolvedCapability("trw_code", "kernel", CapabilityClass.AVAILABLE),
            # gated: behind a config flag
            ResolvedCapability("trw_dispatch", "dispatch", CapabilityClass.GATED),
            # retired: must NOT be advertised in any truthful projection
            ResolvedCapability(
                "trw_claude_md_sync",
                "run_maintenance",
                CapabilityClass.AVAILABLE,
                lifecycle=SurfaceLifecycle.RETIRED,
            ),
        ),
    )


def test_prd_core_218_fr06() -> None:
    """Client projections derive from the resolved profile and distinguish both classes."""
    profile = _coding_profile()

    # Two different client profiles / formats render the SAME semantic state.
    claude = render_capability_projection(profile, client_id="claude-code", fmt=ProjectionFormat.MARKDOWN_TABLE)
    codex = render_capability_projection(profile, client_id="codex", fmt=ProjectionFormat.BULLET_LIST)
    assert claude.fmt is not codex.fmt
    assert claude.semantic_state() == codex.semantic_state()

    # The two classes are distinguished, non-empty, and disjoint.
    assert claude.available == (
        "trw_build_check",
        "trw_code",
        "trw_prd_validate",
        "trw_recall",
        "trw_session_start",
    )
    assert claude.gated == ("trw_dispatch",)
    assert set(claude.available).isdisjoint(set(claude.gated))

    # The retired capability is advertised by NO class.
    everything = set(claude.available) | set(claude.gated)
    assert "trw_claude_md_sync" not in everything

    # A truthful, freshly-rendered projection has zero parity failures.
    assert check_projection_parity(profile, claude) == ()

    # The rendered human-readable instructions carry the two distinct class labels.
    text = render_client_capability_instructions(profile, client_id="claude-code")
    assert "Available in every session" in text
    assert "Behind a config flag" in text
    assert "trw_claude_md_sync" not in text

    # NEGATIVE — a stale declared count fails parity (count drift), typed.
    stale_count = _dc_replace(
        claude,
        declared_counts=(
            (CapabilityClass.AVAILABLE, 99),  # wrong
            (CapabilityClass.GATED, len(claude.gated)),
        ),
    )
    stale_failures = check_projection_parity(profile, stale_count)
    assert any(f.kind is ProjectionDriftKind.COUNT_DRIFT for f in stale_failures)

    # NEGATIVE — a lifecycle-drifted projection (retired tool still listed) fails.
    drifted = _dc_replace(
        claude,
        available=(*claude.available, "trw_claude_md_sync"),
        declared_counts=(
            (CapabilityClass.AVAILABLE, len(claude.available) + 1),
            (CapabilityClass.GATED, len(claude.gated)),
        ),
    )
    drift_failures = check_projection_parity(profile, drifted)
    assert any(f.kind is ProjectionDriftKind.LIFECYCLE_DRIFT for f in drift_failures)

    # Integration landed (FR01 manifest + seam exports in server/_tools.py):
    # the seam now resolves a real profile — deep assertions live in
    # test_fr06_manifest_seam_resolves_from_real_registry.
    assert resolved_profile_from_manifest_seam("coding") is not None


def test_fr06_manifest_seam_resolves_from_real_registry() -> None:
    """Integration wiring: with the FR01 manifest landed, the FR06 seam produces
    a REAL resolved profile — kernel + always-on packs available, flag-gated
    packs gated; retired tools not advertised (PRD-CORE-300 S11b: only the two
    surviving classes)."""
    from trw_mcp.bootstrap._client_integrations import (
        CapabilityClass,
        render_client_capability_instructions,
        resolved_profile_from_manifest_seam,
    )

    profile = resolved_profile_from_manifest_seam("coding")
    assert profile is not None  # the seam is live, not a fixture-only path
    by_class: dict[CapabilityClass, list[str]] = {}
    for capability in profile.capabilities:
        by_class.setdefault(capability.capability_class, []).append(capability.tool_id)
    assert "trw_session_start" in by_class[CapabilityClass.AVAILABLE]
    assert "trw_deliver" in by_class[CapabilityClass.AVAILABLE]
    assert "trw_build_check" in by_class[CapabilityClass.AVAILABLE]  # kernel now
    assert "trw_dispatch" in by_class[CapabilityClass.GATED]  # flag-gated pack
    assert set(by_class) == {CapabilityClass.AVAILABLE, CapabilityClass.GATED}
    text = render_client_capability_instructions(profile, client_id="claude-code")
    assert "trw_session_start" in text


def test_prd_core_218_nfr03() -> None:
    """NFR03: supported client projections encode the SAME capability + lifecycle
    semantic state despite format differences — zero semantic differences."""
    from itertools import cycle

    profile = _coding_profile()
    fmt_cycle = cycle((ProjectionFormat.MARKDOWN_TABLE, ProjectionFormat.BULLET_LIST))

    projections = []
    for client_id in SUPPORTED_IDES:
        proj = render_capability_projection(profile, client_id=client_id, fmt=next(fmt_cycle))
        # Each freshly-rendered projection is internally truthful (no drift).
        assert check_projection_parity(profile, proj) == (), client_id
        projections.append(proj)

    assert len(projections) == len(SUPPORTED_IDES)
    # Two distinct render formats were actually exercised across clients.
    assert len({p.fmt for p in projections}) == 2

    # Every supported client encodes the SAME semantic state despite the format
    # difference — zero semantic differences across the cross-client matrix.
    baseline = projections[0].semantic_state()
    for proj in projections[1:]:
        assert proj.semantic_state() == baseline

    # The RETIRED capability is advertised by NO client projection (lifecycle
    # state is preserved identically across every format).
    for proj in projections:
        advertised = set(proj.available) | set(proj.gated)
        assert "trw_claude_md_sync" not in advertised


# PRD-CORE-266-FR07: the dispatch-targets renderer tests moved with the renderer to
# scripts/tests/test_render_client_profile_docs.py (PRD-CORE-313 FR05).


@requires_monorepo
def test_overview_doc_points_at_the_generated_dispatch_table_and_restates_no_value() -> None:
    overview = OVERVIEW_DOC.read_text(encoding="utf-8")
    assert "Dispatch Targets" in overview
    # PRD-INFRA-174: no hand-written per-client dispatch capability value. The
    # client ids themselves appear in the overview for profile reasons, so the
    # census-relevant check is that no dispatch CAPABILITY value is restated.
    for capability in ("available_default_off", "primary_source", "executable ", "--allow-all-tools"):
        assert capability not in overview
