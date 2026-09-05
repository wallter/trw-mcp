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


# ---------------------------------------------------------------------------
# PRD-CORE-263-FR07 — the background-recovery classification reaches the scheduler
# ---------------------------------------------------------------------------


def _background_recovery_error(trw_dir: Path) -> CorruptDatabaseUnsalvageableError:
    """The typed error the storage layer raises for the background classification.

    ``backup_path`` carries ``preflight.state_path`` in this branch — the durable
    recovery-state locator, not a ``.bak`` file.
    """
    return CorruptDatabaseUnsalvageableError(
        "database recovery requires background recovery outside startup budget",
        backup_path=str(trw_dir / "memory" / "memory.db.recovery-state.json"),
    )


def test_background_recovery_classification_schedules_deferred_recovery(tmp_path: Path) -> None:
    """PRD-CORE-263-FR07 — one scheduling call, a degraded empty result, no raise.

    At HEAD the typed-error branch re-raised ONE branch above the scheduler, so
    the classification's entire purpose was unreachable through recall: the store
    was never scheduled for repair and the caller got an exception instead of a
    degraded result. Attribution: restoring the bare ``raise`` turns the
    ``scheduled.call_count == 1`` assertion red.
    """
    from trw_mcp.state import memory_adapter as _facade

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    terminal = _background_recovery_error(trw_dir)
    backend = MagicMock()
    backend.list_entries.side_effect = terminal

    with (
        patch.object(_facade, "get_backend", return_value=backend),
        patch.object(_facade, "_memory_recovery_in_progress", return_value=False),
        patch.object(_facade, "initialize_canaries"),
        patch.object(_facade, "probe_canaries"),
        patch.object(_facade, "should_halt_recalls", return_value=False),
        patch.object(_facade, "_schedule_deferred_recovery", return_value=True) as scheduled,
        patch.object(_facade, "_log_terminal_recovery") as terminal_log,
    ):
        result = _facade.recall_learnings(trw_dir, "*")

    # A degraded EMPTY result, not a propagating exception.
    assert result == []
    # Exactly one scheduling call, carrying the durable recovery-state locator.
    assert scheduled.call_count == 1
    assert scheduled.call_args.kwargs["context"]["recovery_state"] == terminal.backup_path
    # The terminal log line an operator watches for is still emitted.
    terminal_log.assert_called_once()


def test_repeat_background_recovery_error_schedules_nothing_new(tmp_path: Path) -> None:
    """PRD-CORE-263-FR07 — scheduling stays single-flight."""
    from trw_mcp.state import _memory_recovery
    from trw_mcp.state import memory_adapter as _facade

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    backend = MagicMock()
    backend.list_entries.side_effect = _background_recovery_error(trw_dir)

    started: list[str] = []

    def _fake_schedule(_dir: Path, *, reason: str, context: dict[str, object] | None = None) -> bool:
        # Mirrors the real single-flight contract: no second worker while one runs.
        if started:
            return False
        started.append(reason)
        return True

    with (
        patch.object(_facade, "get_backend", return_value=backend),
        patch.object(_facade, "_memory_recovery_in_progress", return_value=False),
        patch.object(_facade, "initialize_canaries"),
        patch.object(_facade, "probe_canaries"),
        patch.object(_facade, "should_halt_recalls", return_value=False),
        patch.object(_facade, "_schedule_deferred_recovery", side_effect=_fake_schedule),
        patch.object(_facade, "_log_terminal_recovery"),
    ):
        assert _facade.recall_learnings(trw_dir, "*") == []
        assert _facade.recall_learnings(trw_dir, "*") == []

    assert started == ["recall_unsalvageable_background_recovery"]
    assert _memory_recovery is not None  # the single-flight guard lives there


def test_scheduling_failure_surfaces_the_terminal_error(tmp_path: Path) -> None:
    """PRD-CORE-263-FR07 refuse-on-exception.

    If the scheduling itself fails there is no repair pending, so an empty list
    would read as "nothing recalled". The original terminal error is surfaced.
    """
    from trw_mcp.state import memory_adapter as _facade

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    backend = MagicMock()
    backend.list_entries.side_effect = _background_recovery_error(trw_dir)

    with (
        patch.object(_facade, "get_backend", return_value=backend),
        patch.object(_facade, "_memory_recovery_in_progress", return_value=False),
        patch.object(_facade, "initialize_canaries"),
        patch.object(_facade, "probe_canaries"),
        patch.object(_facade, "should_halt_recalls", return_value=False),
        patch.object(_facade, "_schedule_deferred_recovery", side_effect=RuntimeError("no thread")),
        patch.object(_facade, "_log_terminal_recovery"),
        pytest.raises(CorruptDatabaseUnsalvageableError),
    ):
        _facade.recall_learnings(trw_dir, "*")
