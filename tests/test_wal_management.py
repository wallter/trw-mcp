"""Tests for WAL checkpoint management (PRD-QUAL-050-FR05/FR06).

Covers:
- maybe_checkpoint_wal: skip when no WAL, skip under threshold, trigger above threshold, fail-open
- WAL health reporting in check_embeddings_status
- WAL checkpoint wired into run_auto_maintenance
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create minimal .trw structure with a real SQLite WAL-mode database."""
    trw = tmp_path / ".trw"
    memory_dir = trw / "memory"
    memory_dir.mkdir(parents=True)
    (trw / "learnings" / "entries").mkdir(parents=True)

    # Create a real SQLite database in WAL mode so we get a genuine -wal file
    db_path = memory_dir / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS test_data (id INTEGER PRIMARY KEY, val TEXT)")
    conn.commit()
    conn.close()

    return trw


@pytest.fixture()
def config() -> TRWConfig:
    """Default test config."""
    return TRWConfig()


# ---------------------------------------------------------------------------
# FR-05: maybe_checkpoint_wal
# ---------------------------------------------------------------------------


class TestMaybeCheckpointWal:
    """Tests for maybe_checkpoint_wal function."""

    def test_skips_when_no_wal_file(self, trw_dir: Path) -> None:
        """When no WAL file exists, function returns skipped with reason."""
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        # Remove any WAL file that might have been created
        wal_path = trw_dir / "memory" / "memory.db-wal"
        if wal_path.exists():
            wal_path.unlink()

        result = maybe_checkpoint_wal(trw_dir)
        assert result.get("skipped") is True
        assert result.get("reason") == "no_wal_file"

    def test_skips_under_threshold(self, trw_dir: Path) -> None:
        """When WAL file exists but is under threshold, returns skipped."""
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        # Create a small WAL file (1 KB, well under 10 MB default)
        wal_path = trw_dir / "memory" / "memory.db-wal"
        wal_path.write_bytes(b"\x00" * 1024)

        result = maybe_checkpoint_wal(trw_dir)
        assert result.get("skipped") is True
        assert result.get("reason") == "under_threshold"

    def test_triggers_above_threshold(self, trw_dir: Path) -> None:
        """When WAL file exceeds threshold, checkpoint is attempted."""
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        # Create a WAL file that exceeds the default 10 MB threshold
        wal_path = trw_dir / "memory" / "memory.db-wal"
        wal_path.write_bytes(b"\x00" * (11 * 1024 * 1024))  # 11 MB

        # We need to mock the actual checkpoint since our fake WAL file
        # is not a real SQLite WAL and PRAGMA would fail or be a no-op.
        with patch("trw_mcp.state.memory_adapter.get_backend") as mock_get_backend:
            mock_backend = MagicMock()
            # SQLiteBackend exposes .db_path for the database path
            mock_backend.db_path = trw_dir / "memory" / "memory.db"
            mock_get_backend.return_value = mock_backend

            # Mock the sqlite3 connection to simulate checkpoint
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = (0, 100, 100)
            with patch("sqlite3.connect", return_value=mock_conn):
                result = maybe_checkpoint_wal(trw_dir)

        assert result.get("checkpointed") is True
        assert "wal_size_before_mb" in result

    def test_failopen_on_exception(self, trw_dir: Path) -> None:
        """When checkpoint raises an exception, function returns error dict without propagating."""
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        # Create a WAL file above threshold
        wal_path = trw_dir / "memory" / "memory.db-wal"
        wal_path.write_bytes(b"\x00" * (11 * 1024 * 1024))

        # Force an exception during checkpoint
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("locked")):
            result = maybe_checkpoint_wal(trw_dir)

        # Must not raise, must return error info
        assert result.get("error") is True
        assert "reason" in result

    def test_respects_custom_threshold(self, trw_dir: Path) -> None:
        """Config threshold is respected -- 5 MB WAL triggers with 4 MB threshold."""
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        # Create a 5 MB WAL file
        wal_path = trw_dir / "memory" / "memory.db-wal"
        wal_path.write_bytes(b"\x00" * (5 * 1024 * 1024))

        # With default threshold (10 MB), this should be skipped
        result_default = maybe_checkpoint_wal(trw_dir)
        assert result_default.get("skipped") is True

        # With a 4 MB threshold, this should trigger
        with patch("trw_mcp.state.memory_adapter.get_config") as mock_cfg:
            cfg = TRWConfig(wal_checkpoint_threshold_mb=4)
            mock_cfg.return_value = cfg
            with patch("sqlite3.connect") as mock_conn:
                mock_conn.return_value.execute.return_value.fetchone.return_value = (0, 50, 50)
                result_low = maybe_checkpoint_wal(trw_dir)

        assert result_low.get("checkpointed") is True


# ---------------------------------------------------------------------------
# FR-06: WAL health in embeddings status
# ---------------------------------------------------------------------------


