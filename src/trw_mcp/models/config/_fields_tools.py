"""Tool resolution, MCP server instructions, and tool description variant fields.

Covers PRD-CORE-218 FR04 (standard-default / explicit-all resolution mode) — the
production tool-exposure authority. The authoritative surface manifest and the
``resolve_tool_surface`` contract live in
``trw_mcp.server._surface_manifest_registry`` (a pure module). This mixin holds
only the ``tool_resolution_mode`` field and a thin wiring method that resolves
against the manifest via a call-time import — keeping ``models/config`` free of a
module-load dependency on ``trw_mcp.server`` (which eagerly registers tools).

The legacy PRD-CORE-125 ``tool_exposure_mode`` / ``tool_exposure_list`` fields
and their ``TOOL_PRESETS`` vocabulary were removed when the CORE-218 kernel/pack
resolver became the sole exposure authority (SurfaceAuthorityMiddleware) — no
dormant second authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

if TYPE_CHECKING:
    from trw_mcp.server._surface_manifest_registry import ToolResolution


class _ToolsFields:
    """Tool exposure domain mixin -- mixed into _TRWConfigFields via MI."""

    # -- Tool description + instruction toggles (surface control) --
    tool_descriptions_variant: Literal["default", "minimal", "verbose"] = "default"
    mcp_server_instructions_enabled: bool | None = None

    # -- Tool resolution mode (PRD-CORE-218 FR04) --
    # 'standard' is the DEFAULT (bounded kernel + task packs). 'all' is an
    # EXPLICIT operator choice that exposes the full eligible public surface and
    # is recorded in the resolution decision. A missing config field therefore
    # resolves to 'standard' — full exposure is never silently the default.
    tool_resolution_mode: Literal["standard", "all"] = "standard"

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

    def resolve_tool_surface_for_task(self, task_type: str | None) -> ToolResolution:
        """Resolve the tool surface for ``task_type`` under the configured mode.

        Wires ``tool_resolution_mode`` (FR04) into the authoritative manifest
        resolver so the config field is a live production input, not a facade.
        Imported at call time to avoid a ``models/config -> server`` module-load
        cycle (importing ``trw_mcp.server`` eagerly registers all tools).
        """
        from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

        return resolve_tool_surface(task_type, self.tool_resolution_mode)
