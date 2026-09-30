"""No removed tool is registered, aliased or redirected (PRD-CORE-300-NFR01).

PRD-CORE-300 deletes MCP tools with no aliases; each slice adds the names it
removes to ``surface_v2.RETIRED_TOOLS``. The FR01 scan that no rendered surface
still names one covers the whole monorepo, so it lives in the root suite
(``tests/test_retired_tool_surface_denylist.py``) with its monorepo-only scope
globs, not in this published package (MONOREPO-EXCLUSION-GLOBS).
"""

from __future__ import annotations

import pytest

from trw_mcp.models.surface_v2 import RETIRED_TOOLS

pytestmark = pytest.mark.unit

#: Every retired tool name, from the one registry (``surface_v2.RETIRED_TOOLS``) the dead-end census also reads.
REMOVED_TOOLS: tuple[str, ...] = tuple(sorted(RETIRED_TOOLS))


@pytest.mark.parametrize("name", REMOVED_TOOLS)
def test_a_removed_name_is_not_registered(name: str) -> None:
    """NFR01: no registration, alias or redirect exists for a removed name."""
    import asyncio

    from tests._served_app import served_app

    mcp = served_app()
    registered = {tool.name for tool in asyncio.run(mcp._list_tools())}
    assert name not in registered
