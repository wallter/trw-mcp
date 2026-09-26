"""Branch coverage tests for warm-tier behavior in tiers.py."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state.tiers import TierManager


class TestWarmSidecarUpsertEdgeCases:
    """Test blank lines and corrupt JSON in sidecar file."""

    def test_sidecar_upsert_skips_blank_lines(self, tmp_path: Path) -> None:
        """Line 313: blank lines in existing sidecar are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "keep-me", "summary": "keep"})
            + "\n"
            + "\n"
            + "   \n"
            + json.dumps({"id": "also-keep", "summary": "also"})
            + "\n",
            encoding="utf-8",
        )

        mgr._warm_sidecar_upsert("new-entry", {"summary": "new"})

        lines = [line.strip() for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [json.loads(line)["id"] for line in lines]
        assert "keep-me" in ids
        assert "also-keep" in ids
        assert "new-entry" in ids

    def test_sidecar_upsert_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Lines 318-319: corrupt JSON lines in sidecar are silently skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "good", "summary": "good"})
            + "\n"
            + "{not valid json\n"
            + json.dumps({"id": "also-good", "summary": "also"})
            + "\n",
            encoding="utf-8",
        )

        mgr._warm_sidecar_upsert("new-entry", {"summary": "new"})

        lines = [line.strip() for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [json.loads(line)["id"] for line in lines]
        assert "good" in ids
        assert "also-good" in ids
        assert "new-entry" in ids


class TestWarmRemoveSidecarEdgeCases:
    """Test warm_remove handles corrupt sidecar gracefully."""

    def test_warm_remove_sidecar_skips_blank_lines(self, tmp_path: Path) -> None:
        """Line 355: blank lines in sidecar during warm_remove are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "remove-me", "summary": "bye"})
            + "\n"
            + "\n"
            + json.dumps({"id": "keep-me", "summary": "stay"})
            + "\n",
            encoding="utf-8",
        )

        mgr.warm_remove("remove-me")

        lines = [line.strip() for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [json.loads(line)["id"] for line in lines]
        assert "remove-me" not in ids
        assert "keep-me" in ids

    def test_warm_remove_sidecar_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Lines 360-361: corrupt JSON lines in sidecar during remove are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "keep", "summary": "stay"})
            + "\n"
            + "{broken json\n"
            + json.dumps({"id": "remove-me", "summary": "bye"})
            + "\n",
            encoding="utf-8",
        )

        mgr.warm_remove("remove-me")

        lines = [line.strip() for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [json.loads(line)["id"] for line in lines]
        assert "keep" in ids
        assert "remove-me" not in ids


class TestWarmKeywordSearchEdgeCases:
    """Test _warm_keyword_search handles corrupt sidecar gracefully."""

    def test_keyword_search_skips_blank_lines(self, tmp_path: Path) -> None:
        """Line 422: blank lines in sidecar during keyword search are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "entry-1", "summary": "testing coverage", "tags": []})
            + "\n"
            + "\n"
            + "  \n"
            + json.dumps({"id": "entry-2", "summary": "another test", "tags": ["foo"]})
            + "\n",
            encoding="utf-8",
        )

        results = mgr._warm_keyword_search(["testing"], top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == "entry-1"

    def test_keyword_search_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Lines 425-426: corrupt JSON in sidecar during keyword search is skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "entry-1", "summary": "testing coverage", "tags": []})
            + "\n"
            + "{corrupt json here\n"
            + json.dumps({"id": "entry-2", "summary": "other topic", "tags": []})
            + "\n",
            encoding="utf-8",
        )

        results = mgr._warm_keyword_search(["testing"], top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == "entry-1"


class TestWarmKeywordSearchTopK:
    """Test _warm_keyword_search respects top_k limit."""

    def test_keyword_search_respects_top_k(self, tmp_path: Path) -> None:
        """Only top_k results returned when more matches exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        for i in range(5):
            mgr._warm_sidecar_upsert(f"e{i}", {"summary": f"test entry {i}", "tags": []})

        results = mgr._warm_keyword_search(["test"], top_k=2)
        assert len(results) == 2

    def test_keyword_search_empty_tokens_returns_empty(self, tmp_path: Path) -> None:
        """Empty query_tokens returns [] even when sidecar has entries."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)
        mgr._warm_sidecar_upsert("e1", {"summary": "test entry", "tags": []})
        assert mgr._warm_keyword_search([], top_k=10) == []


class TestWarmSearchTagMatching:
    """Test that warm keyword search matches on tags, not just summary."""

    def test_keyword_search_matches_tags(self, tmp_path: Path) -> None:
        """Entries matched by tag content alone are returned."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mgr._warm_sidecar_upsert(
            "tagged",
            {"summary": "unrelated summary", "tags": ["pytest", "fixture"]},
        )
        results = mgr._warm_keyword_search(["pytest"], top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == "tagged"


class TestWarmRemoveEmptySidecar:
    """Test warm_remove when sidecar becomes empty after removal."""

    def test_warm_remove_last_entry_empties_sidecar(self, tmp_path: Path) -> None:
        """Removing the only entry leaves sidecar empty (not stale data)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mgr._warm_sidecar_upsert("only-one", {"summary": "sole entry", "tags": []})
        sidecar = mgr._warm_sidecar_path()
        assert sidecar.exists()

        mgr.warm_remove("only-one")

        content = sidecar.read_text(encoding="utf-8").strip()
        assert content == ""

    def test_warm_remove_no_sidecar_no_error(self, tmp_path: Path) -> None:
        """Removing when no sidecar file exists does not raise."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        assert not sidecar.exists()

        mgr.warm_remove("nonexistent")


class TestWarmSearchKeywordPath:
    """warm_search is keyword search over the sidecar; trw-mcp keeps no vectors (PRD-CORE-302 FR05)."""

    def test_warm_search_matches_the_sidecar_by_keyword(self, tmp_path: Path) -> None:
        """A query token in an entry's summary finds it."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mgr._warm_sidecar_upsert("w1", {"summary": "testing patterns", "tags": []})

        results = mgr.warm_search(["testing"])

        assert len(results) == 1
        assert results[0]["id"] == "w1"
