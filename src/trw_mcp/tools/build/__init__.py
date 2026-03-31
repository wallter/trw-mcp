"""TRW build verification gate tool — PRD-CORE-023, PRD-CORE-098.

Records build/test results for ceremony tracking and delivery gates.
Agents run tests via Bash and report results through ``trw_build_check``.
Phase gates consume cached ``BuildStatus`` from ``.trw/context/build-status.yaml``.

Language-agnostic: the tool accepts results from any build/test system.
No subprocess execution — agents run their own tools and report back.

Internal modules:
  _core        — caching utilities
  _registration — MCP tool registration (result reporter API)
"""

from __future__ import annotations

from trw_mcp.tools.build._core import (
    cache_build_status,
)
from trw_mcp.tools.build._registration import register_build_tools

__all__ = [
    "cache_build_status",
    "register_build_tools",
]
