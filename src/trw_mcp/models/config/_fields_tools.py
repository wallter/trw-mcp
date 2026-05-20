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
    code_index_enabled: bool = False
    code_index_max_file_bytes: int = Field(default=1_000_000, ge=1)
    code_index_exclude_dirs: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".trw",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
            "target",
            "venv",
        ]
    )
    code_index_include_extensions: list[str] = Field(
        default_factory=lambda: [
            ".go",
            ".js",
            ".jsx",
            ".md",
            ".py",
            ".rs",
            ".ts",
            ".tsx",
            ".yaml",
            ".yml",
        ]
    )
