"""PRD-CORE-300 S11b: the kernel IS the post-cut kernel, pinned once.

``models/surface_packs.KERNEL_TOOLS`` restates ``surface_v2.POST_CUT_KERNEL``
(it stays import-free so scripts can load it by path). S12's alwaysLoad floor
derives from ``POST_CUT_KERNEL``; these tests keep the two identical, require
every kernel tool to be registered, and hold the kernel digest to one version.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.surface_packs import (
    FLAG_GATED_PACKS,
    KERNEL_TOOLS,
    PACK_TOOLS,
    REVIEWER_TOOLS,
)
from trw_mcp.models.surface_v2 import POST_CUT_FLAGGED, POST_CUT_KERNEL, POST_CUT_REVIEWER_TOOLS
from trw_mcp.server._surface_manifest_registry import (
    KERNEL_VERSION,
    KERNEL_VERSION_DIGESTS,
    eligible_tool_names,
    kernel_digest,
)
from trw_mcp.server._tools import raw_registered_tool_names

pytestmark = pytest.mark.unit


def test_kernel_tools_equal_post_cut_kernel() -> None:
    assert KERNEL_TOOLS == POST_CUT_KERNEL


def test_reviewer_tools_equal_post_cut_reviewer_tools() -> None:
    """NFR02 end state: the reviewer bound is exactly trw_recall and trw_code."""
    assert REVIEWER_TOOLS == POST_CUT_REVIEWER_TOOLS == frozenset({"trw_recall", "trw_code"})


def test_every_kernel_tool_is_registered_and_in_the_kernel_pack() -> None:
    # S10 registered trw_code, the last post-cut kernel name to land.
    assert set(POST_CUT_KERNEL) <= raw_registered_tool_names()
    assert PACK_TOOLS["kernel"] == KERNEL_TOOLS


def test_the_manifest_is_a_bijection_with_the_registrar() -> None:
    assert set(eligible_tool_names()) == raw_registered_tool_names()


def test_the_kernel_digest_is_pinned_at_version_four() -> None:
    assert KERNEL_VERSION == 4
    assert kernel_digest() == KERNEL_VERSION_DIGESTS[4]


def test_flag_gated_packs_match_the_post_cut_flags() -> None:
    """Every surviving flagged tool's pack is gated by the flag the spec names."""
    pack_of = {tool: pack for pack, tools in PACK_TOOLS.items() for tool in tools}
    for tool, flag in POST_CUT_FLAGGED.items():
        if tool in pack_of:
            assert FLAG_GATED_PACKS.get(pack_of[tool]) == flag, tool
    assert not set(FLAG_GATED_PACKS) & {"kernel"}


def test_reviewer_set_is_registered_and_holds_a_code_navigation_tool() -> None:
    """NFR02 invariants at S11b: a subset of the registered tools, with code search."""
    assert REVIEWER_TOOLS <= raw_registered_tool_names()
    assert "trw_code" in REVIEWER_TOOLS
    assert "trw_recall" in REVIEWER_TOOLS
