"""Tool exposure, MCP server instructions, and tool description variant fields.

Covers PRD-CORE-125 surface area control:
  - Tool exposure mode and custom list
  - Tool description variant
  - MCP server instructions toggle
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field


class _ToolsFields:
    """Tool exposure domain mixin -- mixed into _TRWConfigFields via MI."""

    # -- Tool exposure (S1, PRD-CORE-125) --

    tool_exposure_mode: Literal["all", "core", "minimal", "standard", "custom"] = "all"
    tool_exposure_list: list[str] = Field(default_factory=list)
    tool_descriptions_variant: Literal["default", "minimal", "verbose"] = "default"
    mcp_server_instructions_enabled: bool | None = None
