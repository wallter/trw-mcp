"""Split scoring outcome coverage tests from test_recall_scoring_report.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._recall_scoring_report_support import make_recall_tracking_log
from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestProcessOutcome:
    """Cover process_outcome file-system and history-reset paths."""

    def _setup_trw_dir(self, tmp_path: Path, writer: FileStateWriter) -> Path:
        """Create minimal .trw structure with receipts directory."""
        del writer

        trw_dir = tmp_path / ".trw"
        receipts_dir = trw_dir / get_config().learnings_dir / get_config().receipts_dir
        receipts_dir.mkdir(parents=True, exist_ok=True)
        return trw_dir

    def test_entries_dir_missing_returns_empty(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When entries_dir doesn't exist after correlation, returns [] (line 857)."""
        from trw_mcp.scoring import process_outcome

        trw_dir = self._setup_trw_dir(tmp_path, writer)
        now_ts = datetime.now(timezone.utc).isoformat()
        make_recall_tracking_log(tmp_path, writer, [{"ts": now_ts, "matched_ids": ["L-ghost"]}])

        result = process_outcome(trw_dir, 0.8, "tests_passed")
        assert result == []

    def test_unknown_learning_id_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Learning ID in receipt but not found in entries is skipped (line 866)."""
        from trw_mcp.scoring import process_outcome

        trw_dir = self._setup_trw_dir(tmp_path, writer)
        entries_dir = trw_dir / get_config().learnings_dir / get_config().entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        now_ts = datetime.now(timezone.utc).isoformat()
        make_recall_tracking_log(tmp_path, writer, [{"ts": now_ts, "matched_ids": ["L-missing"]}])

        result = process_outcome(trw_dir, 0.8, "tests_passed")
        assert result == []

    def test_non_list_outcome_history_reset(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When outcome_history is not a list, it is reset to [] (line 890)."""
        from trw_mcp.scoring import process_outcome

        trw_dir = self._setup_trw_dir(tmp_path, writer)
        entries_dir = trw_dir / get_config().learnings_dir / get_config().entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        writer.write_yaml(
            entries_dir / "L-corrupt.yaml",
            {
                "id": "L-corrupt",
                "summary": "corrupt history",
                "detail": "d",
                "impact": 0.7,
                "q_value": 0.7,
                "q_observations": 5,
                "recurrence": 1,
                "access_count": 0,
                "source_type": "agent",
                "outcome_history": "this should be a list",
            },
        )

        now_ts = datetime.now(timezone.utc).isoformat()
        make_recall_tracking_log(tmp_path, writer, [{"ts": now_ts, "matched_ids": ["L-corrupt"]}])

        result = process_outcome(trw_dir, 0.8, "tests_passed")
        assert "L-corrupt" in result

        updated = reader.read_yaml(entries_dir / "L-corrupt.yaml")
        assert isinstance(updated.get("outcome_history"), list)