class TestWalHealthInEmbeddingsStatus:
    """Tests for WAL size advisory in check_embeddings_status."""

    def test_no_wal_advisory_when_under_threshold(self, trw_dir: Path) -> None:
        """When WAL is under threshold, no wal_size_mb or wal_advisory in response."""
        from trw_mcp.state._memory_connection import _append_wal_health

        # Small WAL file (1 KB, well under 10 MB)
        wal_path = trw_dir / "memory" / "memory.db-wal"
        wal_path.write_bytes(b"\x00" * 1024)

        result: dict[str, object] = {"enabled": False, "available": False}
        with patch(
            "trw_mcp.state._memory_connection._resolve_memory_db_path",
            return_value=trw_dir / "memory" / "memory.db",
        ):
            _append_wal_health(result)

        assert "wal_size_mb" not in result
        assert "wal_advisory" not in result

    def test_wal_advisory_when_above_threshold(self, trw_dir: Path) -> None:
        """When WAL exceeds threshold, health check includes wal_size_mb and wal_advisory."""
        from trw_mcp.state._memory_connection import _append_wal_health

        # Create oversized WAL file (15 MB)
        wal_path = trw_dir / "memory" / "memory.db-wal"
        wal_path.write_bytes(b"\x00" * (15 * 1024 * 1024))

        result: dict[str, object] = {"enabled": False, "available": False}
        with patch(
            "trw_mcp.state._memory_connection._resolve_memory_db_path",
            return_value=trw_dir / "memory" / "memory.db",
        ):
            _append_wal_health(result)

        assert "wal_size_mb" in result
        assert float(str(result["wal_size_mb"])) > 10.0
        assert "wal_advisory" in result
        advisory = str(result["wal_advisory"])
        assert "WAL file is" in advisory
        assert "threshold:" in advisory


# ---------------------------------------------------------------------------
# FR-05: WAL checkpoint wired into auto-maintenance
# ---------------------------------------------------------------------------


class TestWalCheckpointInMaintenance:
    """Tests that WAL checkpoint is called during run_auto_maintenance."""

    def test_wal_checkpoint_called_during_maintenance(self, trw_dir: Path, config: TRWConfig) -> None:
        """run_auto_maintenance invokes maybe_checkpoint_wal and includes result."""
        from trw_mcp.tools._ceremony_helpers import run_auto_maintenance

        mock_wal_result = {"checkpointed": True, "wal_size_before_mb": 12.5, "pages_checkpointed": 100}

        with (
            patch("trw_mcp.tools._ceremony_helpers.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.memory_adapter.check_embeddings_status", return_value={
                "enabled": False, "available": False, "advisory": "", "recent_failures": 0,
            }),
            patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
            patch(
                "trw_mcp.state.memory_adapter.maybe_checkpoint_wal",
                return_value=mock_wal_result,
            ) as mock_wal,
        ):
            result = run_auto_maintenance(trw_dir, config, run_dir=None)

        mock_wal.assert_called_once_with(trw_dir)
        assert result.get("wal_checkpoint") == mock_wal_result

    def test_wal_checkpoint_failure_does_not_block_maintenance(
        self, trw_dir: Path, config: TRWConfig,
    ) -> None:
        """WAL checkpoint failure is fail-open -- maintenance continues normally."""
        from trw_mcp.tools._ceremony_helpers import run_auto_maintenance

        with (
            patch("trw_mcp.tools._ceremony_helpers.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.memory_adapter.check_embeddings_status", return_value={
                "enabled": False, "available": False, "advisory": "", "recent_failures": 0,
            }),
            patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
            patch(
                "trw_mcp.state.memory_adapter.maybe_checkpoint_wal",
                side_effect=RuntimeError("WAL checkpoint exploded"),
            ),
        ):
            # Must not raise
            result = run_auto_maintenance(trw_dir, config, run_dir=None)

        # WAL checkpoint key should NOT be present (it failed)
        assert "wal_checkpoint" not in result

    def test_wal_checkpoint_skipped_result_not_in_maintenance(
        self, trw_dir: Path, config: TRWConfig,
    ) -> None:
        """When WAL checkpoint is skipped (under threshold), maintenance dict excludes it."""
        from trw_mcp.tools._ceremony_helpers import run_auto_maintenance

        mock_wal_result = {"skipped": True, "reason": "under_threshold"}

        with (
            patch("trw_mcp.tools._ceremony_helpers.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.memory_adapter.check_embeddings_status", return_value={
                "enabled": False, "available": False, "advisory": "", "recent_failures": 0,
            }),
            patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
            patch(
                "trw_mcp.state.memory_adapter.maybe_checkpoint_wal",
                return_value=mock_wal_result,
            ),
        ):
            result = run_auto_maintenance(trw_dir, config, run_dir=None)

        # Skipped WAL checkpoint should NOT appear in maintenance dict
        assert "wal_checkpoint" not in result
