"""Misc coverage tests for state-layer branches."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestIndexSyncCoverage:
    """Cover index_sync.py uncovered lines."""

    def test_scan_prd_dir_exception_logged_and_skipped(self, tmp_path: Path) -> None:
        """Lines 79-80: exception during PRD scan is caught and logged."""
        from trw_mcp.state.index_sync import _scan_prd_dir

        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()

        bad_prd = prds_dir / "PRD-TEST-001.md"
        bad_prd.write_text("---\nid: null\ntitle:\n---\n# content", encoding="utf-8")

        with patch("trw_mcp.state.index_sync.parse_frontmatter") as mock_parse:
            mock_parse.side_effect = ValueError("bad frontmatter")
            entries = _scan_prd_dir(prds_dir)

        assert entries == []

    def test_scan_prd_dir_oserror_skipped(self, tmp_path: Path) -> None:
        """Lines 79-80: OSError during file read is caught."""
        from trw_mcp.state.index_sync import _scan_prd_dir

        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()

        prd_file = prds_dir / "PRD-TEST-001.md"
        prd_file.write_text("---\nid: PRD-TEST-001\n---\n", encoding="utf-8")

        with patch.object(Path, "read_text") as mock_read:
            mock_read.side_effect = OSError("disk error")
            entries = _scan_prd_dir(prds_dir)

        assert entries == []

    def test_scan_prd_frontmatters_adds_archived_entry(self, tmp_path: Path) -> None:
        """Lines 107-109: archived PRD with new ID gets added to entries."""
        from trw_mcp.state.index_sync import scan_prd_frontmatters

        req_root = tmp_path / "docs" / "requirements"
        prds_dir = req_root / "prds"
        prds_dir.mkdir(parents=True)

        archive_prds = req_root / "archive" / "prds"
        archive_prds.mkdir(parents=True)

        active_prd = prds_dir / "PRD-CORE-001.md"
        active_prd.write_text(
            "---\nid: PRD-CORE-001\ntitle: Active PRD\npriority: P1\nstatus: done\ncategory: CORE\n---\n",
            encoding="utf-8",
        )

        archived_prd = archive_prds / "PRD-CORE-002.md"
        archived_prd.write_text(
            "---\nid: PRD-CORE-002\ntitle: Archived PRD\npriority: P2\nstatus: deprecated\ncategory: CORE\n---\n",
            encoding="utf-8",
        )

        entries = scan_prd_frontmatters(prds_dir)

        ids = [e.id for e in entries]
        assert "PRD-CORE-001" in ids
        assert "PRD-CORE-002" in ids

    def test_scan_prd_frontmatters_archived_duplicate_skipped(self, tmp_path: Path) -> None:
        """Lines 105-109: archived PRD with SAME ID as active is deduplicated."""
        from trw_mcp.state.index_sync import scan_prd_frontmatters

        req_root = tmp_path / "docs" / "requirements"
        prds_dir = req_root / "prds"
        prds_dir.mkdir(parents=True)
        archive_prds = req_root / "archive" / "prds"
        archive_prds.mkdir(parents=True)

        active_prd = prds_dir / "PRD-CORE-001.md"
        active_prd.write_text(
            "---\nid: PRD-CORE-001\ntitle: Active Version\npriority: P1\nstatus: done\ncategory: CORE\n---\n",
            encoding="utf-8",
        )
        archived_prd = archive_prds / "PRD-CORE-001.md"
        archived_prd.write_text(
            "---\nid: PRD-CORE-001\ntitle: Old Version\npriority: P1\nstatus: deprecated\ncategory: CORE\n---\n",
            encoding="utf-8",
        )

        entries = scan_prd_frontmatters(prds_dir)
        core001_entries = [e for e in entries if e.id == "PRD-CORE-001"]
        assert len(core001_entries) == 1
        assert core001_entries[0].title == "Active Version"

    def test_render_index_catalogue_with_deprecated(self, tmp_path: Path) -> None:
        """Line 196: deprecated count > 0 appends deprecated summary."""
        from trw_mcp.state.index_sync import PRDEntry, render_index_catalogue

        entries = [
            PRDEntry(id="PRD-CORE-001", title="Done PRD", priority="P1", status="done", category="CORE"),
            PRDEntry(id="PRD-CORE-002", title="Deprecated PRD", priority="P2", status="deprecated", category="CORE"),
        ]
        result = render_index_catalogue(entries)
        assert "deprecated" in result
        assert "1 deprecated" in result


class TestRecallTrackingExceptionPath:
    """Lines 66-68: record_outcome exception path."""

    def test_record_outcome_exception_returns_false(self, tmp_path: Path) -> None:
        """Lines 66-68: exception during record_outcome returns False."""
        from trw_mcp.state import recall_tracking

        with patch("trw_mcp.state.recall_tracking.resolve_trw_dir") as mock_resolve:
            mock_resolve.side_effect = RuntimeError("trw dir not found")
            result = recall_tracking.record_outcome("L-abc123", "positive")

        assert result is False

    def test_record_outcome_file_not_exists_returns_false(self, tmp_path: Path) -> None:
        """record_outcome returns False if tracking file doesn't exist (line 56)."""
        from trw_mcp.state import recall_tracking

        with patch("trw_mcp.state.recall_tracking.resolve_trw_dir") as mock_resolve:
            mock_resolve.return_value = tmp_path / ".trw"
            result = recall_tracking.record_outcome("L-abc123", "positive")

        assert result is False

    def test_record_outcome_writer_exception_returns_false(self, tmp_path: Path) -> None:
        """Lines 66-68: FileStateWriter.append_jsonl raises exception."""
        from trw_mcp.state import recall_tracking

        trw_dir = tmp_path / ".trw"
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True)
        tracking_path = logs_dir / "recall_tracking.jsonl"
        tracking_path.write_text("", encoding="utf-8")

        with patch("trw_mcp.state.recall_tracking.resolve_trw_dir") as mock_resolve:
            mock_resolve.return_value = trw_dir
            with patch("trw_mcp.state.recall_tracking.FileStateWriter") as mock_writer_cls:
                mock_writer = MagicMock()
                mock_writer.append_jsonl.side_effect = OSError("write failed")
                mock_writer_cls.return_value = mock_writer
                result = recall_tracking.record_outcome("L-abc123", "neutral")

        assert result is False


