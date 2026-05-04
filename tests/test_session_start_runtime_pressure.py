"""PRD-FIX-080 regression tests for MCP timeout hardening."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_pressure import (
    live_memory_writer_pids,
    should_defer_memory_side_effects,
    should_defer_session_start_optional_work,
)
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


def _run_async(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _get_tools_sync(server: Any) -> dict[str, Any]:
    tools = _run_async(server.list_tools())
    return {tool.name: tool for tool in tools}


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
    # Self-only registration is the steady state for both the stdio per-instance
    # server and the shared HTTP server — neither should trigger deferral, which
    # would otherwise turn into a permanent skip and let WAL grow unbounded.
    should_defer_optional, optional_pids, optional_reason = should_defer_session_start_optional_work(
        trw_dir,
        threshold=2,
    )
    assert should_defer_optional is False
    assert optional_pids == [live_pid]
    assert optional_reason == ""


def test_should_defer_session_start_optional_work_triggers_on_peer_pid(tmp_path: Path) -> None:
    """A live peer pid (other than self) is the actual pressure signal."""

    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())
    _write_lock(trw_dir, "peer.lock", os.getppid())

    pids = live_memory_writer_pids(trw_dir)
    assert os.getpid() in pids
    assert os.getppid() in pids

    should_defer, defer_pids, reason = should_defer_session_start_optional_work(
        trw_dir,
        threshold=2,
    )
    assert should_defer is True
    assert reason in {"writer_present", "writer_pressure"}
    assert defer_pids == sorted({os.getpid(), os.getppid()})


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


def test_perform_session_recalls_compacts_if_pressure_appears_after_recall(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
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
            "tags": ["tag"],
        }
        for idx in range(20)
    ]

    def _recall(*args: object, max_results: int | None = None, **kwargs: object) -> list[dict[str, object]]:
        return entries[: max_results or len(entries)]

    with (
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_recall),
        patch("trw_mcp.models.config.get_config", return_value=config),
        patch("trw_mcp.tools._session_recall_helpers.record_session_start_surfaces", return_value=[]),
        patch("trw_mcp.tools._session_recall_helpers.log_ranked_selections"),
        patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        patch(
            "trw_mcp.state.memory_pressure.should_defer_memory_side_effects",
            side_effect=[(False, []), (True, [os.getpid(), os.getppid()])],
        ),
    ):
        learnings, _auto, extra = perform_session_recalls(trw_dir, "mcp timeout", config, MagicMock())

    assert len(learnings) == 8
    assert extra["response_compacted"] is True
    assert all("tags" not in entry for entry in learnings)


def test_perform_session_recalls_defers_optional_side_effects_on_writer_presence(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "peer.lock", os.getppid())
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
            "tags": ["tag"],
        }
        for idx in range(12)
    ]

    def _recall(*args: object, max_results: int | None = None, **kwargs: object) -> list[dict[str, object]]:
        return entries[: max_results or len(entries)]

    with (
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_recall),
        patch(
            "trw_mcp.tools._session_recall_helpers.log_ranked_selections",
            side_effect=AssertionError("propensity logging must leave session_start hot path"),
        ),
        patch(
            "trw_mcp.tools._session_recall_helpers.record_session_start_surfaces",
            side_effect=AssertionError("surface tracking must leave session_start hot path"),
        ),
        patch(
            "trw_mcp.tools._session_recall_helpers.log_recall_receipt",
            side_effect=AssertionError("receipt logging must leave session_start hot path"),
        ),
    ):
        learnings, _auto, extra = perform_session_recalls(trw_dir, "mcp timeout", config, MagicMock())

    assert len(learnings) == 8
    assert extra["response_compacted"] is True
    side_effects_deferred = extra["side_effects_deferred"]
    assert side_effects_deferred["reason"] == "writer_present"
    assert side_effects_deferred["writer_count"] == 1


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


def test_run_auto_maintenance_defers_optional_checks_on_writer_presence(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "peer.lock", os.getppid())
    config = TRWConfig(session_start_defer_under_writer_pressure=True, session_start_writer_pressure_threshold=2)

    with (
        patch(
            "trw_mcp.state.auto_upgrade.check_for_update",
            side_effect=AssertionError("auto-upgrade check must leave session_start hot path"),
        ),
        patch(
            "trw_mcp.state.analytics._stale_runs.auto_close_stale_runs",
            side_effect=AssertionError("stale-run cleanup must leave session_start hot path"),
        ),
        patch(
            "trw_mcp.state.memory_adapter.check_embeddings_status",
            side_effect=AssertionError("embedding status must leave session_start hot path"),
        ),
        patch(
            "trw_mcp.state.memory_adapter.maybe_checkpoint_wal",
            side_effect=AssertionError("WAL checkpoint must leave session_start hot path"),
        ),
    ):
        result = run_auto_maintenance(trw_dir, config)

    assert result["auto_upgrade_check_deferred"]["reason"] == "writer_present"
    assert result["stale_runs_deferred"]["reason"] == "writer_pressure"
    assert result["stale_runs_deferred"]["defer_reason"] == "writer_present"
    assert result["embeddings_backfill_deferred"]["reason"] == "writer_present"
    assert result["wal_checkpoint_deferred"]["reason"] == "writer_present"


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


def test_append_ceremony_status_defers_nudges_on_writer_presence(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "peer.lock", os.getppid())
    (trw_dir / "config.yaml").write_text(
        "session_start_defer_under_writer_pressure: true\n"
        "session_start_writer_pressure_threshold: 2\n"
        "nudge_enabled: true\n",
        encoding="utf-8",
    )

    from trw_mcp.tools._ceremony_status import append_ceremony_status

    with patch(
        "trw_mcp.state._ceremony_progress_state.increment_tool_call_counter",
        side_effect=AssertionError("counter write must be deferred when any writer is present"),
    ):
        response = append_ceremony_status({}, trw_dir=trw_dir)

    nudge_deferred = response["nudge_deferred"]
    assert isinstance(nudge_deferred, dict)
    assert nudge_deferred["reason"] == "writer_present"


def test_build_check_always_defers_q_learning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD-FIX-088 FR01: Q-learning is ALWAYS deferred (not only under writer pressure).

    Pre-FIX-088 this test asserted ``reason == 'writer_present'``; that
    deferral path was conditional on detected peer writers and inline
    otherwise. The 91-second hang on 2026-05-04 (call dc084e2b)
    proved the inline path was unsafe at any corpus size, so FIX-088
    made deferral unconditional. The reason now is the literal
    ``"deferred_always"``.
    """
    from fastmcp import FastMCP

    import trw_mcp.tools.build as build_mod
    import trw_mcp.tools.build._registration as reg_mod

    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "peer.lock", os.getppid())
    config = TRWConfig.model_validate(
        {
            "trw_dir": str(trw_dir),
        }
    )

    monkeypatch.setattr(reg_mod, "get_config", lambda: config)
    monkeypatch.setattr(reg_mod, "resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(reg_mod, "find_active_run", lambda **kwargs: None)
    # The bg worker catches exceptions and logs; we don't need to
    # inject one to prove inline-Q-learning would have failed. We just
    # stub the work so the worker exits cleanly.
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )

    server = FastMCP("test")
    build_mod.register_build_tools(server)
    tool_fn = _get_tools_sync(server)["trw_build_check"].fn

    result = tool_fn(tests_passed=True, test_count=1, static_checks_clean=True, scope="focused")

    assert result["tests_passed"] is True
    q_learning_deferred = result["q_learning_deferred"]
    assert isinstance(q_learning_deferred, dict)
    assert q_learning_deferred["reason"] == "deferred_always"
    assert q_learning_deferred["thread_state"] in {"launched", "queued"}
