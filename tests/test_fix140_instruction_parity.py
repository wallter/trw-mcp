"""PRD-FIX-140-FR08 — generated instructions name only tools the reading role can call.

``trw-mcp check-instructions`` reported 38 unexposed tool references in this
repository's ``AGENTS.md`` on 2026-09-16, and every one of them came from text TRW
generates: the capability block enumerated every discoverable and operator-gated
tool by name, and the exposed baseline under-reported the never-hide set so the
Deliver Gate section was flagged for naming ``trw_build_check``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.phase_policy import RIGID_TOOLS
from trw_mcp.models.surface_packs import KERNEL_TOOLS, REVIEWER_TOOLS
from trw_mcp.state.claude_md._tool_manifest import resolve_exposed_tools, validate_instruction_manifest

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestGeneratedInstructionsNameOnlyExposedTools:
    """The rendered block, the baseline, and this repository's own files."""

    def test_the_agent_baseline_is_the_kernel_plus_the_never_hide_set(self) -> None:
        resolved = resolve_exposed_tools("standard")

        assert resolved == frozenset(KERNEL_TOOLS) | RIGID_TOOLS
        assert "trw_build_check" in resolved, "the Deliver Gate section names it as the remedy"
        assert "trw_review" in resolved

    def test_a_reviewer_lane_resolves_to_the_reviewer_surface(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A reviewer surface REPLACES the resolution, ahead of the never-hide union."""
        from trw_mcp.state import _surface_role

        monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
        _surface_role.reset_surface_role_state()
        try:
            resolved = resolve_exposed_tools("standard")
        finally:
            _surface_role.reset_surface_role_state()

        assert resolved == frozenset(REVIEWER_TOOLS)
        assert "trw_build_check" not in resolved
        assert "trw_request_tool_access" not in resolved

    def test_the_capability_block_names_no_masked_tool(self) -> None:
        from trw_mcp.bootstrap._client_integrations import (
            ProjectionFormat,
            render_capability_projection,
            render_client_capability_instructions,
            resolved_profile_from_manifest_seam,
        )

        profile = resolved_profile_from_manifest_seam("coding")
        assert profile is not None
        rendered = render_client_capability_instructions(profile, client_id="claude-code")
        projection = render_capability_projection(profile, client_id="claude-code", fmt=ProjectionFormat.BULLET_LIST)

        assert projection.discoverable, "fixture lost its discoverable class — the check below would be vacuous"
        assert projection.gated, "fixture lost its gated class — the check below would be vacuous"
        for tool in (*projection.discoverable, *projection.gated):
            if tool in projection.available:
                continue
            assert tool not in rendered, f"{tool} is named in the generated block but is not available"
        assert "trw_request_tool_access(tool_name=..., reason=...)" in rendered
        assert "Available now" in rendered
        assert "Discoverable via" in rendered
        assert "Operator-grant only" in rendered

    def test_the_rendered_block_passes_the_parity_validator(self) -> None:
        from trw_mcp.bootstrap._client_integrations import (
            render_client_capability_instructions,
            resolved_profile_from_manifest_seam,
        )

        profile = resolved_profile_from_manifest_seam("coding")
        assert profile is not None
        rendered = render_client_capability_instructions(profile, client_id="claude-code")

        assert validate_instruction_manifest(rendered, resolve_exposed_tools("standard")) == []

    @pytest.mark.parametrize("filename", ["AGENTS.md", "CLAUDE.md"])
    def test_check_instructions_is_clean_for_this_repository(self, filename: str) -> None:
        """The acceptance criterion, run against the real committed instruction files."""
        path = _REPO_ROOT / filename
        assert path.is_file()

        mismatches = validate_instruction_manifest(path.read_text(encoding="utf-8"), resolve_exposed_tools("standard"))

        assert mismatches == [], f"{filename} names tools the reading session cannot call: {mismatches}"

    def test_the_validator_still_catches_a_real_mismatch(self) -> None:
        """Non-vacuity: the clean result above must not come from a disabled validator."""
        mismatches = validate_instruction_manifest("Call trw_pipeline_health() now.", resolve_exposed_tools("standard"))

        assert mismatches == ["trw_pipeline_health"]
