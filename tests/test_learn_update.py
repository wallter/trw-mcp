"""Tests for PRD-CORE-110 extended trw_learn's update mode with 9 new fields.

PRD-CORE-291 merged the standalone ``trw_learn_update`` tool into
``trw_learn``'s update mode (``learning_id`` set); this file targets that
merged tool. ``type``/``confidence`` are now top-level ``trw_learn`` kwargs;
the other PRD-CORE-110 fields (``phase_origin``, ``nudge_line``, ``expires``,
``protection_tier``, ``domain``) live in the ``metadata`` bag.

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
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from tests._memory_fixtures import DaemonCheckout


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Set up a minimal .trw/ project structure."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "memory").mkdir()
    return tmp_path


class TestLearnUpdateNewFields:
    """Integration tests for trw_learn_update with PRD-CORE-110 fields."""

    def _run_update(self, tmp_project: Path, **kwargs: object) -> dict[str, str]:
        """Helper to call trw_learn's update mode (learning_id set) directly."""
        import asyncio

        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)

        async def _get_tool_fn() -> object:
            tools = await server.list_tools()
            for t in tools:
                if t.name == "trw_learn":
                    return t.fn
            raise KeyError("trw_learn not found")

        fn = asyncio.run(_get_tool_fn())

        trw_dir = tmp_project / ".trw"
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.adapter_update") as mock_update,
            patch("trw_mcp.state.analytics.find_entry_by_id", return_value=None),
            patch("trw_mcp.state.analytics.resync_learning_index", return_value=None),
        ):
            mock_update.return_value = {"learning_id": "L-test", "changes": "updated", "status": "updated"}
            result = fn(learning_id="L-test", **kwargs)
        return result

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
        result = self._run_update(tmp_project, metadata={"phase_origin": "IMPLEMENT"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_nudge_line(self, tmp_project: Path) -> None:
        """Updating nudge_line to valid short string is accepted."""
        result = self._run_update(tmp_project, metadata={"nudge_line": "Use X not Y"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_expires(self, tmp_project: Path) -> None:
        """Updating expires to ISO date is accepted."""
        result = self._run_update(tmp_project, metadata={"expires": "2026-12-31"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_protection_tier(self, tmp_project: Path) -> None:
        """Updating protection_tier to 'protected' is accepted."""
        result = self._run_update(tmp_project, metadata={"protection_tier": "protected"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_domain(self, tmp_project: Path) -> None:
        """Updating domain to a list is accepted."""
        result = self._run_update(tmp_project, metadata={"domain": ["testing", "mcp"]})
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
        result = self._run_update(tmp_project, metadata={"protection_tier": "top-secret"})
        assert result.get("status") == "invalid"
        assert "protection_tier" in result.get("error", "").lower()

    def test_update_nudge_line_over_80_rejected(self, tmp_project: Path) -> None:
        """nudge_line >80 chars is rejected with 'invalid' status."""
        too_long = "x" * 81
        result = self._run_update(tmp_project, metadata={"nudge_line": too_long})
        assert result.get("status") == "invalid"
        assert "nudge_line" in result.get("error", "").lower()

    def test_update_invalid_phase_origin_rejected(self, tmp_project: Path) -> None:
        """phase_origin='INVALID' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, metadata={"phase_origin": "INVALID"})
        assert result.get("status") == "invalid"
        assert "phase_origin" in result.get("error", "").lower()

    def test_update_phase_origin_empty_allowed(self, tmp_project: Path) -> None:
        """phase_origin='' is valid (clears the field)."""
        result = self._run_update(tmp_project, metadata={"phase_origin": ""})
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
            result = self._run_update(tmp_project, metadata={"phase_origin": p})
            assert result.get("status") != "invalid", f"phase_origin={p!r} rejected: {result}"

    def test_update_nudge_line_exactly_80_chars(self, tmp_project: Path) -> None:
        """nudge_line of exactly 80 chars is accepted."""
        exactly_80 = "x" * 80
        result = self._run_update(tmp_project, metadata={"nudge_line": exactly_80})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_tags_list_accepted(self, tmp_project: Path) -> None:
        """Updating tags to a list of strings is accepted."""
        result = self._run_update(tmp_project, tags=["alpha", "beta", "gamma"])
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_tags_empty_list_clears_tags(self, tmp_project: Path) -> None:
        """Passing tags=[] is accepted (clears all tags)."""
        result = self._run_update(tmp_project, tags=[])
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_tags_comma_string_is_coerced_not_rejected(self, tmp_project: Path) -> None:
        """A comma/space string is accepted and split, matching trw_learn.

        Superseded the old "a plain string is rejected" assertion on 2026-09-10:
        trw_learn has accepted this shape since PRD-IMPROVE-MCP-01 FR1, and the
        asymmetry meant a caller could record a learning with tags="a,b" and then
        be rejected passing the identical value to the update path.
        """
        result = self._run_update(tmp_project, tags="alpha, beta gamma")
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_tags_non_string_element_rejected(self, tmp_project: Path) -> None:
        """tags entries must all be strings — mixed types are rejected."""
        result = self._run_update(tmp_project, tags=["ok", 42, "also-ok"])
        assert result.get("status") == "invalid"
        assert "tags" in result.get("error", "").lower()

    def test_update_tags_wired_through_adapter(self, tmp_project: Path) -> None:
        """trw_learn's update mode passes tags kwarg through to adapter_update."""
        import asyncio
        from unittest.mock import patch as _patch

        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)

        async def _get_tool_fn() -> object:
            tools = await server.list_tools()
            for t in tools:
                if t.name == "trw_learn":
                    return t.fn
            raise KeyError("trw_learn not found")

        fn = asyncio.run(_get_tool_fn())

        trw_dir = tmp_project / ".trw"
        captured: dict[str, object] = {}

        def _capture(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
            captured.update(kwargs)
            return {"learning_id": "L-test", "changes": "tags updated", "status": "updated"}

        with (
            _patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            _patch("trw_mcp.tools.learning.adapter_update", side_effect=_capture),
            _patch("trw_mcp.state.analytics.find_entry_by_id", return_value=None),
            _patch("trw_mcp.state.analytics.resync_learning_index", return_value=None),
        ):
            result = fn(learning_id="L-test", tags=["foo", "bar"])

        assert result.get("status") == "updated"
        assert captured.get("tags") == ["foo", "bar"]


# --- PRD-CORE-293: trw_learn's update mode writes no outcome/feedback rows ---


def _outcome_rows(trw_dir: Path) -> list[dict[str, object]]:
    import json

    path = trw_dir / "logs" / "recall_tracking.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("outcome") is not None]


@pytest.fixture()
def live_learning(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    """A real (daemon-backed) store with one learning, and the registered trw_learn update mode.

    PRD-CORE-280 slice e1: routed through ``daemon_checkout`` instead of an
    in-process ``memory.db``. The tool resolves ``trw_dir`` via
    ``resolve_trw_dir()``, which this suite's process-wide path-isolation
    stand-in answers from its own ``current_root()`` -- repointed at the
    checkout's own root so the tool closure lands on the daemon-migrated
    checkout (see ``tests/test_learn_update_by_id.py`` for the same pattern).
    """
    from tests import _path_isolation
    from tests.conftest import extract_tool_fn, make_test_server

    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    _path_isolation.set_current_root(daemon_checkout.trw_dir.parent)
    server = make_test_server("learning")
    lid = extract_tool_fn(server, "trw_learn")(
        summary="Feedback telemetry fixture learning", detail="Used by FR03 tests.", impact=0.6
    )["learning_id"]
    return extract_tool_fn(server, "trw_learn"), lid


def test_update_without_feedback_writes_no_outcome(
    daemon_checkout: DaemonCheckout, live_learning: tuple[Any, str]
) -> None:
    update, lid = live_learning
    assert update(learning_id=lid, impact=0.9)["status"] == "updated"
    assert _outcome_rows(daemon_checkout.trw_dir) == []


def test_retired_feedback_key_is_rejected_not_ignored(
    daemon_checkout: DaemonCheckout, live_learning: tuple[Any, str]
) -> None:
    """PRD-CORE-293 x CORE-291: the reward loop is gone, so update-mode metadata has no feedback key.

    A caller still passing it fails loudly (``invalid``, naming the key) and nothing is written.
    """
    update, lid = live_learning
    result = update(learning_id=lid, metadata={"feedback": "helpful"})
    assert result["status"] == "invalid"
    assert "feedback" in result["error"]
    assert _outcome_rows(daemon_checkout.trw_dir) == []
