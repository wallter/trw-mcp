"""Tests for learning helper dedup handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._learning_helpers_test_support import _CFG, set_project_root  # noqa: F401
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._learning_helpers import LearningParams, check_and_handle_dedup


class TestCheckAndHandleDedup:
    """Tests for semantic dedup check helper."""

    def test_returns_none_when_disabled(self, tmp_path: Path) -> None:
        """When dedup is disabled, returns None (proceed to store)."""
        cfg = _CFG.model_copy(update={"dedup_enabled": False})
        result = check_and_handle_dedup(
            LearningParams(
                summary="summary",
                detail="detail",
                learning_id="L-test001",
                tags=["tag"],
                evidence=["evidence"],
                impact=0.8,
                source_type="agent",
                source_identity="",
            ),
            tmp_path / "entries",
            FileStateReader(),
            FileStateWriter(),
            cfg,
        )
        assert result is None

    def test_returns_none_when_no_duplicate(self, tmp_path: Path) -> None:
        """When no duplicate found, returns None."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.action = "store"
        mock_result.existing_id = None
        mock_result.similarity = 0.1

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            return_value=mock_result,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="new summary",
                    detail="new detail",
                    learning_id="L-test002",
                    tags=[],
                    evidence=[],
                    impact=0.5,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                FileStateReader(),
                FileStateWriter(),
                _CFG,
            )
            assert result is None

    def test_returns_skip_result_on_exact_duplicate(self, tmp_path: Path) -> None:
        """When dedup says skip, returns a skip result dict."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.action = "skip"
        mock_result.existing_id = "L-existing001"
        mock_result.similarity = 0.98

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            return_value=mock_result,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="duplicate summary",
                    detail="duplicate detail",
                    learning_id="L-test003",
                    tags=[],
                    evidence=[],
                    impact=0.5,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                FileStateReader(),
                FileStateWriter(),
                _CFG,
            )
            assert result is not None
            assert result["status"] == "skipped"
            assert result["duplicate_of"] == "L-existing001"
            assert result["similarity"] == 0.98

    def test_returns_merge_result_on_near_duplicate(self, tmp_path: Path) -> None:
        """When dedup says merge, merges and returns a merge result dict."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        existing_data = {
            "id": "L-existing002",
            "summary": "Existing learning",
            "detail": "Existing detail",
            "tags": [],
            "evidence": [],
            "impact": 0.7,
        }
        writer.write_yaml(entries_dir / "existing.yaml", existing_data)

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing002"
        mock_dedup.similarity = 0.88

        with (
            patch(
                "trw_mcp.state.dedup.check_duplicate",
                return_value=mock_dedup,
            ),
            patch(
                "trw_mcp.state.dedup.merge_entries",
            ) as mock_merge,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-duplicate summary",
                    detail="near-duplicate detail",
                    learning_id="L-test004",
                    tags=["tag"],
                    evidence=["evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )
            assert result is not None
            assert result["status"] == "merged"
            assert result["merged_into"] == "L-existing002"
            assert result["new_id"] == "L-test004"
            assert "message" in result
            assert mock_merge.called

    def test_merge_skips_index_yaml(self, tmp_path: Path) -> None:
        """Line 178: index.yaml is skipped when scanning for merge target."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        writer.write_yaml(
            entries_dir / "index.yaml",
            {
                "id": "L-existing010",
                "summary": "Index entry",
            },
        )
        writer.write_yaml(
            entries_dir / "real-entry.yaml",
            {
                "id": "L-existing010",
                "summary": "Real entry",
                "detail": "Detail",
                "tags": [],
                "evidence": [],
                "impact": 0.7,
            },
        )

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing010"
        mock_dedup.similarity = 0.85

        with (
            patch(
                "trw_mcp.state.dedup.check_duplicate",
                return_value=mock_dedup,
            ),
            patch(
                "trw_mcp.state.dedup.merge_entries",
            ) as mock_merge,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-dup summary",
                    detail="near-dup detail",
                    learning_id="L-test010",
                    tags=["tag"],
                    evidence=["evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        assert result["merged_into"] == "L-existing010"
        assert result["new_id"] == "L-test010"
        assert mock_merge.called
        actual_path = mock_merge.call_args[0][0]
        assert actual_path.name == "real-entry.yaml"

    def test_merge_inner_read_exception_continues(self, tmp_path: Path) -> None:
        """Lines 210-211: Exception reading a yaml file during merge scan continues."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        (entries_dir / "corrupt.yaml").write_text("{{invalid", encoding="utf-8")
        writer.write_yaml(
            entries_dir / "valid.yaml",
            {
                "id": "L-existing020",
                "summary": "Valid",
                "detail": "Detail",
                "tags": [],
                "evidence": [],
                "impact": 0.7,
            },
        )

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing020"
        mock_dedup.similarity = 0.85

        reader = FileStateReader()

        with (
            patch(
                "trw_mcp.state.dedup.check_duplicate",
                return_value=mock_dedup,
            ),
            patch(
                "trw_mcp.state.dedup.merge_entries",
            ) as mock_merge,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-dup summary",
                    detail="near-dup detail",
                    learning_id="L-test020",
                    tags=["tag"],
                    evidence=["evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        assert result["merged_into"] == "L-existing020"
        assert result["new_id"] == "L-test020"
        assert mock_merge.called

    def test_merge_syncs_merged_yaml_to_backend(self, tmp_path: Path) -> None:
        """Merged YAML state is written back to SQLite after dedup merges."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        writer.write_yaml(
            entries_dir / "existing.yaml",
            {
                "id": "L-existing030",
                "summary": "Existing learning",
                "detail": "short detail",
                "tags": ["existing"],
                "evidence": ["existing-evidence"],
                "impact": 0.6,
                "recurrence": 1,
                "merged_from": [],
                "assertions": [{"type": "grep_present", "pattern": "old", "target": "**/*.py"}],
            },
        )

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing030"
        mock_dedup.similarity = 0.89
        mock_backend = MagicMock()

        with (
            patch("trw_mcp.state.dedup.check_duplicate", return_value=mock_dedup),
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend),
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path / ".trw"),
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="Existing learning",
                    detail="this replacement detail is much longer than the old one",
                    learning_id="L-test030",
                    tags=["new-tag"],
                    evidence=["new-evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                    assertions=[{"type": "glob_exists", "pattern": "", "target": "src/main.py"}],
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        update_kwargs = mock_backend.update.call_args.kwargs
        assert update_kwargs["recurrence"] == 2
        assert update_kwargs["importance"] == 0.8
        assert update_kwargs["tags"] == ["existing", "new-tag"]
        assert update_kwargs["evidence"] == ["existing-evidence", "new-evidence"]
        assert update_kwargs["merged_from"] == ["L-test030"]
        assert "Merged from L-test030" in update_kwargs["detail"]
        assert len(update_kwargs["assertions"]) == 2

    def test_fail_open_on_dedup_exception(self, tmp_path: Path) -> None:
        """When dedup check throws, returns None (proceed to store)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            side_effect=RuntimeError("dedup boom"),
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="summary",
                    detail="detail",
                    learning_id="L-test005",
                    tags=[],
                    evidence=[],
                    impact=0.5,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                FileStateReader(),
                FileStateWriter(),
                _CFG,
            )
            assert result is None
