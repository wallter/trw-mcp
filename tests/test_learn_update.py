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
from typing import Any
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
            patch("trw_mcp.tools.learning.adapter_update") as mock_update,
            patch("trw_mcp.state.analytics.find_entry_by_id", return_value=None),
            patch("trw_mcp.state.analytics.resync_learning_index", return_value=None),
        ):
            mock_update.return_value = {"learning_id": "L-test", "changes": "updated", "status": "updated"}
            result = fn(learning_id="L-test", **kwargs)
        return result

    def test_update_type(self, tmp_project: Path) -> None:
        """Updating type to 'incident' is accepted."""
        result = self._run_update(tmp_project, fields={"type": "incident"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_confidence(self, tmp_project: Path) -> None:
        """Updating confidence to 'verified' is accepted."""
        result = self._run_update(tmp_project, fields={"confidence": "verified"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_phase_origin(self, tmp_project: Path) -> None:
        """Updating phase_origin to 'IMPLEMENT' is accepted."""
        result = self._run_update(tmp_project, fields={"phase_origin": "IMPLEMENT"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_nudge_line(self, tmp_project: Path) -> None:
        """Updating nudge_line to valid short string is accepted."""
        result = self._run_update(tmp_project, fields={"nudge_line": "Use X not Y"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_expires(self, tmp_project: Path) -> None:
        """Updating expires to ISO date is accepted."""
        result = self._run_update(tmp_project, fields={"expires": "2026-12-31"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_protection_tier(self, tmp_project: Path) -> None:
        """Updating protection_tier to 'protected' is accepted."""
        result = self._run_update(tmp_project, fields={"protection_tier": "protected"})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_domain(self, tmp_project: Path) -> None:
        """Updating domain to a list is accepted."""
        result = self._run_update(tmp_project, fields={"domain": ["testing", "mcp"]})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_invalid_enum_rejected(self, tmp_project: Path) -> None:
        """type='bogus' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, fields={"type": "bogus"})
        assert result.get("status") == "invalid"
        assert "type" in result.get("error", "").lower()

    def test_update_invalid_confidence_rejected(self, tmp_project: Path) -> None:
        """confidence='excellent' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, fields={"confidence": "excellent"})
        assert result.get("status") == "invalid"
        assert "confidence" in result.get("error", "").lower()

    def test_update_invalid_protection_tier_rejected(self, tmp_project: Path) -> None:
        """protection_tier='top-secret' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, fields={"protection_tier": "top-secret"})
        assert result.get("status") == "invalid"
        assert "protection_tier" in result.get("error", "").lower()

    def test_update_nudge_line_over_80_rejected(self, tmp_project: Path) -> None:
        """nudge_line >80 chars is rejected with 'invalid' status."""
        too_long = "x" * 81
        result = self._run_update(tmp_project, fields={"nudge_line": too_long})
        assert result.get("status") == "invalid"
        assert "nudge_line" in result.get("error", "").lower()

    def test_update_invalid_phase_origin_rejected(self, tmp_project: Path) -> None:
        """phase_origin='INVALID' is rejected with 'invalid' status."""
        result = self._run_update(tmp_project, fields={"phase_origin": "INVALID"})
        assert result.get("status") == "invalid"
        assert "phase_origin" in result.get("error", "").lower()

    def test_update_phase_origin_empty_allowed(self, tmp_project: Path) -> None:
        """phase_origin='' is valid (clears the field)."""
        result = self._run_update(tmp_project, fields={"phase_origin": ""})
        assert result.get("status") != "invalid", f"Got error: {result}"

    def test_update_all_valid_enum_types(self, tmp_project: Path) -> None:
        """All valid type values are accepted."""
        for t in ("incident", "pattern", "convention", "hypothesis", "workaround"):
            result = self._run_update(tmp_project, fields={"type": t})
            assert result.get("status") != "invalid", f"type={t!r} rejected: {result}"

    def test_update_all_valid_confidence_values(self, tmp_project: Path) -> None:
        """All valid confidence values are accepted."""
        for c in ("unverified", "low", "medium", "high", "verified"):
            result = self._run_update(tmp_project, fields={"confidence": c})
            assert result.get("status") != "invalid", f"confidence={c!r} rejected: {result}"

    def test_update_all_valid_phase_origins(self, tmp_project: Path) -> None:
        """All valid phase_origin values are accepted."""
        for p in ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"):
            result = self._run_update(tmp_project, fields={"phase_origin": p})
            assert result.get("status") != "invalid", f"phase_origin={p!r} rejected: {result}"

    def test_update_nudge_line_exactly_80_chars(self, tmp_project: Path) -> None:
        """nudge_line of exactly 80 chars is accepted."""
        exactly_80 = "x" * 80
        result = self._run_update(tmp_project, fields={"nudge_line": exactly_80})
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
        """trw_learn_update passes tags kwarg through to adapter_update."""
        import asyncio
        from unittest.mock import patch as _patch

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
        captured: dict[str, object] = {}

        def _capture(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
            captured.update(kwargs)
            return {"learning_id": "L-test", "changes": "tags updated", "status": "updated"}

        with (
            _patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            _patch("trw_mcp.state.memory_adapter.get_backend", return_value=_make_mock_backend()),
            _patch("trw_mcp.tools.learning.adapter_update", side_effect=_capture),
            _patch("trw_mcp.state.analytics.find_entry_by_id", return_value=None),
            _patch("trw_mcp.state.analytics.resync_learning_index", return_value=None),
        ):
            result = fn(learning_id="L-test", tags=["foo", "bar"])

        assert result.get("status") == "updated"
        assert captured.get("tags") == ["foo", "bar"]


# --- PRD-FIX-144 FR03: explicit feedback appends an attributed outcome row ---


def _outcome_rows(trw_dir: Path) -> list[dict[str, object]]:
    import json

    path = trw_dir / "logs" / "recall_tracking.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("outcome") is not None]


@pytest.fixture()
def live_learning(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    """A real store with one learning, and the registered trw_learn_update."""
    from tests.conftest import extract_tool_fn, make_test_server

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    server = make_test_server("learning")
    lid = extract_tool_fn(server, "trw_learn")(
        summary="Feedback telemetry fixture learning", detail="Used by FR03 tests.", impact=0.6
    )["learning_id"]
    return extract_tool_fn(server, "trw_learn_update"), lid


def _counters(trw_dir: Path, lid: str) -> tuple[int, int]:
    from trw_mcp.state.memory_adapter import find_entry_by_id

    entry = find_entry_by_id(trw_dir, lid)
    assert entry is not None
    return int(str(entry["helpful_count"])), int(str(entry["unhelpful_count"]))


def test_feedback_appends_one_attributed_outcome_row(tmp_project: Path, live_learning: tuple[Any, str]) -> None:
    update, lid = live_learning
    trw_dir = tmp_project / ".trw"
    assert not (trw_dir / "logs" / "recall_tracking.jsonl").exists()  # no recall happened first
    assert _counters(trw_dir, lid) == (0, 0)

    helpful = update(learning_id=lid, feedback="helpful")
    unhelpful = update(learning_id=lid, feedback="unhelpful")

    # The response contract is unchanged.
    assert set(helpful) == set(unhelpful) == {"status", "learning_id", "changes"}
    rows = _outcome_rows(trw_dir)
    assert [(r["learning_id"], r["outcome"], r["source"]) for r in rows] == [
        (lid, "positive", "explicit_feedback"),
        (lid, "negative", "explicit_feedback"),
    ]
    assert all(r["session_id"] and r["process_session_id"] and isinstance(r["timestamp"], float) for r in rows)
    assert _counters(trw_dir, lid) == (1, 1)


def test_feedback_not_found_writes_nothing(tmp_project: Path, live_learning: tuple[Any, str]) -> None:
    update, _lid = live_learning
    result = update(learning_id="L-doesnotexist", feedback="helpful")
    assert result["status"] == "not_found"
    assert _outcome_rows(tmp_project / ".trw") == []


def test_invalid_feedback_writes_nothing(tmp_project: Path, live_learning: tuple[Any, str]) -> None:
    update, lid = live_learning
    assert update(learning_id=lid, feedback="meh")["status"] == "invalid"
    assert _outcome_rows(tmp_project / ".trw") == []


def test_feedback_with_status_change_writes_exactly_one_row(tmp_project: Path, live_learning: tuple[Any, str]) -> None:
    update, lid = live_learning
    result = update(learning_id=lid, feedback="helpful", status="resolved", impact=0.9)
    assert result["status"] == "updated"
    assert [(r["learning_id"], r["outcome"]) for r in _outcome_rows(tmp_project / ".trw")] == [(lid, "positive")]


def test_update_without_feedback_writes_no_outcome(tmp_project: Path, live_learning: tuple[Any, str]) -> None:
    update, lid = live_learning
    assert update(learning_id=lid, impact=0.9)["status"] == "updated"
    assert _outcome_rows(tmp_project / ".trw") == []


def test_deliver_skill_names_explicit_feedback_call() -> None:
    """FR06: the deliver skill asks for earned feedback only; mirror is byte-identical."""
    import trw_mcp

    bundled = Path(trw_mcp.__file__).parent / "data" / "skills" / "trw-deliver" / "SKILL.md"
    text = bundled.read_text(encoding="utf-8")
    assert 'trw_learn_update(learning_id="<id>", feedback="helpful")' in text
    assert 'trw_learn_update(learning_id="<id>", feedback="unhelpful")' in text
    assert "exposure alone is not feedback" in text
    mirror = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
    if mirror.exists():  # absent in a standalone trw-mcp checkout
        assert mirror.read_bytes() == bundled.read_bytes()
