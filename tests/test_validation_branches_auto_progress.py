"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import auto_progress_prds

from tests._validation_branches_support import _make_prd_file, _make_run_dir


class TestAutoProgressPrds:
    """Tests for auto_progress_prds function."""

    def test_returns_empty_for_unknown_phase(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        result = auto_progress_prds(
            run_dir,
            "unknown_phase",
            prds_dir,
            TRWConfig(),
        )
        assert result == []

    def test_returns_empty_when_no_prd_scope(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        assert result == []

    def test_skips_missing_prd_file(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-extra1234",
                "task": "test",
                "status": "active",
                "phase": "plan",
                "prd_scope": ["PRD-MISSING-001"],
            },
        )
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        assert result == []

    def test_dry_run_does_not_write_file(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-extra1234",
                "task": "test",
                "status": "active",
                "phase": "plan",
                "prd_scope": ["PRD-TEST-DRY"],
            },
        )
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        prd_file = _make_prd_file(prds_dir, "PRD-TEST-DRY", status="draft")
        original_content = prd_file.read_text(encoding="utf-8")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig(), dry_run=True)
        assert prd_file.read_text(encoding="utf-8") == original_content
        would_apply_entries = [r for r in result if r.get("would_apply") is True]
        assert len(would_apply_entries) >= 1

    def test_applies_transition_for_approved_prd(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-extra1234",
                "task": "test",
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-TEST-IMPL"],
            },
        )
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        _make_prd_file(prds_dir, "PRD-TEST-IMPL", status="approved")

        result = auto_progress_prds(run_dir, "implement", prds_dir, TRWConfig())
        applied = [r for r in result if r.get("applied") is True]
        assert len(applied) == 1
        assert applied[0]["from_status"] == "approved"
        assert applied[0]["to_status"] == "implemented"

    def test_skips_terminal_status_prds(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-extra1234",
                "task": "test",
                "status": "active",
                "phase": "plan",
                "prd_scope": ["PRD-TEST-DONE"],
            },
        )
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        _make_prd_file(prds_dir, "PRD-TEST-DONE", status="done")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        assert result == []

    def test_skips_identity_transition(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-extra1234",
                "task": "test",
                "status": "active",
                "phase": "plan",
                "prd_scope": ["PRD-TEST-ALREADY"],
            },
        )
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        _make_prd_file(prds_dir, "PRD-TEST-ALREADY", status="review")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        assert result == []

    def test_invalid_prd_status_in_file_is_skipped(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-extra1234",
                "task": "test",
                "status": "active",
                "phase": "plan",
                "prd_scope": ["PRD-TEST-BAD"],
            },
        )
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        _make_prd_file(prds_dir, "PRD-TEST-BAD", status="totally_invalid_status")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        assert result == []
