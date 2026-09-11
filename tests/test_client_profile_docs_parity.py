"""Parity tests for client-profile documentation generated from runtime code."""

from __future__ import annotations

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo
from trw_mcp.client_profiles.markdown import render_matrix_page, render_quick_reference_table

DOC_ROOT = (MONOREPO_ROOT or PACKAGE_ROOT.parent) / "docs"
OVERVIEW_DOC = DOC_ROOT / "CLIENT-PROFILES.md"
MATRIX_DOC = DOC_ROOT / "client-profiles" / "matrix.md"


def _extract_table(doc_text: str, heading: str) -> str:
    lines = doc_text.splitlines()
    start = lines.index(heading)
    table_lines: list[str] = []
    for line in lines[start + 1 :]:
        if table_lines and not line.startswith("|"):
            break
        if line.startswith("|"):
            table_lines.append(line)
    return "\n".join(table_lines)


@requires_monorepo
def test_generated_matrix_doc_matches_renderer() -> None:
    assert MATRIX_DOC.read_text(encoding="utf-8") == render_matrix_page()


@requires_monorepo
def test_overview_quick_reference_matches_renderer() -> None:
    overview = OVERVIEW_DOC.read_text(encoding="utf-8")
    assert _extract_table(overview, "## Quick Reference") == render_quick_reference_table()


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
    """A resolved profile spanning all three classes plus a retired capability."""
    return ResolvedProfile(
        task_type="coding",
        capabilities=(
            # available: kernel + selected packs
            ResolvedCapability("trw_session_start", "kernel", CapabilityClass.AVAILABLE),
            ResolvedCapability("trw_recall", "kernel", CapabilityClass.AVAILABLE),
            ResolvedCapability("trw_build_check", "verification", CapabilityClass.AVAILABLE),
            # discoverable: reachable via skill discovery / request_tool_access
            ResolvedCapability("trw_prd_validate", "requirements", CapabilityClass.DISCOVERABLE),
            ResolvedCapability("trw_code_search", "code_navigation", CapabilityClass.DISCOVERABLE),
            # gated: operator-grant only
            ResolvedCapability("trw_meta_tune_rollback", "experimentation", CapabilityClass.GATED),
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
    """Client projections derive from the resolved profile and distinguish three classes."""
    profile = _coding_profile()

    # Two different client profiles / formats render the SAME semantic state.
    claude = render_capability_projection(profile, client_id="claude-code", fmt=ProjectionFormat.MARKDOWN_TABLE)
    codex = render_capability_projection(profile, client_id="codex", fmt=ProjectionFormat.BULLET_LIST)
    assert claude.fmt is not codex.fmt
    assert claude.semantic_state() == codex.semantic_state()

    # The three classes are distinguished, non-empty, and disjoint.
    assert claude.available == ("trw_build_check", "trw_recall", "trw_session_start")
    assert claude.discoverable == ("trw_code_search", "trw_prd_validate")
    assert claude.gated == ("trw_meta_tune_rollback",)
    all_sets = [set(claude.available), set(claude.discoverable), set(claude.gated)]
    for a in range(len(all_sets)):
        for b in range(a + 1, len(all_sets)):
            assert all_sets[a].isdisjoint(all_sets[b])

    # The retired capability is advertised by NO class.
    everything = set(claude.available) | set(claude.discoverable) | set(claude.gated)
    assert "trw_claude_md_sync" not in everything

    # A truthful, freshly-rendered projection has zero parity failures.
    assert check_projection_parity(profile, claude) == ()

    # The rendered human-readable instructions carry the three distinct class labels.
    text = render_client_capability_instructions(profile, client_id="claude-code")
    assert "Available now" in text
    assert "Discoverable via" in text
    assert "Operator-grant only" in text
    assert "trw_claude_md_sync" not in text

    # NEGATIVE — a stale declared count fails parity (count drift), typed.
    stale_count = _dc_replace(
        claude,
        declared_counts=(
            (CapabilityClass.AVAILABLE, 99),  # wrong
            (CapabilityClass.DISCOVERABLE, len(claude.discoverable)),
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
            (CapabilityClass.DISCOVERABLE, len(claude.discoverable)),
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
    """Integration wiring: with shard-E's FR01 manifest landed, the FR06 seam
    produces a REAL resolved profile — kernel available, high-risk packs
    gated, everything else discoverable; retired tools not advertised."""
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
    assert "trw_dispatch" in by_class[CapabilityClass.GATED]  # high-risk pack
    assert "trw_build_check" in by_class[CapabilityClass.DISCOVERABLE]
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
        advertised = set(proj.available) | set(proj.discoverable) | set(proj.gated)
        assert "trw_claude_md_sync" not in advertised


# --------------------------------------------------------------------------- #
# PRD-CORE-266-FR07: the dispatch capability table is generated, not written
# --------------------------------------------------------------------------- #

from trw_mcp.client_profiles.markdown import render_dispatch_targets_table
from trw_mcp.dispatch._client_specs import CLIENT_SPECS


def _table_row_ids(table: str) -> list[str]:
    """First cell of every body row, with the backticks stripped."""
    lines = [ln for ln in table.splitlines() if ln.startswith("|")]
    return [ln.split("|")[1].strip().strip("`") for ln in lines[2:]]


def test_dispatch_targets_table_matches_the_registry() -> None:
    table = render_dispatch_targets_table()
    assert _table_row_ids(table) == list(CLIENT_SPECS)


@requires_monorepo
def test_generated_matrix_page_carries_the_dispatch_targets_section() -> None:
    doc = MATRIX_DOC.read_text(encoding="utf-8")
    assert "## Dispatch Targets" in doc
    assert _extract_table(doc, "## Dispatch Targets") == render_dispatch_targets_table()


def test_dispatch_table_renders_the_verification_method_and_date_for_every_row() -> None:
    # The column that makes the table worth generating: a capability with no
    # recorded provenance is the failure mode PRD-CORE-266 exists to remove.
    table = render_dispatch_targets_table()
    for spec in CLIENT_SPECS.values():
        row = next(ln for ln in table.splitlines() if ln.startswith(f"| `{spec.client_id}` "))
        assert spec.verification.method in row
        assert spec.verification.verified_at.isoformat() in row


def test_dispatch_table_reports_sandbox_as_a_tri_state_not_on_off() -> None:
    table = render_dispatch_targets_table()
    assert "available_default_off" in table
    assert "| enforced |" in table
    copilot_row = next(ln for ln in table.splitlines() if ln.startswith("| `copilot` "))
    assert "available_default_off" in copilot_row


def test_dispatch_table_distinguishes_an_absent_profile_from_a_false_agent_surface() -> None:
    grok_row = next(ln for ln in render_dispatch_targets_table().splitlines() if ln.startswith("| `grok` "))
    cursor_row = next(ln for ln in render_dispatch_targets_table().splitlines() if ln.startswith("| `cursor-cli` "))
    assert "no client profile" in grok_row
    assert "| off |" in cursor_row


def test_adding_a_registry_entry_grows_the_table_by_exactly_one_row(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import date

    from trw_mcp.dispatch import _client_specs as specs

    before = len(_table_row_ids(render_dispatch_targets_table()))
    synthetic = specs.ClientSpec(
        client_id="synthetic-cli",
        binary="synthetic-cli",
        base_argv=("synthetic-cli",),
        version_argv=("--version",),
        output_shape="trailing_text",
        sub_agents="unknown",
        sandbox="none",
        verification=specs.ClientVerification(
            method="primary_source", evidence="synthetic fixture", verified_at=date(2026, 9, 5)
        ),
    )
    monkeypatch.setitem(specs.CLIENT_SPECS, "synthetic-cli", synthetic)
    rows = _table_row_ids(render_dispatch_targets_table())
    assert len(rows) == before + 1
    assert rows[-1] == "synthetic-cli"


@requires_monorepo
def test_overview_doc_points_at_the_generated_dispatch_table_and_restates_no_value() -> None:
    overview = OVERVIEW_DOC.read_text(encoding="utf-8")
    assert "Dispatch Targets" in overview
    # PRD-INFRA-174: no hand-written per-client dispatch capability value. The
    # client ids themselves appear in the overview for profile reasons, so the
    # census-relevant check is that no dispatch CAPABILITY value is restated.
    for capability in ("available_default_off", "primary_source", "executable ", "--allow-all-tools"):
        assert capability not in overview
