"""Recovery-boundary tests for ``trw_mcp.state._memory_connection``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.exceptions import CorruptDatabaseUnsalvageableError

from trw_mcp.state import _memory_connection


def test_get_backend_does_not_retry_after_strict_refusal(tmp_path: Path) -> None:
    """Strict recovery refusal must surface instead of creating a fresh empty DB."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    terminal = CorruptDatabaseUnsalvageableError(
        "database disk image is malformed and salvage yielded 0 rows",
        backup_path=str(trw_dir / "memory" / "memory.db.corrupt.test.bak"),
    )
    fake_backend_cls = MagicMock(side_effect=terminal)
    fake_config = SimpleNamespace(retrieval_embedding_dim=384)

    _memory_connection.reset_backend()
    try:
        with (
            patch.object(_memory_connection, "SQLiteBackend", fake_backend_cls),
            patch("trw_mcp.models.config.get_config", return_value=fake_config),
            patch.object(_memory_connection.logger, "error") as mock_error,
        ):
            with pytest.raises(CorruptDatabaseUnsalvageableError):
                _memory_connection.get_backend(trw_dir)
    finally:
        _memory_connection.reset_backend()

    fake_backend_cls.recover_db.assert_not_called()
    mock_error.assert_called_once()
    assert mock_error.call_args.args == ("memory_recovery_terminal",)
    assert mock_error.call_args.kwargs["backup_path"] == terminal.backup_path
    assert fake_backend_cls.call_count == 1
