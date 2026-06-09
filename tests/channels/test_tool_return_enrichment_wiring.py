"""Wiring tests: tool-return enrichment by client tier (Gap 2 closure).

Proves that the RESPONSE CONTENT changes by resolved tier (T0 / T1 / T2)
for trw_before_edit_hint.  Tests verify behavior change, not existence.

These are unit tests: no filesystem I/O, all sidecar/entitlement
dependencies are patched out.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hint_payload(**overrides: Any) -> dict[str, Any]:
    """Return a minimal BeforeYouEditHintPayload dict."""
    base: dict[str, Any] = {
        "target_path": "src/foo.py",
        "target_exists_in_map": True,
        "importers": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"],
        "inferred_tests": ["tests/t1.py", "tests/t2.py", "tests/t3.py", "tests/t4.py"],
        "doc_references": [],
        "co_change_neighbors": ["x.py", "y.py"],
        "hotspot_warnings": ["w1", "w2", "w3", "w4"],
        "risk_score": 0.75,
    }
    base.update(overrides)
    return base


def _make_hint_result(distill_hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a minimal BeforeEditHintResult model_dump dict."""
    return {
        "file_path": "src/foo.py",
        "tier": "pro",
        "distill_hint": distill_hint,
        "distill_status": "hint_available" if distill_hint else "tier_required",
        "distill_action": None,
        "distill_sidecar_path": None,
        "distill_sidecar_sha": None,
        "learnings": [],
        "learnings_count": 0,
    }


# ---------------------------------------------------------------------------
# enrich_response unit tests (channels/_tool_return_tiers.py)
# ---------------------------------------------------------------------------