class TestPathsCoverage:
    """Lines 58, 172: _find_latest_run_dir and detect_current_phase."""

    def test_find_latest_run_dir_skips_non_dir_runs(self, tmp_path: Path) -> None:
        """iter_run_dirs skips task_dirs without valid run subdirectories."""
        from trw_mcp.state._paths import _find_latest_run_dir

        runs_root = tmp_path / ".trw" / "runs"
        runs_root.mkdir(parents=True)

        task_no_runs = runs_root / "task-no-runs"
        task_no_runs.mkdir()

        task_with_run = runs_root / "task-with-run"
        run_dir = task_with_run / "20260206T120000Z-abc1"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text("run_id: abc1\n", encoding="utf-8")

        result = _find_latest_run_dir(runs_root)
        assert result is not None
        assert result.name == "20260206T120000Z-abc1"

    def test_detect_current_phase_skips_non_dir_runs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """detect_current_phase skips task_dirs without valid run subdirectories."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import _paths

        runs_root = tmp_path / ".trw" / "runs"
        runs_root.mkdir(parents=True)

        no_runs_dir = runs_root / "no-runs-task"
        no_runs_dir.mkdir()

        valid_task = runs_root / "valid-task"
        run_dir = valid_task / "20260206T120000Z-xyz1"
        (run_dir / "meta").mkdir(parents=True)
        run_yaml = run_dir / "meta" / "run.yaml"
        run_yaml.write_text("run_id: xyz1\nphase: deliver\nstatus: complete\n", encoding="utf-8")

        cfg = TRWConfig()
        object.__setattr__(cfg, "runs_root", ".trw/runs")

        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths.get_config", return_value=cfg):
                result = _paths.detect_current_phase()

        assert result is None

    def test_detect_current_phase_inactive_run_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """detect_current_phase returns None when status != active."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import _paths

        runs_root = tmp_path / ".trw" / "runs"
        task_dir = runs_root / "my-task"
        run_dir = task_dir / "20260206T120000Z-xyz1"
        (run_dir / "meta").mkdir(parents=True)
        run_yaml = run_dir / "meta" / "run.yaml"
        run_yaml.write_text("run_id: xyz1\nphase: deliver\nstatus: complete\n", encoding="utf-8")

        cfg = TRWConfig()
        object.__setattr__(cfg, "runs_root", ".trw/runs")

        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths.get_config", return_value=cfg):
                result = _paths.detect_current_phase()

        assert result is None

    def test_detect_current_phase_active_run_returns_phase(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Positive: detect_current_phase returns phase when status == active."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import _paths

        runs_root = tmp_path / ".trw" / "runs"
        task_dir = runs_root / "my-task"
        run_dir = task_dir / "20260206T120000Z-xyz2"
        (run_dir / "meta").mkdir(parents=True)
        run_yaml = run_dir / "meta" / "run.yaml"
        run_yaml.write_text("run_id: xyz2\nphase: implement\nstatus: active\n", encoding="utf-8")

        cfg = TRWConfig()
        object.__setattr__(cfg, "runs_root", ".trw/runs")

        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths.get_config", return_value=cfg):
                result = _paths.detect_current_phase()

        assert result == "implement"
