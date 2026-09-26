"""Tool resolution, MCP server instructions, and tool description variant fields.

Covers PRD-CORE-218 FR04 (standard-default / explicit-all resolution mode) — the
production tool-exposure authority. The authoritative surface manifest and the
``resolve_tool_surface`` contract live in
``trw_mcp.server._surface_manifest_registry``. This mixin holds the
``tool_resolution_mode`` and ``surface_role`` fields the surface authority reads.

The legacy PRD-CORE-125 ``tool_exposure_mode`` / ``tool_exposure_list`` fields
and their ``TOOL_PRESETS`` vocabulary were removed when the CORE-218 kernel/pack
resolver became the sole exposure authority (SurfaceAuthorityMiddleware) — no
dormant second authority.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from trw_mcp.code_index.bounds import CodeIndexBounds


class _ToolsFields:
    """Tool exposure domain mixin -- mixed into _TRWConfigFields via MI."""

    # tool_descriptions_variant and mcp_server_instructions_enabled were
    # removed under PRD-CORE-291 (slice 2): both fed only the SurfaceConfig
    # projection / resolve_surface() consumer removed in 2.0.0 (WD-02), so
    # neither reached a live gate.

    # -- Tool resolution mode (PRD-CORE-218 FR04, flattened by PRD-CORE-300 S11b) --
    # 'standard' is the DEFAULT: the kernel plus every pack whose config flag is
    # on. 'all' is an EXPLICIT operator choice that also turns on the comms and
    # assess packs; the dispatch pack still needs dispatch_tools_exposed (FR09).
    tool_resolution_mode: Literal["standard", "all"] = "standard"

    # -- Session identity: WHO this process is (PRD-SEC-015 FR02) --
    # 'agent' is the default, so every existing session is unchanged. 'reviewer'
    # is set per PROCESS by the dispatch layer (TRW_SURFACE_ROLE=reviewer) and
    # bounds the server to the read-only REVIEWER_TOOLS surface, DOMINATING
    # tool_resolution_mode (including 'all') and the pack flags.
    # Deliberately TOP-LEVEL rather than nested: the env selection this field
    # exists for is the flat `TRW_<KEY_UPPER>` form the loader filters on, which
    # a nested field could not use.
    surface_role: Literal["agent", "reviewer"] = "agent"

    @field_validator("surface_role", mode="before")
    @classmethod
    def _normalize_surface_role(cls, value: object) -> object:
        """Case/whitespace-normalize BEFORE the Literal check.

        Mirrors ``surface_authority._env_marks_reviewer``'s tolerance (``.strip()
        .lower()``): ``" Reviewer "`` must resolve the same way through the typed
        field as it already does through the raw environment read, or the two
        mechanisms silently disagree on the same input. A value that still is
        not ``agent``/``reviewer`` after normalization is a genuine typo and is
        passed through UNCHANGED so the Literal validator's error message names
        the value the operator actually set, not a lowercased guess.
        """
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("agent", "reviewer"):
                return normalized
        return value

    # code_index_enabled removed under PRD-CORE-291 (slice 2): the code-index
    # feature has no reader that gates on it -- only a test pinned the default.
    # tool_access_grant_max_ttl_seconds removed with the grant tool (PRD-CORE-300 S11b).
    code_index_max_file_bytes: int = Field(
        default=1_000_000,
        ge=1,
        description=(
            "Largest file the code index reads. A value above 1 MiB (1,048,576) acts as 1 MiB: the store's "
            "per-value SQLite length limit is sized from it (rc7 C12)."
        ),
    )
    code_index_bounds: CodeIndexBounds = Field(
        default_factory=CodeIndexBounds,
        description=(
            "PRD-CORE-300-FR15 build and query budgets for the local code index: directory entries,"
            " files, source bytes and wall-clock per build; query characters and terms, candidate rows,"
            " response bytes and wall-clock per query. Crossing one fails with index_bound_exceeded naming the key."
        ),
    )
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