class TestEnrichResponseTiers:
    """Direct tests of the enrich_response function."""

    def test_t2_includes_full_importers_list(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        hint = _make_hint_payload()
        result = _make_hint_result(distill_hint=hint)
        enriched = enrich_response(result, client_tier="T2")

        assert "enrichment" in enriched
        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert ctx["importers"] == hint["importers"]  # all 6, not truncated

    def test_t1_truncates_importers_to_5(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        hint = _make_hint_payload()
        result = _make_hint_result(distill_hint=hint)
        enriched = enrich_response(result, client_tier="T1")

        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert len(ctx["importers"]) == 5
        assert ctx["importers"] == hint["importers"][:5]

    def test_t1_truncates_hotspot_warnings_to_3(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        hint = _make_hint_payload()
        result = _make_hint_result(distill_hint=hint)
        enriched = enrich_response(result, client_tier="T1")

        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert len(ctx["hotspot_warnings"]) == 3

    def test_t0_omits_list_fields(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        hint = _make_hint_payload()
        result = _make_hint_result(distill_hint=hint)
        enriched = enrich_response(result, client_tier="T0")

        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert "importers" not in ctx
        assert "inferred_tests" not in ctx
        assert "co_change_neighbors" not in ctx

    def test_t0_includes_risk_score_scalar(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        hint = _make_hint_payload(risk_score=0.42)
        result = _make_hint_result(distill_hint=hint)
        enriched = enrich_response(result, client_tier="T0")

        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert ctx["risk_score"] == pytest.approx(0.42)

    def test_t2_includes_co_change_neighbors(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        hint = _make_hint_payload()
        result = _make_hint_result(distill_hint=hint)
        enriched = enrich_response(result, client_tier="T2")

        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert ctx["co_change_neighbors"] == hint["co_change_neighbors"]

    def test_t1_excludes_co_change_neighbors(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        hint = _make_hint_payload()
        result = _make_hint_result(distill_hint=hint)
        enriched = enrich_response(result, client_tier="T1")

        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert "co_change_neighbors" not in ctx

    def test_tier_applied_field_reflects_tier(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        result = _make_hint_result()
        for tier in ("T0", "T1", "T2"):
            enriched = enrich_response(result, client_tier=tier)
            assert enriched["enrichment"]["tier_applied"] == tier  # type: ignore[index]

    def test_unknown_tier_returns_original_unchanged(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        result = _make_hint_result()
        enriched = enrich_response(result, client_tier="TX")
        assert "enrichment" not in enriched
        assert enriched is result  # same object — no copy

    def test_base_fields_preserved_after_enrichment(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        result = _make_hint_result(_make_hint_payload())
        enriched = enrich_response(result, client_tier="T1")

        # All original keys still present
        for key in result:
            assert key in enriched

    def test_t0_distill_status_included_in_beacon(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        result = _make_hint_result()  # no distill_hint
        result["distill_status"] = "tier_required"
        enriched = enrich_response(result, client_tier="T0")

        ctx = enriched["enrichment"]["distill_context"]  # type: ignore[index]
        assert ctx["distill_status"] == "tier_required"

    def test_t2_none_sidecar_returns_null_context(self) -> None:
        from trw_mcp.channels._tool_return_tiers import enrich_response

        result = _make_hint_result(distill_hint=None)
        enriched = enrich_response(result, client_tier="T2")

        assert enriched["enrichment"]["distill_context"] is None  # type: ignore[index]


# ---------------------------------------------------------------------------
# before_edit_hint tool wiring: response content varies by tier
# ---------------------------------------------------------------------------


class TestBeforeEditHintTierWiring:
    """Prove the tool handler enriches its response based on client tier."""

    def _call_tool_with_env_tier(
        self,
        client_profile: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> dict[str, Any]:
        """Build a real git repo + sidecar + entitlement, set TRW_CLIENT_PROFILE, call tool."""
        import json
        import os
        import subprocess
        import sys
        from datetime import datetime, timedelta, timezone

        from trw_mcp.state._entitlements import sign_entitlement_for_dev
        from trw_mcp.tools.before_edit_hint import _SCHEMA_VERSION_ACCEPTED

        # Build a minimal git repo + sidecar + entitlement
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src.py").write_text("x = 1\n")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

        cache_dir = repo / ".trw" / "distill" / "map-cache"
        cache_dir.mkdir(parents=True)
        sidecar = {
            "schema_version": _SCHEMA_VERSION_ACCEPTED,
            "sha": sha,
            "generated_at_unix": 1714000000.0,
            "payload": {
                "target_path": "src.py",
                "target_exists_in_map": True,
                "importers": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"],
                "inferred_tests": ["t1.py", "t2.py", "t3.py", "t4.py"],
                "doc_references": [],
                "co_change_neighbors": ["x.py", "y.py"],
                "hotspot_warnings": ["w1", "w2", "w3", "w4"],
                "risk_score": 0.88,
            },
        }
        (cache_dir / f"before-edit-hint-{sha}.json").write_text(json.dumps(sidecar))

        trw_dir = repo / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
        sig = sign_entitlement_for_dev(tier="pro", issued_to="t@t", expires_at=future)  # type: ignore[arg-type]
        (trw_dir / "entitlements.yaml").write_text(
            f"tier: pro\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n"
        )

        monkeypatch.setenv("TRW_CLIENT_PROFILE", client_profile)

        # Use conftest pattern: import get_tools_sync directly
        tests_dir = os.path.join(os.path.dirname(__file__), "..")
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        from conftest import get_tools_sync
        from fastmcp import FastMCP

        from trw_mcp.tools.before_edit_hint import register_before_edit_hint_tools

        srv = FastMCP("test")
        register_before_edit_hint_tools(srv)
        tools = get_tools_sync(srv)
        tool_fn = tools["trw_before_edit_hint"].fn

        return tool_fn(  # type: ignore[return-value]
            file_path="src.py", repo_root=str(repo), cache_dir=str(cache_dir)
        )

    def test_t2_response_has_co_change_neighbors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        result = self._call_tool_with_env_tier("codex", monkeypatch, tmp_path)
        assert "enrichment" in result
        ctx = result["enrichment"]["distill_context"]
        assert ctx is not None
        assert len(ctx["co_change_neighbors"]) == 2

    def test_t1_response_has_no_co_change_neighbors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        result = self._call_tool_with_env_tier("claude-code", monkeypatch, tmp_path)
        assert "enrichment" in result
        ctx = result["enrichment"]["distill_context"]
        assert ctx is not None
        assert "co_change_neighbors" not in ctx

    def test_t0_response_has_no_importers_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        result = self._call_tool_with_env_tier("copilot", monkeypatch, tmp_path)
        assert "enrichment" in result
        ctx = result["enrichment"]["distill_context"]
        assert ctx is not None
        assert "importers" not in ctx

    def test_t2_importers_not_truncated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        result = self._call_tool_with_env_tier("codex", monkeypatch, tmp_path)
        ctx = result["enrichment"]["distill_context"]
        # sidecar has 6 importers; T2 returns all
        assert len(ctx["importers"]) == 6

    def test_t1_importers_truncated_to_5(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        result = self._call_tool_with_env_tier("claude-code", monkeypatch, tmp_path)
        ctx = result["enrichment"]["distill_context"]
        assert len(ctx["importers"]) == 5

    def test_base_fields_unchanged_across_tiers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        result = self._call_tool_with_env_tier("codex", monkeypatch, tmp_path)
        assert result["file_path"] == "src.py"
        assert result["distill_status"] == "hint_available"
