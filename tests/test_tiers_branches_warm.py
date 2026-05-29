"""Branch coverage tests for warm-tier behavior in tiers.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.state.tiers import TierManager


class TestWarmAddWithMemoryStore:
    """Test warm_add branch when MemoryStore is available and embedding provided."""

    def test_warm_add_memory_store_available_with_embedding(self, tmp_path: Path) -> None:
        """Lines 288-292: MemoryStore.available() is True and embedding is not None."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mock_store = MagicMock()
        mock_store_cls = MagicMock(return_value=mock_store)
        mock_store_cls.available.return_value = True

        with patch("trw_mcp.state.tiers.MemoryStore", mock_store_cls, create=True):
            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
            with patch.dict("sys.modules", {}):
                assert original_import

        mock_ms = MagicMock()
        mock_ms.available.return_value = True
        mock_instance = MagicMock()
        mock_ms.return_value = mock_instance

        with patch("trw_mcp.state.memory_store.MemoryStore", mock_ms):
            mgr.warm_add("entry-1", {"summary": "test"}, [0.1, 0.2, 0.3])

        mock_instance.upsert.assert_called_once_with("entry-1", [0.1, 0.2, 0.3], {"source": "warm_tier"})
        mock_instance.close.assert_not_called()

    def test_warm_add_memory_store_upsert_exception_propagates(self, tmp_path: Path) -> None:
        """Lines 288-292: MemoryStore.upsert raises — exception propagates."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mock_ms = MagicMock()
        mock_ms.available.return_value = True
        mock_instance = MagicMock()
        mock_instance.upsert.side_effect = RuntimeError("vec error")
        mock_ms.return_value = mock_instance

        with patch("trw_mcp.state.memory_store.MemoryStore", mock_ms):
            with pytest.raises(RuntimeError, match="vec error"):
                mgr.warm_add("entry-1", {"summary": "test"}, [0.1, 0.2, 0.3])


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

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
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

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
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

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
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

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            mgr.warm_remove("nonexistent")


class TestWarmSearchFallbackPath:
    """Test warm_search falls back to keyword search when MemoryStore unavailable."""

    def test_warm_search_no_memorystore_uses_keyword_fallback(self, tmp_path: Path) -> None:
        """When MemoryStore.available() is False, keyword search is used."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mgr._warm_sidecar_upsert("w1", {"summary": "pytest patterns", "tags": []})
        mgr._warm_sidecar_upsert("w2", {"summary": "docker setup", "tags": []})

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            results = mgr.warm_search(["pytest"], query_embedding=[0.1] * 384)

        assert len(results) == 1
        assert results[0]["id"] == "w1"

    def test_warm_search_no_embedding_uses_keyword_fallback(self, tmp_path: Path) -> None:
        """When query_embedding is None, keyword search is used even if store available."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mgr._warm_sidecar_upsert("w1", {"summary": "testing patterns", "tags": []})

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = True
            results = mgr.warm_search(["testing"], query_embedding=None)

        assert len(results) == 1
        assert results[0]["id"] == "w1"
