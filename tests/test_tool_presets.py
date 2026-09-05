"""PRD-CORE-218: authoritative surface manifest, minimal kernel, resolution.

The PRD-CORE-125 ``TOOL_PRESETS`` vocabulary was removed when the CORE-218
kernel/pack resolver became the sole tool-exposure authority (enforced by
``SurfaceAuthorityMiddleware``). These tests exercise the manifest SSOT: the
first-party security bridge covers the eligible public surface, every registered
tool resolves to exactly one manifest entry, the nine-tool kernel is stable and
digest-pinned, and standard/all resolution is bounded + explainable.
"""

from __future__ import annotations

import pytest


def _registered_production_tools() -> set[str]:
    """Return the full set of tool names registered on a FRESH production server.

    Iterates ``server/_tools._tool_registrars()`` — the SAME tuple production
    boot walks — rather than re-listing the registrars by hand. A hand-copied
    list is the drift the single-source tuple exists to prevent: it silently
    kept passing after a registrar was removed, because it imported the dead
    module itself.
    """
    import asyncio

    from fastmcp import FastMCP

    from trw_mcp.server._tools import _tool_registrars

    server = FastMCP("parity-probe")
    for fn in _tool_registrars():
        fn(server)
    return {t.name for t in asyncio.run(server.list_tools())}


def test_first_party_bridge_parity_over_manifest() -> None:
    """Every REGISTERED production tool is reachable through the first-party
    security bridge (the CORE-218 eligible public surface) OR is an explicit
    operator-only tool. This replaces the CORE-125 TOOL_PRESETS bridge-parity
    test and sources both sides from the manifest SSOT (no divergent second
    table can silently strand a tool ``tool_not_in_server_capabilities``)."""
    from trw_mcp.models.surface_packs import OPERATOR_ONLY_TOOLS
    from trw_mcp.server._surface_manifest_registry import eligible_tool_names

    registered = _registered_production_tools()
    bridged = set(eligible_tool_names())
    operator_only = set(OPERATOR_ONLY_TOOLS)

    unaccounted = registered - bridged - operator_only
    assert not unaccounted, (
        "Registered tools neither in the eligible public surface (first-party "
        f"bridge) nor operator-only: {sorted(unaccounted)}. Add each to a pack in "
        "models/surface_packs.py or mark it OPERATOR_ONLY_TOOLS."
    )
    # No phantom operator-only names, and the two sets never overlap.
    assert not (operator_only - registered), sorted(operator_only - registered)
    assert not (bridged & operator_only), "a tool is both bridged and operator-only"


# =====================================================================
# PRD-CORE-218: authoritative surface manifest, minimal kernel, resolution
# =====================================================================

# Exact FR02 kernel membership — the nine tool IDs. Hardcoded here (not imported
# from the manifest) so a silent membership drift is caught by THIS test.
_EXPECTED_KERNEL: frozenset[str] = frozenset(
    {
        "trw_session_start",
        "trw_status",
        "trw_recall",
        "trw_learn",
        "trw_checkpoint",
        "trw_deliver",
        "trw_skill_discovery",
        "trw_request_tool_access",
        "trw_profile_explain",
    }
)


@pytest.mark.unit
def test_prd_core_218_fr01() -> None:
    """FR01: every registered tool resolves to exactly one manifest entry; every
    entry has owner/pack/lifecycle; unmanifested tools and orphan entries fail."""
    from trw_mcp.models.surface_packs import OPERATOR_ONLY_TOOLS
    from trw_mcp.server._surface_manifest_registry import (
        MANIFEST_BY_NAME,
        TOOL_MANIFEST,
        SurfaceKind,
        SurfaceManifestEntry,
    )
    from trw_mcp.server._tools import raw_registered_tool_names

    registered = raw_registered_tool_names()
    manifest_names = set(MANIFEST_BY_NAME)

    # Bijection: exactly one manifest entry per registered tool, no orphans.
    assert registered == manifest_names, {
        "unmanifested": sorted(registered - manifest_names),
        "orphans": sorted(manifest_names - registered),
    }
    # Exactly one entry per tool (no duplicate names in the manifest).
    assert len(TOOL_MANIFEST) == len(manifest_names) == len(registered)

    # No entry lacks an owner, pack, lifecycle, kind, or validation reference.
    for entry in TOOL_MANIFEST:
        assert entry.kind is SurfaceKind.TOOL
        assert entry.owner, entry.name
        assert entry.pack, entry.name
        assert entry.lifecycle is not None, entry.name
        assert entry.validation_reference, entry.name

    # Manifest public-status is consistent with the operator-only SSOT.
    non_public = {e.name for e in TOOL_MANIFEST if not e.public}
    assert non_public == set(OPERATOR_ONLY_TOOLS)

    # Negative: a registered tool absent from the manifest is detected typed.
    fake_registered = registered | {"trw_fixture_unmanifested"}
    assert fake_registered - manifest_names == {"trw_fixture_unmanifested"}

    # Negative: a manifest entry with no registered consumer is detected typed.
    orphan = SurfaceManifestEntry(
        name="trw_fixture_orphan",
        kind=SurfaceKind.TOOL,
        owner="tools.fixture",
        pack="feedback",
        validation_reference="fixture",
    )
    fake_manifest = manifest_names | {orphan.name}
    assert fake_manifest - registered == {"trw_fixture_orphan"}


