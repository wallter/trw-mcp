"""PRD-FIX-080 regression tests for MCP timeout hardening."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_pressure import live_memory_writer_pids, should_defer_memory_side_effects
from trw_mcp.tools._ceremony_helpers import run_auto_maintenance
from trw_mcp.tools._session_recall_helpers import perform_session_recalls, record_session_start_surfaces


def _minimal_trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory" / "memory.db.writers").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "learnings" / "receipts").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


def _write_lock(trw_dir: Path, name: str, pid: int) -> None:
    (trw_dir / "memory" / "memory.db.writers" / name).write_text(f"{pid}\n0\n", encoding="utf-8")


def test_live_memory_writer_pids_ignores_stale_locks(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    live_pid = os.getpid()
    _write_lock(trw_dir, "live.lock", live_pid)
    _write_lock(trw_dir, "stale.lock", 999_999_999)
    (trw_dir / "memory" / "memory.db.writers" / "bad.lock").write_text("not-a-pid\n", encoding="utf-8")

    assert live_memory_writer_pids(trw_dir) == [live_pid]
    should_defer, pids = should_defer_memory_side_effects(trw_dir, threshold=2)
    assert should_defer is False
    assert pids == [live_pid]


def test_record_session_start_surfaces_defers_sqlite_tracking_under_writer_pressure(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())
    _write_lock(trw_dir, "parent.lock", os.getppid())
    config = TRWConfig(session_start_defer_under_writer_pressure=True, session_start_writer_pressure_threshold=2)

    with (
        patch("trw_mcp.models.config.get_config", return_value=config),
        patch("trw_mcp.state.memory_adapter.increment_session_counts") as increment_counts,
        patch("trw_mcp.state.memory_adapter.update_access_tracking") as update_access,
        patch("trw_mcp.tools._session_recall_helpers._log_session_start_surfaces") as log_surfaces,
    ):
        result = record_session_start_surfaces(trw_dir, ["L-one", "L-one", "L-two"])

    assert result == ["L-one", "L-two"]
    increment_counts.assert_not_called()
    update_access.assert_not_called()
    log_surfaces.assert_not_called()


def test_perform_session_recalls_compacts_response_under_writer_pressure(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())
    _write_lock(trw_dir, "parent.lock", os.getppid())
    config = TRWConfig.model_validate(
        {
            "recall_max_results": 25,
            "session_start_defer_under_writer_pressure": True,
            "session_start_writer_pressure_threshold": 2,
        }
    )
    entries = [
        {
            "id": f"L-{idx}",
            "summary": f"Learning {idx}",
            "impact": 0.9,
            "status": "active",
            "tags": ["tag", "mcp", "timeout"],
            "detail": "verbose detail that should not be returned under writer pressure",
        }
        for idx in range(20)
    ]

    def _recall(*args: object, max_results: int | None = None, **kwargs: object) -> list[dict[str, object]]:
        return entries[: max_results or len(entries)]

    with (
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_recall) as recall,
        patch("trw_mcp.models.config.get_config", return_value=config),
        patch("trw_mcp.tools._session_recall_helpers.log_ranked_selections"),
        patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
    ):
        learnings, _auto, extra = perform_session_recalls(trw_dir, "mcp timeout", config, MagicMock())

    assert recall.call_count == 2
    assert {call.kwargs["max_results"] for call in recall.call_args_list} == {8}
    assert len(learnings) == 8
    assert extra["response_compacted"] is True
    assert all(set(entry) <= {"id", "summary", "impact", "status"} for entry in learnings)


def test_run_auto_maintenance_defers_backfill_and_wal_under_writer_pressure(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())
    _write_lock(trw_dir, "parent.lock", os.getppid())
    config = TRWConfig(session_start_defer_under_writer_pressure=True, session_start_writer_pressure_threshold=2)

    backfill = MagicMock(return_value={"embedded": 1, "skipped": 0, "failed": 0})
    wal = MagicMock(return_value={"checkpointed": True})

    stale_close = MagicMock(return_value={"count": 1, "runs_closed": ["stale-run"], "errors": []})

    with (
        patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
        patch("trw_mcp.state.analytics._stale_runs.auto_close_stale_runs", stale_close),
        patch(
            "trw_mcp.state.memory_adapter.check_embeddings_status",
            return_value={"enabled": True, "available": True, "advisory": ""},
        ),
        patch("trw_mcp.state.memory_adapter.backfill_embeddings", backfill),
        patch("trw_mcp.state.memory_adapter.maybe_checkpoint_wal", wal),
    ):
        result = run_auto_maintenance(trw_dir, config)

    backfill.assert_not_called()
    wal.assert_not_called()
    stale_close.assert_not_called()
    assert result["stale_runs_deferred"]["reason"] == "writer_pressure"
    assert result["embeddings_backfill_deferred"]["reason"] == "writer_pressure"
    assert result["wal_checkpoint_deferred"]["reason"] == "writer_pressure"


def test_candidate_run_hints_list_live_pinned_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._pin_store import upsert_pin_entry
    from trw_mcp.tools.ceremony import _candidate_run_hints

    _reset_config()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    trw_dir = tmp_path / config.trw_dir
    trw_dir.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / config.runs_root / "task" / "20260502T000000Z-runtime-hardening"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text("status: active\nphase: implement\n", encoding="utf-8")

    upsert_pin_entry("other-session", run_dir)

    candidates = _candidate_run_hints(limit=1)

    assert candidates
    assert candidates[0]["run_path"] == str(run_dir)
    assert "trw_adopt_run" in str(candidates[0]["adopt_command"])


def test_append_ceremony_status_defers_nudges_under_writer_pressure(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())
    _write_lock(trw_dir, "parent.lock", os.getppid())
    (trw_dir / "config.yaml").write_text(
        "session_start_defer_under_writer_pressure: true\n"
        "session_start_writer_pressure_threshold: 2\n"
        "nudge_enabled: true\n",
        encoding="utf-8",
    )

    from trw_mcp.tools._ceremony_status import append_ceremony_status

    with (
        patch(
            "trw_mcp.state._ceremony_progress_state.increment_tool_call_counter",
            side_effect=AssertionError("counter write must be deferred under writer pressure"),
        ),
        patch(
            "trw_mcp.state._paths.resolve_run_path",
            side_effect=AssertionError("active run resolution must be deferred under writer pressure"),
        ),
    ):
        response = append_ceremony_status({}, trw_dir=trw_dir)

    assert "ceremony_status" in response
    nudge_deferred = response["nudge_deferred"]
    assert isinstance(nudge_deferred, dict)
    assert nudge_deferred["reason"] == "writer_pressure"
    assert "nudge_content" not in response
