"""Tests for PRD-FIX-COMPOUNDING-3: Post-recovery embeddings backfill.

Covers:
- FR01: get_backend() schedules backfill when backend.recovered==True
- FR02: check_embeddings_status(coverage_probe=True) reports coverage_ratio + advisory
- FR03: _recover_and_reset_backend() triggers backfill after deferred recovery
- FR04: backfill_embeddings() logs at WARNING when missing_count > 0
- FR05: 5+ tests covering all FRs
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import structlog.testing


# ---------------------------------------------------------------------------
# FR01: get_backend() schedules backfill on recovery
# ---------------------------------------------------------------------------


def test_get_backend_schedules_backfill_on_recovery(tmp_path: Path) -> None:
    """FR01: When backend.recovered==True after get_backend(), a backfill thread starts."""
    import trw_mcp.state._memory_connection as mc

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True, exist_ok=True)
    # Sentinel must exist so unlink() succeeds inside get_backend
    sentinel = trw_dir / "memory" / ".migrated"
    sentinel.write_text("migrated_at=2026-01-01T00:00:00")

    mock_backend = MagicMock()
    mock_backend.recovered = True

    schedule_calls: list[Path] = []

    def fake_schedule(d: Path) -> bool:
        schedule_calls.append(d)
        return True

    with (
        patch.object(mc, "_backend", None),
        patch("trw_mcp.state._memory_connection._create_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection.ensure_migrated", return_value={"migrated": 0, "skipped": 0}),
        patch("trw_mcp.state._memory_connection._schedule_post_recovery_backfill", side_effect=fake_schedule),
    ):
        from trw_mcp.models.config import TRWConfig

        mock_cfg = TRWConfig()
        with patch("trw_mcp.models.config.get_config", return_value=mock_cfg):
            result = mc.get_backend(trw_dir)

    assert result is mock_backend
    assert len(schedule_calls) == 1, "Expected _schedule_post_recovery_backfill to be called once"
    assert schedule_calls[0] == trw_dir


def test_get_backend_no_backfill_when_not_recovered(tmp_path: Path) -> None:
    """FR01 regression: backfill must NOT be scheduled when backend.recovered==False."""
    import trw_mcp.state._memory_connection as mc

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True, exist_ok=True)

    mock_backend = MagicMock()
    mock_backend.recovered = False

    schedule_calls: list[Path] = []

    def fake_schedule(d: Path) -> bool:
        schedule_calls.append(d)
        return True

    with (
        patch.object(mc, "_backend", None),
        patch("trw_mcp.state._memory_connection._create_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection.ensure_migrated", return_value={"migrated": 0, "skipped": 0}),
        patch("trw_mcp.state._memory_connection._schedule_post_recovery_backfill", side_effect=fake_schedule),
    ):
        from trw_mcp.models.config import TRWConfig

        mock_cfg = TRWConfig()
        with patch("trw_mcp.models.config.get_config", return_value=mock_cfg):
            mc.get_backend(trw_dir)

    assert len(schedule_calls) == 0, "backfill must not be scheduled when backend.recovered is False"


# ---------------------------------------------------------------------------
# FR01 helper: _schedule_post_recovery_backfill guard
# ---------------------------------------------------------------------------


def test_schedule_post_recovery_backfill_guard_prevents_duplicate(tmp_path: Path) -> None:
    """FR01: _BACKFILL_THREAD guard returns False on second call while thread alive."""
    import trw_mcp.state._memory_connection as mc

    trw_dir = tmp_path / ".trw"

    backfill_started = threading.Event()
    backfill_block = threading.Event()

    def slow_backfill(d: Path) -> dict[str, int]:
        backfill_started.set()
        backfill_block.wait(timeout=5)
        return {"embedded": 0, "skipped": 0, "failed": 0}

    # Reset thread guard before test to ensure clean state
    original_thread = mc._BACKFILL_THREAD
    mc._BACKFILL_THREAD = None
    try:
        with patch("trw_mcp.state._memory_connection.backfill_embeddings", side_effect=slow_backfill):
            first = mc._schedule_post_recovery_backfill(trw_dir)
            assert first is True, "first call should start the thread"

            backfill_started.wait(timeout=5)

            second = mc._schedule_post_recovery_backfill(trw_dir)
            assert second is False, "second call while thread alive should return False"
    finally:
        backfill_block.set()
        if mc._BACKFILL_THREAD is not None:
            mc._BACKFILL_THREAD.join(timeout=5)
        mc._BACKFILL_THREAD = original_thread


# ---------------------------------------------------------------------------
# FR02: check_embeddings_status coverage_probe
# ---------------------------------------------------------------------------


def test_check_embeddings_status_coverage_ratio_present_when_probed(tmp_path: Path) -> None:
    """FR02: coverage_probe=True always includes coverage_ratio in result."""
    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import TRWConfig

    mock_backend = MagicMock()
    mock_backend.existing_vector_ids.return_value = {"id1", "id2"}
    mock_backend.count.return_value = 20  # 2/20 = 0.10

    mock_embedder = MagicMock()
    cfg = TRWConfig(embeddings_enabled=True)  # type: ignore[call-arg]

    with (
        patch.object(mc, "_embedder_checked", True),
        patch.object(mc, "_embedder", mock_embedder),
        patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=mock_embedder),
        patch("trw_mcp.state._memory_connection.peek_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection._append_wal_health"),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        result = mc.check_embeddings_status(coverage_probe=True, allow_initialize=False)

    assert "coverage_ratio" in result, "coverage_ratio must be present when coverage_probe=True"
    ratio = float(str(result["coverage_ratio"]))
    assert abs(ratio - 0.1) < 0.001, f"Expected 0.1 (2/20), got {ratio}"


def test_check_embeddings_status_coverage_below_threshold_emits_advisory(tmp_path: Path) -> None:
    """FR02: coverage below threshold (0.05 < 0.10 default) emits advisory."""
    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import TRWConfig

    mock_backend = MagicMock()
    # 1/20 = 0.05 which is < 0.10 default threshold → advisory fires
    mock_backend.existing_vector_ids.return_value = {"id1"}
    mock_backend.count.return_value = 20

    mock_embedder = MagicMock()
    cfg = TRWConfig(embeddings_enabled=True)  # type: ignore[call-arg]

    with (
        patch.object(mc, "_embedder_checked", True),
        patch.object(mc, "_embedder", mock_embedder),
        patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=mock_embedder),
        patch("trw_mcp.state._memory_connection.peek_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection._append_wal_health"),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        result = mc.check_embeddings_status(coverage_probe=True, allow_initialize=False)

    assert "coverage_ratio" in result
    ratio = float(str(result["coverage_ratio"]))
    assert abs(ratio - 0.05) < 0.001, f"Expected 0.05, got {ratio}"
    advisory = str(result.get("advisory", ""))
    assert advisory, "advisory must be non-empty when coverage < threshold"
    assert "vector" in advisory.lower() or "coverage" in advisory.lower(), (
        f"advisory should mention coverage/vector: {advisory!r}"
    )


def test_check_embeddings_status_coverage_above_threshold_no_advisory(tmp_path: Path) -> None:
    """FR02: High coverage (95%) produces empty advisory."""
    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import TRWConfig

    mock_backend = MagicMock()
    mock_backend.existing_vector_ids.return_value = {f"id{i}" for i in range(95)}
    mock_backend.count.return_value = 100  # 95% — well above 10% threshold

    mock_embedder = MagicMock()
    cfg = TRWConfig(embeddings_enabled=True)  # type: ignore[call-arg]

    with (
        patch.object(mc, "_embedder_checked", True),
        patch.object(mc, "_embedder", mock_embedder),
        patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=mock_embedder),
        patch("trw_mcp.state._memory_connection.peek_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection._append_wal_health"),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        result = mc.check_embeddings_status(coverage_probe=True, allow_initialize=False)

    assert "coverage_ratio" in result
    assert not result.get("advisory"), "advisory must be empty when coverage is above threshold"


def test_coverage_probe_false_does_not_call_backend_methods(tmp_path: Path) -> None:
    """FR02 regression: coverage_probe=False (default) must not call backend.count()."""
    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import TRWConfig

    mock_backend = MagicMock()
    mock_embedder = MagicMock()
    cfg = TRWConfig(embeddings_enabled=True)  # type: ignore[call-arg]

    with (
        patch.object(mc, "_embedder_checked", True),
        patch.object(mc, "_embedder", mock_embedder),
        patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=mock_embedder),
        patch("trw_mcp.state._memory_connection.peek_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection._append_wal_health"),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        result = mc.check_embeddings_status(coverage_probe=False, allow_initialize=False)

    mock_backend.existing_vector_ids.assert_not_called()
    mock_backend.count.assert_not_called()
    assert "coverage_ratio" not in result, "coverage_ratio must NOT be present when coverage_probe=False"


def test_coverage_probe_default_is_false_backward_compat(tmp_path: Path) -> None:
    """FR02: default coverage_probe=False — no coverage_ratio key in result (backward compat)."""
    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import TRWConfig

    mock_backend = MagicMock()
    mock_embedder = MagicMock()
    cfg = TRWConfig(embeddings_enabled=True)  # type: ignore[call-arg]

    with (
        patch.object(mc, "_embedder_checked", True),
        patch.object(mc, "_embedder", mock_embedder),
        patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=mock_embedder),
        patch("trw_mcp.state._memory_connection.peek_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection._append_wal_health"),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        # No coverage_probe argument → defaults to False
        result = mc.check_embeddings_status(allow_initialize=False)

    assert "coverage_ratio" not in result


# ---------------------------------------------------------------------------
# FR03: _recover_and_reset_backend triggers backfill
# ---------------------------------------------------------------------------


def test_deferred_recovery_triggers_backfill(tmp_path: Path) -> None:
    """FR03: _recover_and_reset_backend() schedules backfill after recovery.

    The explicit backfill call in _recover_and_reset_backend() fires for the case
    where backend.recovered==False (manual recover_db doesn't set the recovered flag).
    """
    import trw_mcp.state._memory_recovery as mr

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True, exist_ok=True)

    schedule_calls: list[Path] = []

    def fake_schedule(d: Path) -> bool:
        schedule_calls.append(d)
        return True

    # Simulate backend that has recovered=False (explicit FR03 branch fires)
    mock_backend = MagicMock()
    mock_backend.recovered = False

    # Patch at source modules since local imports resolve there at call time
    with (
        patch("trw_mcp.state._memory_connection.get_backend", return_value=mock_backend),
        patch("trw_mcp.state._memory_connection.reset_backend"),
        patch("trw_mcp.state._memory_connection._schedule_post_recovery_backfill", side_effect=fake_schedule),
        patch.object(mr, "_logger", return_value=MagicMock()),
    ):
        mr._recover_and_reset_backend(trw_dir)

    assert len(schedule_calls) == 1, "Expected backfill to be scheduled after deferred recovery"
    assert schedule_calls[0] == trw_dir


# ---------------------------------------------------------------------------
# FR04: backfill_embeddings logs WARNING on start when vectors missing
# ---------------------------------------------------------------------------


def test_backfill_start_warning_logged_when_missing(tmp_path: Path) -> None:
    """FR04: WARNING log emitted at backfill start when missing_count > 0."""
    import trw_mcp.state._memory_connection as mc

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)

    mock_backend = MagicMock()
    mock_backend.existing_vector_ids.return_value = set()
    mock_backend.count.return_value = 5  # 5 missing → FR04 warning fires

    mock_entry = MagicMock()
    mock_entry.id = "test-id"
    mock_entry.content = "some content"
    mock_entry.detail = "some detail"
    mock_entry.metadata = {}
    mock_backend.list_entries.return_value = [mock_entry] * 5

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 384

    with (
        patch("trw_mcp.state._memory_connection.get_embedder", return_value=mock_embedder),
        patch("trw_mcp.state._memory_connection.get_backend", return_value=mock_backend),
    ):
        with structlog.testing.capture_logs() as cap:
            mc.backfill_embeddings(trw_dir)

    log_events = [e["event"] for e in cap]
    warning_events = [e for e in cap if e.get("log_level") == "warning"]

    start_warnings = [
        e for e in warning_events
        if "backfill" in e.get("event", "").lower() and "start" in e.get("event", "").lower()
    ]
    assert start_warnings, (
        f"Expected a WARNING log at backfill start when missing_count>0. Got: {log_events}"
    )
    start_warn = start_warnings[0]
    assert "missing_count" in start_warn or "total_entries" in start_warn, (
        f"Start warning must include count fields: {start_warn}"
    )


def test_backfill_no_start_warning_when_all_embedded(tmp_path: Path) -> None:
    """FR04: No start WARNING when all entries already have vectors (fast-path)."""
    import trw_mcp.state._memory_connection as mc

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)

    mock_backend = MagicMock()
    mock_backend.existing_vector_ids.return_value = {"id1", "id2", "id3", "id4", "id5"}
    mock_backend.count.return_value = 5  # all embedded → no warning

    mock_embedder = MagicMock()

    with (
        patch("trw_mcp.state._memory_connection.get_embedder", return_value=mock_embedder),
        patch("trw_mcp.state._memory_connection.get_backend", return_value=mock_backend),
    ):
        with structlog.testing.capture_logs() as cap:
            result = mc.backfill_embeddings(trw_dir)

    warning_events = [e for e in cap if e.get("log_level") == "warning"]
    start_warnings = [
        e for e in warning_events
        if "backfill" in e.get("event", "").lower() and "start" in e.get("event", "").lower()
    ]
    assert not start_warnings, f"No start WARNING expected when nothing to backfill, got: {start_warnings}"
    assert result["embedded"] == 0
    assert result["skipped"] == 5


# ---------------------------------------------------------------------------
# FR05 integration: coverage_probe=True wired in run_auto_maintenance
# ---------------------------------------------------------------------------


def test_run_auto_maintenance_passes_coverage_probe(tmp_path: Path) -> None:
    """FR02/FR05: run_auto_maintenance calls check_embeddings_status with coverage_probe=True."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()

    probe_values_seen: list[bool] = []

    def fake_check_embeddings(**kwargs: Any) -> dict[str, object]:
        probe_values_seen.append(bool(kwargs.get("coverage_probe", False)))
        return {
            "enabled": True,
            "available": True,
            "advisory": "",
            "recent_failures": 0,
        }

    with (
        patch("trw_mcp.state.memory_adapter.check_embeddings_status", side_effect=fake_check_embeddings),
        patch("trw_mcp.state.memory_pressure.should_defer_session_start_optional_work", return_value=(False, [], "none")),
        patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
        patch("trw_mcp.state.analytics._stale_runs.auto_close_stale_runs", return_value={"count": 0}),
        patch("trw_mcp.state.memory_adapter.maybe_checkpoint_wal", return_value={"checkpointed": False}),
    ):
        from trw_mcp.tools._ceremony_helpers import run_auto_maintenance

        run_auto_maintenance(
            trw_dir=tmp_path,
            config=cfg,
        )

    assert any(probe_values_seen), (
        "run_auto_maintenance must call check_embeddings_status with coverage_probe=True. "
        f"Probe values seen: {probe_values_seen}"
    )
