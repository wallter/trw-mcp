"""Tests for PRD-CORE-110 extended trw_learn_update with 9 new fields.

Covers:
- test_update_type: update type to "incident"
- test_update_confidence: update confidence to "verified"
- test_update_phase_origin: update phase_origin to "IMPLEMENT"
- test_update_nudge_line: update nudge_line to valid string
- test_update_invalid_enum_rejected: type="bogus" returns error
- test_update_nudge_line_over_80_rejected: nudge_line >80 chars returns error
- test_update_expires: update expires to ISO date
- test_update_protection_tier: update protection_tier to "protected"
- test_update_domain: update domain list
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Set up a minimal .trw/ project structure."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "memory").mkdir()
    return tmp_path


def _make_mock_backend(learning_id: str = "L-test") -> MagicMock:
    """Return a mock backend with a single entry."""
    mock_entry = MagicMock()
    mock_entry.id = learning_id
    mock_backend = MagicMock()
    mock_backend.get.return_value = mock_entry
    mock_backend.update.return_value = None
    return mock_backend


class TestLearnUpdateNewFields:
    """Integration tests for trw_learn_update with PRD-CORE-110 fields."""

    def _run_update(self, tmp_project: Path, **kwargs: object) -> dict[str, str]:
        """Helper to call the trw_learn_update tool function directly."""
        import asyncio

        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)

        async def _get_tool_fn() -> object:
            tools = await server.list_tools()
            for t in tools:
                if t.name == "trw_learn_update":
                    return t.fn
            raise KeyError("trw_learn_update not found")

        fn = asyncio.run(_get_tool_fn())

        trw_dir = tmp_project / ".trw"
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=_make_mock_backend()),
            patch("trw_mcp.tools.learning.get_backend", return_value=_make_mock_backend()),
            patch("trw_mcp.tools.learning.adapter_update") as mock_update,
            patch("trw_mcp.state.analytics.find_entry_by_id", return_value=None),
            patch("trw_mcp.state.analytics.resync_learning_index", return_value=None),
        ):
            mock_update.return_value = {"learning_id": "L-test", "changes": "updated", "status": "updated"}
            result = fn(learning_id="L-test", **kwargs)
        return result  # type: ignore[return-value]

    def test_update_type(self, tmp_project: Path) -> None:
        """Updating type to 'incident' is accepted."""
        result = self._run_update(tmp_project, type="incident")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_confidence(self, tmp_project: Path) -> None:
        """Updating confidence to 'verified' is accepted."""
        result = self._run_update(tmp_project, confidence="verified")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_phase_origin(self, tmp_project: Path) -> None:
        """Updating phase_origin to 'IMPLEMENT' is accepted."""
        result = self._run_update(tmp_project, phase_origin="IMPLEMENT")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_nudge_line(self, tmp_project: Path) -> None:
        """Updating nudge_line to valid short string is accepted."""
        result = self._run_update(tmp_project, nudge_line="Use X not Y")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_expires(self, tmp_project: Path) -> None:
        """Updating expires to ISO date is accepted."""
        result = self._run_update(tmp_project, expires="2026-12-31")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_protection_tier(self, tmp_project: Path) -> None:
        """Updating protection_tier to 'protected' is accepted."""
        result = self._run_update(tmp_project, protection_tier="protected")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_domain(self, tmp_project: Path) -> None:
        """Updating domain to a list is accepted."""
        result = self._run_update(tmp_project, domain=["testing", "mcp"])
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_invalid_enum_rejected(self, tmp_project: Path) -> None:
        """type='bogus' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, type="bogus")
        assert result.get("status") == "invalid"
        assert "type" in result.get("error", "").lower()

    def test_update_invalid_confidence_rejected(self, tmp_project: Path) -> None:
        """confidence='excellent' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, confidence="excellent")
        assert result.get("status") == "invalid"
        assert "confidence" in result.get("error", "").lower()

    def test_update_invalid_protection_tier_rejected(self, tmp_project: Path) -> None:
        """protection_tier='top-secret' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, protection_tier="top-secret")
        assert result.get("status") == "invalid"
        assert "protection_tier" in result.get("error", "").lower()

    def test_update_nudge_line_over_80_rejected(self, tmp_project: Path) -> None:
        """nudge_line >80 chars is rejected with 'invalid' status."""
        too_long = "x" * 81
        result = self._run_update(tmp_project, nudge_line=too_long)
        assert result.get("status") == "invalid"
        assert "nudge_line" in result.get("error", "").lower()

    def test_update_invalid_phase_origin_rejected(self, tmp_project: Path) -> None:
        """phase_origin='INVALID' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, phase_origin="INVALID")
        assert result.get("status") == "invalid"
        assert "phase_origin" in result.get("error", "").lower()

    def test_update_phase_origin_empty_allowed(self, tmp_project: Path) -> None:
        """phase_origin='' is valid (clears the field)."""
        result = self._run_update(tmp_project, phase_origin="")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_all_valid_enum_types(self, tmp_project: Path) -> None:
        """All valid type values are accepted."""
        for t in ("incident", "pattern", "convention", "hypothesis", "workaround"):
            result = self._run_update(tmp_project, type=t)
            assert result.get("status") != "invalid", f"type={t!r} rejected: {result}"

    def test_update_all_valid_confidence_values(self, tmp_project: Path) -> None:
        """All valid confidence values are accepted."""
        for c in ("unverified", "low", "medium", "high", "verified"):
            result = self._run_update(tmp_project, confidence=c)
            assert result.get("status") != "invalid", f"confidence={c!r} rejected: {result}"

    def test_update_all_valid_phase_origins(self, tmp_project: Path) -> None:
        """All valid phase_origin values are accepted."""
        for p in ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"):
            result = self._run_update(tmp_project, phase_origin=p)
            assert result.get("status") != "invalid", f"phase_origin={p!r} rejected: {result}"

    def test_update_nudge_line_exactly_80_chars(self, tmp_project: Path) -> None:
        """nudge_line of exactly 80 chars is accepted."""
        exactly_80 = "x" * 80
        result = self._run_update(tmp_project, nudge_line=exactly_80)
        assert result.get("status") != "invalid", f"Got error: {result}"
