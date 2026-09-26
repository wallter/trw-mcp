"""The post-cut surface spec agrees with today's registry (PRD-CORE-300-FR01, S0).

``models/surface_v2.py`` states the 15-tool target once. These checks tie it to
``surface_packs.PACK_TOOLS`` so the removal set the slices work through (37
names) is derived, not copied: a tool added to the registry before the cut
lands shows up here as a 38th name to place. A slice moves names from the
registry to the denylist's ``REMOVED_TOOLS``, so the checks count both and no
slice edits a number here.
"""

from __future__ import annotations

import pytest

from tests.test_removed_tool_denylist import REMOVED_TOOLS
from trw_mcp.models.surface_packs import PACK_TOOLS
from trw_mcp.models.surface_v2 import (
    POST_CUT_FLAGGED,
    POST_CUT_KERNEL,
    POST_CUT_NEW_TOOLS,
    POST_CUT_REVIEWER_TOOLS,
    POST_CUT_SURFACE,
)

pytestmark = pytest.mark.unit

_REGISTERED_TODAY = frozenset(name for names in PACK_TOOLS.values() for name in names)
_REMOVED = frozenset(REMOVED_TOOLS)


def test_the_target_is_fifteen_distinct_tools_thirteen_by_default() -> None:
    default_off = {tool for tool, flag in POST_CUT_FLAGGED.items() if flag != "comms_enabled"}
    assert len(POST_CUT_KERNEL) == len(set(POST_CUT_KERNEL)) == 11
    assert len(POST_CUT_SURFACE) == 15
    assert len(POST_CUT_SURFACE - default_off) == 13


def test_every_surviving_tool_exists_today_except_the_new_ones() -> None:
    assert POST_CUT_SURFACE - POST_CUT_NEW_TOOLS <= _REGISTERED_TODAY
    assert not POST_CUT_NEW_TOOLS & _REMOVED


def test_the_removal_set_is_the_thirty_seven_names_the_map_places() -> None:
    assert not _REGISTERED_TODAY & _REMOVED, "a removed name is still in a pack"
    assert not _REMOVED & POST_CUT_SURFACE, "a surviving tool is on the denylist"
    assert len((_REGISTERED_TODAY | _REMOVED) - POST_CUT_NEW_TOOLS) == 51
    assert len((_REGISTERED_TODAY | _REMOVED) - POST_CUT_SURFACE) == 37


def test_the_final_reviewer_bound_is_inside_the_surface() -> None:
    assert POST_CUT_REVIEWER_TOOLS <= POST_CUT_SURFACE
