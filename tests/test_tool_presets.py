"""PRD-CORE-218 / PRD-CORE-300 S11b: authoritative surface manifest, minimal
kernel, resolution.

The PRD-CORE-125 ``TOOL_PRESETS`` vocabulary was removed when the CORE-218
kernel/pack resolver became the sole tool-exposure authority (enforced by
``SurfaceAuthorityMiddleware``). PRD-CORE-300 S11b then deleted per-task pack
resolution entirely: the surface is flat — the kernel plus every pack whose
config flag is on, independent of task or run phase. These tests exercise the
manifest SSOT: the first-party security bridge covers the eligible public
surface, every registered tool resolves to exactly one manifest entry, the
kernel (``surface_v2.POST_CUT_KERNEL``, KERNEL_VERSION 4) is stable and
digest-pinned, and standard/all resolution is bounded + explainable.
"""

from __future__ import annotations

import pytest

from tests._layout import requires_local_timing
from tests._timing import assert_budget


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
    security bridge (the CORE-218 eligible public surface); PRD-CORE-300 S3a
    deleted the operator-only exception. This replaces the CORE-125 TOOL_PRESETS bridge-parity
    test and sources both sides from the manifest SSOT (no divergent second
    table can silently strand a tool ``tool_not_in_server_capabilities``)."""
    from trw_mcp.server._surface_manifest_registry import eligible_tool_names

    registered = _registered_production_tools()
    bridged = set(eligible_tool_names())

    unaccounted = registered - bridged
    assert not unaccounted, (
        "Registered tools not in the eligible public surface (first-party "
        f"bridge): {sorted(unaccounted)}. Add each to a pack in models/surface_packs.py."
    )


# =====================================================================
# PRD-CORE-218: authoritative surface manifest, minimal kernel, resolution
# =====================================================================

# Exact FR02 kernel membership — surface_v2.POST_CUT_KERNEL (PRD-CORE-300 S11b),
# all eleven registered since S10 landed trw_code.
# Hardcoded here (not imported from the manifest) so a silent membership drift
# is caught by THIS test.
_EXPECTED_KERNEL: frozenset[str] = frozenset(
    {
        "trw_session_start",
        "trw_init",
        "trw_status",
        "trw_recall",
        "trw_learn",
        "trw_checkpoint",
        "trw_deliver",
        "trw_build_check",
        "trw_review",
        "trw_prd_validate",
        "trw_code",
    }
)


@pytest.mark.unit
def test_prd_core_218_fr01() -> None:
    """FR01: every registered tool resolves to exactly one manifest entry; every
    entry has owner/pack/lifecycle; unmanifested tools and orphan entries fail."""
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
    """FR02: the kernel tools appear once in EVERY resolution regardless of mode
    or flags, no other tool is kernel, and a kernel-membership mutation without a
    version bump fails the pinned digest."""
    import hashlib

    from trw_mcp.server._surface_manifest_registry import (
        KERNEL_VERSION,
        KERNEL_VERSION_DIGESTS,
        MANIFEST_BY_NAME,
        PACK_TOOLS,
        kernel_digest,
        resolve_tool_surface,
    )

    # Kernel is EXACTLY the expected tool IDs — as a pack and in the manifest.
    assert set(PACK_TOOLS["kernel"]) == _EXPECTED_KERNEL
    kernel_pack_members = {n for n, e in MANIFEST_BY_NAME.items() if e.pack == "kernel"}
    assert kernel_pack_members == _EXPECTED_KERNEL  # no other tool is kernel

    # The kernel appears exactly once under every mode/flag combination — the
    # surface no longer depends on a task, so there is nothing left to vary but
    # the mode and the three flags.
    for mode in ("standard", "all", "unmapped-xyz"):
        for flags in (
            {},
            {"comms_enabled": True},
            {"dispatch_enabled": True},
            {"assess_enabled": True},
            {"comms_enabled": True, "dispatch_enabled": True, "assess_enabled": True},
        ):
            res = resolve_tool_surface(mode, **flags)
            assert res.packs[0] == "kernel"
            for tool in _EXPECTED_KERNEL:
                assert res.tools.count(tool) == 1, (mode, flags, tool)

    # Versioned kernel digest: current membership matches the pinned digest.
    assert kernel_digest() == KERNEL_VERSION_DIGESTS[KERNEL_VERSION]
    # A membership mutation changes the digest, so the pin fails until the
    # version is bumped and re-pinned (forces the versioned manifest diff).
    mutated = sorted(_EXPECTED_KERNEL | {"trw_dispatch"})
    mutated_digest = hashlib.sha256("\n".join(mutated).encode("utf-8")).hexdigest()
    assert mutated_digest != KERNEL_VERSION_DIGESTS[KERNEL_VERSION]


@pytest.mark.unit
def test_prd_core_218_nfr01() -> None:
    """NFR01: manifest + pack resolution is local and deterministic."""
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    # Determinism: identical inputs yield identical resolutions.
    assert resolve_tool_surface("standard") == resolve_tool_surface("standard")


@pytest.mark.unit
@requires_local_timing
def test_prd_core_218_nfr01_budget() -> None:
    """NFR01: manifest + pack resolution completes within 50 ms p95 over a 30-run fixture."""
    import time

    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        for mode in ("standard", "unmapped-xyz"):
            resolve_tool_surface(mode)
        resolve_tool_surface("all")
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[int(0.95 * (len(samples) - 1))]
    assert_budget("tool_surface_resolution_p95", p95, 50.0, "ms")


@pytest.mark.unit
def test_prd_core_218_fr04(config: object) -> None:
    """FR04: standard is the default; the surface is flat (no task input); only
    explicit-all turns on comms/assess (never dispatch) with a visible recorded
    decision; a flag widens standard by exactly its pack; an unrecognized mode
    degrades to standard, never silently to full."""
    from trw_mcp.models.surface_packs import PACK_TOOLS
    from trw_mcp.server._surface_manifest_registry import (
        eligible_tool_names,
        resolve_tool_surface,
    )

    # Missing config field -> standard is the DEFAULT (never silently full).
    assert config.tool_resolution_mode == "standard"  # type: ignore[attr-defined]

    # Standard with every flag off is exactly the kernel (PRD-CORE-300 S11b
    # deleted run_maintenance: trw_init joined the kernel, the rest moved to CLI).
    baseline = resolve_tool_surface("standard")
    assert baseline.mode == "standard"
    assert set(baseline.tools) == set(PACK_TOOLS["kernel"])
    assert "peer_comms" not in baseline.packs
    assert "dispatch" not in baseline.packs
    assert "assess_support" not in baseline.packs

    # Each flag widens standard by exactly its own pack's tools.
    with_comms = resolve_tool_surface("standard", comms_enabled=True)
    assert set(with_comms.tools) - set(baseline.tools) == set(PACK_TOOLS["peer_comms"])
    with_dispatch = resolve_tool_surface("standard", dispatch_enabled=True)
    assert set(with_dispatch.tools) - set(baseline.tools) == set(PACK_TOOLS["dispatch"])
    with_assess = resolve_tool_surface("standard", assess_enabled=True)
    assert set(with_assess.tools) - set(baseline.tools) == set(PACK_TOOLS["assess_support"])

    # Explicit all -> comms + assess turn on, dispatch stays off (FR09) unless
    # its own flag is on; the decision names what's still off.
    res_all = resolve_tool_surface("all")
    assert res_all.mode == "all"
    assert "explicit_all" in res_all.decision
    assert "dispatch" in res_all.decision
    assert set(res_all.tools) == set(eligible_tool_names()) - set(PACK_TOOLS["dispatch"])

    res_all_with_dispatch = resolve_tool_surface("all", dispatch_enabled=True)
    assert set(res_all_with_dispatch.tools) == set(eligible_tool_names())

    # Nothing else returns the full eligible set.
    full = set(eligible_tool_names())
    assert set(baseline.tools) != full
    assert set(with_comms.tools) != full
    assert set(res_all.tools) != full  # dispatch still off
    # An unrecognized mode value degrades to standard, never silently to full.
    bogus = resolve_tool_surface("bogus")
    assert bogus.mode == "standard"
    assert set(bogus.tools) == set(baseline.tools)
