"""PRD-FIX-140 FR06/FR07 — every advisory naming an unreachable tool says how to reach it.

The 2026-09-16 probe measured 15 of 53 tools exposed while server advisories kept
saying ``call trw_pipeline_health()`` and ``Run trw_code_index_update for this
repo first.`` with no mention of ``trw_request_tool_access``. These tests EXECUTE
the advisory producers rather than grepping their source, so an advisory that
stops carrying the step fails here even if the literal survives somewhere.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.phase_policy import RIGID_TOOLS
from trw_mcp.models.surface_packs import KERNEL_TOOLS, OPERATOR_ONLY_TOOLS, STANDARD_TASK_PACKS
from trw_mcp.tools._masked_tool_hint import effective_tool_surface, unmask_hint

_GRANT_LITERAL = "trw_request_tool_access(tool_name="


def test_resolution_matches_the_real_resolver() -> None:
    """The pure re-implementation must equal the server's own resolver.

    ``_masked_tool_hint`` resolves from ``models/surface_packs`` to avoid importing
    ``server._surface_manifest_registry`` (which eagerly registers the tool
    surface). This pins the two against each other for every task type plus the
    unmapped and unresolvable cases, so the copy cannot drift into a second
    opinion.
    """
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    for task_type in [None, "", "nonsense", *STANDARD_TASK_PACKS]:
        expected = set(resolve_tool_surface(task_type, "standard").tools) | set(RIGID_TOOLS) | {"trw_init"}
        assert effective_tool_surface(task_type) == expected, task_type


class TestEveryMaskedToolAdvisoryCarriesTheUnmaskStep:
    """FR06 — the producers, executed."""

    def test_a_masked_tool_advisory_carries_the_grant_step(self) -> None:
        hint = unmask_hint("trw_pipeline_health", reason="inspect degraded pipeline signals")

        assert _GRANT_LITERAL in hint
        assert "trw_pipeline_health" in hint

    def test_the_session_start_pipeline_advisory_carries_it(self) -> None:
        from trw_mcp.tools._ceremony_pipeline_advisory import _pipeline_health_unmask_hint

        assert _GRANT_LITERAL in _pipeline_health_unmask_hint()

    def test_the_aggregate_pipeline_advisory_carries_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.tools._pipeline_health as ph

        degraded = {"degraded": True, "measured": True}
        healthy = {"degraded": False, "measured": True}
        monkeypatch.setattr(ph, "probe_sync_push", lambda *a, **k: degraded)
        monkeypatch.setattr(ph, "probe_graph_edges", lambda *a, **k: healthy)
        monkeypatch.setattr(ph, "probe_embedding_coverage", lambda *a, **k: healthy)
        monkeypatch.setattr(ph, "probe_recall_feedback", lambda *a, **k: healthy)
        monkeypatch.setattr(ph, "probe_bandit_state", lambda *a, **k: healthy)

        with tempfile.TemporaryDirectory() as tmp:
            result: dict[str, Any] = dict(ph.step_pipeline_health(Path(tmp) / ".trw"))

        assert result["degraded"] is True
        assert _GRANT_LITERAL in str(result["advisory"])

    @pytest.mark.parametrize("tool", ["trw_code_search", "trw_code_symbol"])
    def test_both_missing_index_remediations_carry_it(self, tool: str) -> None:
        from trw_mcp.tools import code_search

        with tempfile.TemporaryDirectory() as tmp:
            payload = (
                code_search.trw_code_search(repo_root=tmp, query="x")
                if tool == "trw_code_search"
                else code_search.trw_code_symbol(repo_root=tmp, symbol="x")
            )

        assert payload["error_code"] == "missing_index"
        assert "trw_code_index_update" in str(payload["remediation"])
        assert _GRANT_LITERAL in str(payload["remediation"])

    def test_the_producer_inventory_is_not_empty(self) -> None:
        """Guards the scan above against passing vacuously if a producer is deleted."""
        producers = [
            Path("src/trw_mcp/tools/_ceremony_pipeline_advisory.py"),
            Path("src/trw_mcp/tools/_pipeline_health.py"),
            Path("src/trw_mcp/tools/code_search.py"),
        ]
        root = Path(__file__).resolve().parents[1]
        present = [p for p in producers if (root / p).is_file() and "unmask_hint" in (root / p).read_text()]

        assert len(present) == len(producers), f"advisory producers lost the hint: {present}"


class TestHintIsWithheldWhereGrantingIsNotAvailable:
    """FR07 — the three exception classes, plus fail-quiet."""

    def test_an_exposed_kernel_tool_gets_no_hint(self) -> None:
        for tool in KERNEL_TOOLS:
            assert unmask_hint(tool, reason="x") == "", tool

    def test_a_never_hidden_tool_gets_no_hint(self) -> None:
        for tool in RIGID_TOOLS:
            assert unmask_hint(tool, reason="x") == "", tool

    def test_an_operator_only_tool_gets_no_hint(self) -> None:
        for tool in OPERATOR_ONLY_TOOLS:
            assert unmask_hint(tool, reason="x") == "", tool

    def test_a_reviewer_lane_gets_no_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A reviewer can neither list nor call trw_request_tool_access (PRD-SEC-015)."""
        from trw_mcp.state import _surface_role

        monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
        _surface_role.reset_surface_role_state()
        try:
            assert unmask_hint("trw_pipeline_health", reason="x") == ""
        finally:
            _surface_role.reset_surface_role_state()

    def test_a_granted_tool_gets_no_hint(self) -> None:
        from trw_mcp.tools.phase_overrides import grant_override, reset_overrides

        reset_overrides()
        try:
            grant_override("sid", "trw_pipeline_health", reason="checking the pipeline")
            assert unmask_hint("trw_pipeline_health", reason="x", session_id="sid") == ""
            assert _GRANT_LITERAL in unmask_hint("trw_pipeline_health", reason="x", session_id="other")
        finally:
            reset_overrides()

    def test_a_task_pack_member_gets_no_hint_for_its_own_task(self) -> None:
        """Task-aware resolution: code_search is masked for docs, exposed for coding."""
        assert unmask_hint("trw_code_search", reason="x", task_type="coding") == ""
        assert _GRANT_LITERAL in unmask_hint("trw_code_search", reason="x", task_type="docs")

    def test_a_resolution_failure_is_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.tools._masked_tool_hint as hint_module

        def _boom(*_a: object, **_k: object) -> frozenset[str]:
            raise RuntimeError("resolver exploded")

        monkeypatch.setattr(hint_module, "effective_tool_surface", _boom)
        assert unmask_hint("trw_pipeline_health", reason="x") == ""

    def test_a_non_trw_name_is_ignored(self) -> None:
        assert unmask_hint("ripgrep", reason="x") == ""

    def test_the_helper_never_creates_a_grant(self) -> None:
        """NFR02 — a disclosure repair must not become an escalation path."""
        from trw_mcp.tools import phase_overrides

        phase_overrides.reset_overrides()
        unmask_hint("trw_pipeline_health", reason="x", session_id="sid")

        assert phase_overrides.has_active_override("sid", "trw_pipeline_health") is False
