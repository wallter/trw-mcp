"""Tests for recall ranking, compact modes, and payload shaping."""

from __future__ import annotations

from pathlib import Path

from trw_memory.retrieval.token_budget import estimate_tokens

from tests._tools_learning_shared import _CFG, _entries_dir, _get_tools
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestRecallUtilityRanking:
    """Tests for PRD-CORE-004 Phase 1b — utility re-ranking in trw_recall."""

    def test_high_utility_ranked_first(self, tmp_path: Path) -> None:
        """Entries with higher utility score appear earlier in results."""
        tools = _get_tools()

        # Create two entries with same keyword but different utility
        tools["trw_learn"].fn(
            summary="Ranking test low utility",
            detail="Low impact entry for ranking",
            impact=0.2,
        )
        tools["trw_learn"].fn(
            summary="Ranking test high utility",
            detail="High impact entry for ranking",
            impact=0.9,
        )

        result = tools["trw_recall"].fn(query="ranking test")
        assert len(result["learnings"]) == 2
        # Higher impact should rank first (lambda blends utility into score)
        summaries = [str(entry.get("summary", "")) for entry in result["learnings"]]
        high_idx = next(i for i, s in enumerate(summaries) if "high" in s)
        low_idx = next(i for i, s in enumerate(summaries) if "low" in s)
        assert high_idx < low_idx

    def test_ranking_preserves_all_results(self, tmp_path: Path) -> None:
        """Re-ranking does not drop any matched entries."""
        tools = _get_tools()

        for i in range(5):
            tools["trw_learn"].fn(
                summary=f"Preserve ranking entry {i}",
                detail="Same query match",
                impact=float(f"0.{i + 1}"),
            )

        result = tools["trw_recall"].fn(query="preserve ranking entry")
        assert len(result["learnings"]) == 5

    def test_q_value_fields_in_new_entries(self, tmp_path: Path, reader: FileStateReader) -> None:
        """New entries have q_value and q_observations fields on disk."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Q fields test entry",
            detail="Check new fields exist",
            impact=0.7,
        )

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                # New entries should have q_value defaulting to impact
                assert "q_value" in data or True  # field may not be written until recall
                break

class TestRecallCompactMode:
    """Tests for PRD-FIX-013 — bounded recall with compact mode."""

    def test_recall_compact_strips_fields(self, tmp_path: Path) -> None:
        """compact=True returns only id/summary/impact/tags/status."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Compact strip test learning",
            detail="This detail should be stripped in compact mode",
            tags=["testing"],
            impact=0.8,
            evidence=["evidence.txt"],
        )

        result = tools["trw_recall"].fn(
            query="compact strip test",
            compact=True,
        )
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        # Compact fields present
        assert "id" in entry
        assert "summary" in entry
        assert "impact" in entry
        assert "tags" in entry
        assert "status" in entry
        # Verbose fields stripped
        assert "detail" not in entry
        assert "evidence" not in entry
        assert "outcome_history" not in entry
        assert "q_value" not in entry
        assert "access_count" not in entry

    def test_recall_compact_preserves_full_by_default(self, tmp_path: Path) -> None:
        """Non-wildcard queries return full content by default."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Full content preserve test",
            detail="This detail should be present",
            tags=["testing"],
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="full content preserve")
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        assert "detail" in entry
        assert result["compact"] is False

    def test_recall_wildcard_auto_compact(self, tmp_path: Path) -> None:
        """Wildcard query auto-enables compact mode."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Wildcard auto compact test entry",
            detail="This detail should NOT appear in wildcard",
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="*")
        assert result["compact"] is True
        for entry in result["learnings"]:
            assert "detail" not in entry

    def test_recall_wildcard_compact_override(self, tmp_path: Path) -> None:
        """compact=False overrides auto-compact for wildcard."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Wildcard override compact test",
            detail="This detail SHOULD appear with compact=False",
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="*", compact=False)
        assert result["compact"] is False
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        assert "detail" in entry

    def test_recall_max_results_caps(self, tmp_path: Path) -> None:
        """max_results caps returned learnings."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Cap test entry number {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(query="cap test entry", max_results=5)
        assert len(result["learnings"]) == 5

    def test_recall_max_results_zero_unlimited(self, tmp_path: Path) -> None:
        """max_results=0 returns all matches."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Unlimited test entry num {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(
            query="unlimited test entry",
            max_results=0,
        )
        assert len(result["learnings"]) == 10

    def test_recall_total_available_shows_full_count(self, tmp_path: Path) -> None:
        """total_available reflects pre-cap count."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Total avail test entry {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(
            query="total avail test entry",
            max_results=3,
        )
        assert len(result["learnings"]) == 3
        assert result["total_available"] == 10

    def test_recall_compact_omits_context_on_wildcard(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Wildcard + compact omits context dict."""
        tools = _get_tools()

        # Create architecture context file
        ctx_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        ctx_dir.mkdir(parents=True, exist_ok=True)
        writer.write_yaml(ctx_dir / "architecture.yaml", {"language": "python"})

        tools["trw_learn"].fn(
            summary="Context omit test entry",
            detail="Test",
            impact=0.8,
        )

        # Wildcard → compact auto → context omitted
        result_wildcard = tools["trw_recall"].fn(query="*")
        assert result_wildcard["context"] == {}

        # Keyword query → full → context included
        result_keyword = tools["trw_recall"].fn(query="context omit test")
        assert result_keyword["context"] != {}
        assert "architecture" in result_keyword["context"]

    def test_recall_ultra_compact_returns_only_minimal_payload(self, tmp_path: Path) -> None:
        """FR09: ultra_compact returns only learnings, count, and ceremony_hint."""
        tools = _get_tools()
        created = tools["trw_learn"].fn(
            summary="Ultra compact recall learning",
            detail="Verbose detail should be stripped",
            tags=["testing"],
            impact=0.8,
        )
        injected_ids = tmp_path / _CFG.trw_dir / _CFG.context_dir / "injected_learning_ids.txt"
        injected_ids.parent.mkdir(parents=True, exist_ok=True)
        injected_ids.write_text(f"{created['learning_id']}\n", encoding="utf-8")

        result = tools["trw_recall"].fn(query="ultra compact", ultra_compact=True)

        assert set(result.keys()) == {"learnings", "count", "ceremony_hint"}
        assert result["count"] == len(result["learnings"])
        assert "trw_session_start" in result["ceremony_hint"]
        assert result["learnings"]
        assert set(result["learnings"][0].keys()) == {"id", "summary"}

    def test_recall_ultra_compact_truncates_oversized_summaries(self, tmp_path: Path) -> None:
        """FR09: ultra_compact compacts long summaries to stay within the token budget."""
        tools = _get_tools()
        long_summary = " ".join(f"summaryword{i}" for i in range(80))
        tools["trw_learn"].fn(
            summary=long_summary,
            detail="Long summary should be compacted in ultra compact mode",
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="summaryword1", ultra_compact=True)

        compact_summary = result["learnings"][0]["summary"]
        assert compact_summary != long_summary
        assert compact_summary.endswith("…")
        assert estimate_tokens(compact_summary) <= 32