@pytest.mark.unit
def test_prd_core_218_fr02() -> None:
    """FR02: exactly nine kernel tools appear once in every profile resolution,
    no other tool is kernel, pack tools need explicit selection, and a
    kernel-membership mutation without a version bump fails the pinned digest."""
    import hashlib

    from trw_mcp.server._surface_manifest_registry import (
        KERNEL_VERSION,
        KERNEL_VERSION_DIGESTS,
        MANIFEST_BY_NAME,
        PACK_TOOLS,
        kernel_digest,
        resolve_tool_surface,
    )

    # Kernel is EXACTLY the nine tool IDs — as a pack and in the manifest.
    assert set(PACK_TOOLS["kernel"]) == _EXPECTED_KERNEL
    assert len(PACK_TOOLS["kernel"]) == 9
    kernel_pack_members = {n for n, e in MANIFEST_BY_NAME.items() if e.pack == "kernel"}
    assert kernel_pack_members == _EXPECTED_KERNEL  # no other tool is kernel

    # The nine appear exactly once in EVERY profile resolution (real TaskType
    # vocabulary; F2: 'audit' is not a TaskType and resolves kernel-only).
    for task in ("coding", "research", "docs", "eval", "rca", "planning", "unknown", "unmapped-xyz"):
        res = resolve_tool_surface(task, "standard")
        assert res.packs[0] == "kernel"
        for tool in _EXPECTED_KERNEL:
            assert res.tools.count(tool) == 1, (task, tool)
    res_all = resolve_tool_surface("coding", "all")
    for tool in _EXPECTED_KERNEL:
        assert res_all.tools.count(tool) == 1

    # Pack tools appear ONLY through explicit selection: code_risk is not part
    # of any standard task surface.
    for task in ("coding", "research", "docs", "eval", "rca", "planning", "unknown"):
        surface = set(resolve_tool_surface(task, "standard").tools)
        assert not (set(PACK_TOOLS["code_risk"]) & surface), task

    # Versioned kernel digest: current membership matches the pinned digest.
    assert kernel_digest() == KERNEL_VERSION_DIGESTS[KERNEL_VERSION]
    # A membership mutation changes the digest, so the pin fails until the
    # version is bumped and re-pinned (forces the versioned manifest diff).
    mutated = sorted(_EXPECTED_KERNEL | {"trw_build_check"})
    mutated_digest = hashlib.sha256("\n".join(mutated).encode("utf-8")).hexdigest()
    assert mutated_digest != KERNEL_VERSION_DIGESTS[KERNEL_VERSION]


@pytest.mark.unit
def test_prd_core_218_nfr01() -> None:
    """NFR01: manifest + pack resolution is local, deterministic, and completes
    within 50 ms p95 over a 30-run fixture."""
    import time

    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    # Determinism: identical inputs yield identical resolutions.
    assert resolve_tool_surface("coding", "standard") == resolve_tool_surface("coding", "standard")

    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        for task in ("coding", "docs", "audit", "unknown", "unmapped-xyz"):
            resolve_tool_surface(task, "standard")
        resolve_tool_surface("coding", "all")
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[int(0.95 * (len(samples) - 1))]
    assert p95 <= 50.0, f"resolution p95={p95:.3f}ms"


@pytest.mark.unit
def test_prd_core_218_fr04(config: object) -> None:
    """FR04: standard is the default; unknown -> kernel only; standard applies
    the task mapping; only explicit-all returns the full eligible set with a
    visible recorded decision, and nothing else returns the full set."""
    from trw_mcp.server._surface_manifest_registry import (
        eligible_tool_names,
        resolve_tool_surface,
    )

    # Missing config field -> standard is the DEFAULT (never silently full).
    assert config.tool_resolution_mode == "standard"  # type: ignore[attr-defined]
    # Wiring: the config field is a live production input to resolution.
    wired = config.resolve_tool_surface_for_task("coding")  # type: ignore[attr-defined]
    assert wired.mode == "standard"
    assert len(wired.tools) == 15

    # PRD-CORE-246-FR05: an unmapped or missing task falls back to the
    # ``unknown`` packs (kernel + verification), NOT to kernel only — the
    # declared authority must state what the runtime actually exposes.
    unknown = resolve_tool_surface("totally-unknown", "standard")
    assert unknown.packs == ("kernel", "verification")
    assert len(unknown.tools) == 11
    assert "unknown fallback" in unknown.decision
    assert len(resolve_tool_surface(None, "standard").tools) == 11
    assert len(resolve_tool_surface("unknown", "standard").tools) == 11

    # Standard -> exact task mapping over the REAL TaskType vocabulary (F2).
    assert len(resolve_tool_surface("coding", "standard").tools) == 15
    assert len(resolve_tool_surface("research", "standard").tools) == 15
    assert len(resolve_tool_surface("docs", "standard").tools) == 14
    assert len(resolve_tool_surface("eval", "standard").tools) == 11
    assert len(resolve_tool_surface("rca", "standard").tools) == 15
    assert len(resolve_tool_surface("planning", "standard").tools) == 12
    # F2 tombstone: 'audit' is NOT a TaskType. Since PRD-CORE-246-FR05 it
    # resolves to the verification-bearing ``unknown`` fallback (11) rather than
    # silently to kernel only (9), which was the shape the tombstone recorded.
    assert len(resolve_tool_surface("audit", "standard").tools) == 11

    # Explicit all -> full eligible set WITH a visible recorded decision.
    full = set(eligible_tool_names())
    res_all = resolve_tool_surface("coding", "all")
    assert set(res_all.tools) == full
    assert res_all.mode == "all"
    assert "explicit_all" in res_all.decision

    # Nothing else returns the full set — no standard resolution equals `all`.
    for task in ("coding", "research", "docs", "eval", "rca", "planning", "unknown", None):
        assert set(resolve_tool_surface(task, "standard").tools) != full
    # An unrecognized mode value degrades to standard, never silently to full.
    assert set(resolve_tool_surface("coding", "bogus").tools) != full