class TestProcessOutcomeHistoryCap:
    """Cover outcome_history trimming when it exceeds history_cap."""

    def test_history_trimmed_when_exceeds_cap(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When outcome_history exceeds history_cap, it is trimmed (line 893)."""
        del monkeypatch
        from trw_mcp.scoring import process_outcome

        trw_dir = tmp_path / ".trw"
        receipts_dir = trw_dir / get_config().learnings_dir / get_config().receipts_dir
        receipts_dir.mkdir(parents=True)
        entries_dir = trw_dir / get_config().learnings_dir / get_config().entries_dir
        entries_dir.mkdir(parents=True)

        history_cap = get_config().learning_outcome_history_cap
        existing_history = [f"2026-01-0{i % 9 + 1}:+0.8:tests_passed" for i in range(history_cap)]
        writer.write_yaml(
            entries_dir / "2026-01-01-L-capped.yaml",
            {
                "id": "L-capped",
                "summary": "capped history learning",
                "detail": "d",
                "impact": 0.7,
                "q_value": 0.7,
                "q_observations": 5,
                "recurrence": 1,
                "access_count": 0,
                "source_type": "agent",
                "outcome_history": existing_history,
            },
        )

        now_ts = datetime.now(timezone.utc).isoformat()
        make_recall_tracking_log(tmp_path, writer, [{"ts": now_ts, "matched_ids": ["L-capped"]}])

        result = process_outcome(trw_dir, 0.8, "tests_passed")
        assert "L-capped" in result

        updated = reader.read_yaml(entries_dir / "2026-01-01-L-capped.yaml")
        history = updated.get("outcome_history", [])
        assert isinstance(history, list)
        assert len(history) <= history_cap


class TestProcessOutcomeForEventException:
    """Cover process_outcome_for_event exception handling."""

    def test_state_error_returns_empty_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """StateError from resolve_trw_dir returns [] without propagating (lines 999-1001)."""
        del tmp_path
        import trw_mcp.scoring._utils as scoring_utils
        from trw_mcp.scoring import process_outcome_for_event

        monkeypatch.setattr(
            scoring_utils,
            "resolve_trw_dir",
            lambda: (_ for _ in ()).throw(StateError("no .trw", path="none")),
        )

        result = process_outcome_for_event("tests_passed")
        assert result == []

    def test_os_error_returns_empty_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError from resolve_trw_dir returns [] without propagating (lines 999-1001)."""
        del tmp_path
        import trw_mcp.scoring._utils as scoring_utils
        from trw_mcp.scoring import process_outcome_for_event

        monkeypatch.setattr(
            scoring_utils,
            "resolve_trw_dir",
            lambda: (_ for _ in ()).throw(OSError("permission denied")),
        )

        result = process_outcome_for_event("tests_passed")
        assert result == []

    def test_none_reward_returns_empty_without_resolve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Events with no reward don't call resolve_trw_dir at all."""
        import trw_mcp.scoring._utils as scoring_utils
        from trw_mcp.scoring import process_outcome_for_event

        resolve_called = [False]

        def mock_resolve() -> Path:
            resolve_called[0] = True
            return Path("/fake")

        monkeypatch.setattr(scoring_utils, "resolve_trw_dir", mock_resolve)

        result = process_outcome_for_event("shard_started")
        assert result == []
        assert not resolve_called[0]

    def test_success_path_calls_process_outcome(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: resolve_trw_dir succeeds and process_outcome is called (lines 997-998)."""
        import trw_mcp.scoring._utils as scoring_utils
        from trw_mcp.scoring import process_outcome_for_event

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        monkeypatch.setattr(scoring_utils, "resolve_trw_dir", lambda: trw_dir)

        result = process_outcome_for_event("tests_passed")
        assert isinstance(result, list)


class TestProcessOutcomeForEventSuccessPath:
    """Cover successful resolve_trw_dir + process_outcome calls."""

    def test_process_outcome_called_with_valid_trw_dir(self, tmp_path: Path) -> None:
        """When resolve_trw_dir succeeds, process_outcome is invoked (lines 997-998)."""
        from trw_mcp.scoring import process_outcome_for_event

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch("trw_mcp.scoring._utils.resolve_trw_dir", return_value=trw_dir):
            result = process_outcome_for_event("phase_gate_passed")
        assert isinstance(result, list)

    def test_process_outcome_returns_updated_ids_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Full success path: receipts exist + entries exist -> IDs returned."""
        del monkeypatch
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import process_outcome_for_event

        cfg = TRWConfig()
        trw_dir = tmp_path / ".trw"

        now_ts = datetime.now(timezone.utc).isoformat()
        make_recall_tracking_log(tmp_path, writer, [{"ts": now_ts, "matched_ids": ["L-target"]}])

        entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
        entries_dir.mkdir(parents=True)
        writer.write_yaml(
            entries_dir / "2026-01-01-L-target.yaml",
            {
                "id": "L-target",
                "summary": "target learning",
                "detail": "d",
                "impact": 0.7,
                "q_value": 0.7,
                "q_observations": 5,
                "recurrence": 1,
                "access_count": 0,
                "source_type": "agent",
            },
        )

        with patch("trw_mcp.scoring._utils.resolve_trw_dir", return_value=trw_dir):
            result = process_outcome_for_event("tests_passed")
        assert "L-target" in result
