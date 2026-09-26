"""PRD-FIX-140-FR08 — generated instructions name only tools the reading role can call.

``trw-mcp check-instructions`` reported 38 unexposed tool references in this
repository's ``AGENTS.md`` on 2026-09-16, and every one of them came from text TRW
generates: the capability block enumerated every discoverable and operator-gated
tool by name, and the exposed baseline under-reported the never-hide set so the
Deliver Gate section was flagged for naming ``trw_build_check``.
"""

from __future__ import annotations

import pytest

from tests._layout import PACKAGE_ROOT, requires_monorepo
from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS, REVIEWER_TOOLS
from trw_mcp.state.claude_md._tool_manifest import resolve_exposed_tools, validate_instruction_manifest

_REPO_ROOT = PACKAGE_ROOT


class TestGeneratedInstructionsNameOnlyExposedTools:
    """The rendered block, the baseline, and this repository's own files."""

    def test_the_agent_baseline_is_the_kernel_plus_the_never_hide_set(self) -> None:
        """PRD-CORE-300 S11b: the agent baseline is now ALWAYS_ON_TOOLS — the
        kernel plus every pack no config flag gates."""
        resolved = resolve_exposed_tools("standard")

        assert resolved == frozenset(ALWAYS_ON_TOOLS)
        assert "trw_build_check" in resolved, "the Deliver Gate section names it as the remedy"
        assert "trw_review" in resolved

    def test_a_reviewer_lane_resolves_to_the_reviewer_surface(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A reviewer surface REPLACES the resolution, ahead of the always-on union."""
        from trw_mcp.state import _surface_role

        monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
        _surface_role.reset_surface_role_state()
        try:
            resolved = resolve_exposed_tools("standard")
        finally:
            _surface_role.reset_surface_role_state()

        assert resolved == frozenset(REVIEWER_TOOLS)
        assert "trw_build_check" not in resolved
        assert "trw_dispatch" not in resolved

    def test_the_capability_block_names_no_masked_tool(self) -> None:
        """PRD-CORE-300 S11b flattened the listing to available/gated (the
        discoverable tier and its meta tools are gone)."""
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

        assert projection.gated, "fixture lost its gated class — the check below would be vacuous"
        for tool in projection.gated:
            if tool in projection.available:
                continue
            assert tool not in rendered, f"{tool} is named in the generated block but is not available"
        assert "Available in every session" in rendered
        assert "Behind a config flag" in rendered

    def test_the_rendered_block_passes_the_parity_validator(self) -> None:
        from trw_mcp.bootstrap._client_integrations import (
            render_client_capability_instructions,
            resolved_profile_from_manifest_seam,
        )

        profile = resolved_profile_from_manifest_seam("coding")
        assert profile is not None
        rendered = render_client_capability_instructions(profile, client_id="claude-code")

        assert validate_instruction_manifest(rendered, resolve_exposed_tools("standard")) == []

    @requires_monorepo  # AGENTS.md / CLAUDE.md are private instruction files stripped from the public mirror
    @pytest.mark.parametrize("filename", ["AGENTS.md", "CLAUDE.md"])
    def test_check_instructions_is_clean_for_this_repository(self, filename: str) -> None:
        """The acceptance criterion, run against the real committed instruction files."""
        path = _REPO_ROOT / filename
        assert path.is_file()

        mismatches = validate_instruction_manifest(path.read_text(encoding="utf-8"), resolve_exposed_tools("standard"))

        assert mismatches == [], f"{filename} names tools the reading session cannot call: {mismatches}"

    def test_the_validator_still_catches_a_real_mismatch(self) -> None:
        """Non-vacuity: the clean result above must not come from a disabled validator."""
        mismatches = validate_instruction_manifest("Call trw_dispatch() now.", resolve_exposed_tools("standard"))

        assert mismatches == ["trw_dispatch"]


class TestTheGateNamesOnlyFieldsTheResponseCarries:
    """The gate text told every client to check a field the tool never returns.

    ``build_check_result`` lives in ceremony state (values passed/failed), read
    server-side by the delivery gate and the pre-tool hook. The tool's own
    response has ``tests_passed``/``static_checks_clean`` and never that key, so
    an agent following the instruction literally was looking for something it
    could not see -- and every client surface carried the claim.
    """

    def _gate_text(self) -> str:
        from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

        return render_deliver_gate_statement()

    def test_the_gate_names_no_field_absent_from_the_build_check_response(self) -> None:
        import re

        from trw_mcp.models.typed_dicts._tools import BuildCheckResultDict

        response_fields = set(BuildCheckResultDict.__annotations__)
        text = self._gate_text()
        # Only the clause that attributes a field to trw_build_check; the rest of
        # the gate legitimately names trw_deliver args and config keys.
        clause = next(line for line in text.splitlines() if "trw_build_check" in line)
        named = set(re.findall(r"`([a-z_]+)=", clause))

        assert named, "the clause must still name the field(s) a caller checks"
        assert named <= response_fields, (
            f"the gate tells every client to read {sorted(named - response_fields)} from "
            "trw_build_check, which the response does not carry"
        )

    def test_the_gate_does_not_resurrect_the_server_side_key(self) -> None:
        assert "build_check_result" not in self._gate_text(), (
            "build_check_result is ceremony state read by the gate and the hook, "
            "not something the calling agent can observe"
        )

    def test_the_clause_names_every_field_the_gate_predicate_reads(self) -> None:
        """Derived from the predicate, not restated (worker-1's review).

        A hard-coded pair passes unchanged when someone tightens the gate, which
        is how the instruction went stale the first time: WD-01 added test_count
        and scope to _build_pass_rejection and no text moved. Reading the
        predicate's own source means the next tightening turns this red.
        """
        import inspect
        import re

        from trw_mcp.tools import _delivery_build_gates

        source = inspect.getsource(_delivery_build_gates._build_pass_rejection)
        required = set(re.findall(r'data\.get\("([a-z_]+)"', source)) | set(re.findall(r'"([a-z_]+)" in data', source))
        required -= {"mypy_clean"}  # the legacy alias, named in the tool's own docstring
        clause = next(line for line in self._gate_text().splitlines() if "trw_build_check" in line)
        missing = sorted(field for field in required if field not in clause)

        assert required >= {"tests_passed", "static_checks_clean", "scope"}, (
            f"the predicate no longer reads what this test assumes: {sorted(required)}"
        )
        assert not missing, f"the gate predicate rejects on {missing}, which the instruction never mentions: {clause}"

    def test_the_clause_names_the_count_condition(self) -> None:
        """test_count is read through a helper, so the regex above cannot see it."""
        clause = next(line for line in self._gate_text().splitlines() if "trw_build_check" in line)
        assert "test_count" in clause, "a check that ran zero tests is rejected; the text must say so"
