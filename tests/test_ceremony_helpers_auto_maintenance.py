"""Tests for ceremony auto-maintenance helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests._ceremony_helpers_support import write_installed_version
from tests._structlog_capture import captured_structlog as captured_structlog
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._ceremony_helpers import run_auto_maintenance


class TestRunAutoMaintenance:
    """Auto-maintenance operations: upgrade, stale runs, embeddings."""

    def test_returns_empty_when_nothing_needed(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "trw_mcp.state.analytics._stale_runs.auto_close_stale_runs",
                return_value={"runs_closed": [], "count": 0, "errors": []},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result
        assert "auto_upgrade" not in result
        assert "stale_runs_closed" not in result

    def test_includes_update_advisory_when_available(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": True, "advisory": "v2.0 available"},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert result["update_advisory"] == "v2.0 available"

    def test_failopen_on_upgrade_error(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                side_effect=Exception("network error"),
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert isinstance(result, dict)

    def test_embeddings_advisory_included(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"advisory": "Install anthropic SDK for embeddings"},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "embeddings_advisory" in result

    def test_auto_upgrade_performed_when_enabled(
        self,
        trw_dir: Path,
    ) -> None:
        """Lines 181-189: When auto_upgrade=True and upgrade is applied."""
        cfg = TRWConfig(auto_upgrade=True)  # type: ignore[call-arg]
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": True, "advisory": "v2.0 available"},
            ),
            patch(
                "trw_mcp.state.auto_upgrade.perform_upgrade",
                return_value={"applied": True, "version": "2.0.0", "details": "patch applied"},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, cfg)

        assert result["update_advisory"] == "v2.0 available"
        assert result["auto_upgrade"]["applied"] is True
        assert result["auto_upgrade"]["version"] == "2.0.0"

    def test_embeddings_backfill_failopen_on_exception(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Lines 215-216: Embeddings block fails open on exception."""
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                side_effect=Exception("embeddings boom"),
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert isinstance(result, dict)
        assert "embeddings_advisory" not in result
        assert "embeddings_backfill" not in result

    def test_embeddings_backfill_not_performed_on_session_start_hot_path(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Session startup never performs a bulk synchronous embedding backfill.

        PRD-CORE-263 DEF-11: named ``embeddings_backfill_not_performed``, not
        ``_deferred`` — nothing schedules a later bulk backfill for a healthy
        corpus; this is the standing hot-path policy, not a deferral with a
        consumer.
        """
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": True, "available": True, "advisory": ""},
            ),
            patch(
                "trw_mcp.state.memory_adapter.backfill_embeddings",
                side_effect=AssertionError("bulk embedding backfill must leave trw_session_start"),
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "embeddings_backfill" not in result
        assert result["embeddings_backfill_not_performed"]["reason"] == "session_start_hot_path"

    def test_wal_checkpoint_success_is_reported(self, trw_dir: Path, config: TRWConfig) -> None:
        with (
            patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
            patch("trw_mcp.state.memory_adapter.check_embeddings_status", return_value={"enabled": False}),
            patch(
                "trw_mcp.state.memory_adapter.maybe_checkpoint_wal",
                return_value={"checkpointed": True, "pages": 4},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert result["wal_checkpoint"] == {"checkpointed": True, "pages": 4}

    def test_wal_checkpoint_failure_is_isolated(self, trw_dir: Path, config: TRWConfig) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": True, "advisory": "upgrade available"},
            ),
            patch("trw_mcp.state.memory_adapter.check_embeddings_status", return_value={"enabled": False}),
            patch("trw_mcp.state.memory_adapter.maybe_checkpoint_wal", side_effect=OSError("busy")),
            patch("trw_mcp.tools._ceremony_helpers.logger") as logger,
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert result["update_advisory"] == "upgrade available"
        logger.warning.assert_any_call("maintenance_wal_checkpoint_failed", exc_info=True)

    def test_version_sentinel_mismatch_injects_advisory(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Version sentinel with mismatched version produces update_advisory."""
        write_installed_version(trw_dir, "99.0.0")
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" in result
        assert "99.0.0" in str(result["update_advisory"])
        assert "/mcp" in str(result["update_advisory"])

    def test_version_sentinel_matching_no_advisory(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Version sentinel matching running version does not inject advisory."""
        write_installed_version(trw_dir, "0.15.0")
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_older_on_disk_no_advisory(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Potemkin defect D: an on-disk version OLDER than the running process
        must NOT inject a reload advisory.

        The reported symptom was "TRW vOLD was installed but still running vNEW —
        Run /mcp to reload", where reloading would downgrade, not update. The
        advisory must fire only on a genuine pending upgrade (on-disk newer).
        """
        write_installed_version(trw_dir, "0.48.7")
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.55.14",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_missing_no_error(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Missing sentinel file does not cause errors."""
        sentinel = trw_dir / "installed-version.json"
        assert not sentinel.exists()
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_corrupt_json_no_error(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Corrupt sentinel JSON does not crash maintenance."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text("not valid json{{{", encoding="utf-8")
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert isinstance(result, dict)

    def test_version_sentinel_missing_version_key(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Sentinel JSON without 'version' key produces no advisory."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_importlib_failure(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """importlib.metadata failure produces no advisory and no crash."""
        write_installed_version(trw_dir, "99.0.0")
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                side_effect=Exception("package not found"),
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_existing_advisory_preserved(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Pre-existing update_advisory is not overwritten by sentinel check."""
        write_installed_version(trw_dir, "99.0.0")
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": True, "advisory": "upstream advisory"},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" in result

    def test_version_sentinel_e2e_upgrade_cycle(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """E2E: installer writes sentinel → session_start detects mismatch → advisory."""
        write_installed_version(trw_dir, "0.16.0")

        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.1",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" in result
        advisory = str(result["update_advisory"])
        assert "0.16.0" in advisory
        assert "0.15.1" in advisory
        assert "/mcp" in advisory

    def test_version_sentinel_e2e_no_advisory_after_reload(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """E2E: after /mcp reload (versions match), no advisory appears."""
        write_installed_version(trw_dir, "0.16.0")

        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.16.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_no_platform_imports(self) -> None:
        """FR09: _check_version_sentinel uses no platform-specific imports."""
        import inspect

        from trw_mcp.tools._ceremony_helpers import _check_version_sentinel

        source = inspect.getsource(_check_version_sentinel)
        assert "import signal" not in source
        assert "import fcntl" not in source
        assert "sys.platform" not in source


class TestLowCoverageBackgroundBackfill:
    """PRD-FIX-105-FR01: a low-coverage advisory must schedule a background
    backfill so a post-recovery vector loss self-heals instead of crying wolf
    every session with no remediation path."""

    def test_low_coverage_advisory_schedules_background_backfill(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """When coverage is low (advisory + ratio), a background backfill is scheduled."""
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={
                    "enabled": True,
                    "available": True,
                    "advisory": "Vector coverage is low: 350/7651 entries have embeddings (4.6%).",
                    "coverage_ratio": 0.046,
                },
            ),
            patch(
                "trw_mcp.state._memory_connection._schedule_post_recovery_backfill",
                return_value=True,
            ) as mock_sched,
        ):
            result = run_auto_maintenance(trw_dir, config)

        mock_sched.assert_called_once_with(trw_dir)
        assert "embeddings_advisory" in result
        assert result["embeddings_backfill_scheduled"]["reason"] == "low_coverage"
        assert result["embeddings_backfill_scheduled"]["thread_started"] is True
        # Low coverage must NOT take the "nothing to do" deferred-hot-path branch.
        assert "embeddings_backfill_deferred" not in result

    def test_low_coverage_backfill_disabled_by_config_flag(
        self,
        trw_dir: Path,
    ) -> None:
        """With the kill switch off, low coverage warns but schedules no backfill."""
        cfg = TRWConfig(embeddings_auto_backfill_on_low_coverage=False)  # type: ignore[call-arg]
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={
                    "enabled": True,
                    "available": True,
                    "advisory": "Vector coverage is low: 350/7651 entries have embeddings (4.6%).",
                    "coverage_ratio": 0.046,
                },
            ),
            patch(
                "trw_mcp.state._memory_connection._schedule_post_recovery_backfill",
                return_value=True,
            ) as mock_sched,
        ):
            result = run_auto_maintenance(trw_dir, cfg)

        mock_sched.assert_not_called()
        assert "embeddings_advisory" in result
        assert "embeddings_backfill_scheduled" not in result

    def test_healthy_coverage_does_not_schedule_backfill(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """No advisory (healthy coverage) → no background backfill scheduled."""
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={
                    "enabled": True,
                    "available": True,
                    "advisory": "",
                    "coverage_ratio": 0.999,
                },
            ),
            patch(
                "trw_mcp.state._memory_connection._schedule_post_recovery_backfill",
                return_value=True,
            ) as mock_sched,
        ):
            result = run_auto_maintenance(trw_dir, config)

        mock_sched.assert_not_called()
        assert "embeddings_backfill_scheduled" not in result
        # Healthy path still leaves the synchronous bulk backfill off the hot
        # path (PRD-CORE-263 DEF-11: "not performed", not "deferred").
        assert result["embeddings_backfill_not_performed"]["reason"] == "session_start_hot_path"


# ---------------------------------------------------------------------------
# PRD-CORE-263-FR04 — every maintenance result is propagated or declared internal
# ---------------------------------------------------------------------------


def _declared_maintenance_keys() -> set[str]:
    from trw_mcp.models.typed_dicts._ceremony import AutoMaintenanceDict

    return set(AutoMaintenanceDict.__annotations__)


def test_every_maintenance_key_is_propagated_or_declared_internal() -> None:
    """PRD-CORE-263-FR04 — totality over the declared result type.

    At HEAD the propagation allowlist named 11 of the 14 declared keys, and the
    three it omitted (``wal_checkpoint``, ``embeddings_coverage_ratio``,
    ``embedder_warmup_scheduled``) were computed on the hot path of every session
    and dropped. Attribution: reverting FR04 turns this red naming exactly those
    three under ``dropped``.
    """
    from trw_mcp.tools._ceremony_step_table import (
        MAINTENANCE_INTERNAL_KEYS,
        MAINTENANCE_PROPAGATED_KEYS,
    )

    declared = _declared_maintenance_keys()
    propagated = set(MAINTENANCE_PROPAGATED_KEYS)
    internal = set(MAINTENANCE_INTERNAL_KEYS)

    dropped = declared - propagated - internal
    assert not dropped, (
        f"maintenance keys computed and classified in neither list: {sorted(dropped)}. "
        "Add each to MAINTENANCE_PROPAGATED_KEYS or MAINTENANCE_INTERNAL_KEYS."
    )
    # The other direction: an allowlist entry the result type cannot produce is
    # a dead string that would silently never fire.
    orphaned = (propagated | internal) - declared
    assert not orphaned, f"classified keys AutoMaintenanceDict does not declare: {sorted(orphaned)}"
    # The two classifications must not overlap — a key cannot be both.
    assert not (propagated & internal)
    # Non-vacuity: the exclusion set is the mechanism, not the documentation.
    assert len(propagated) >= 14


def test_unclassified_maintenance_key_fails_the_totality_check_by_name() -> None:
    """PRD-CORE-263-FR04 — an added-but-unclassified key must fail BY NAME."""
    from trw_mcp.tools._ceremony_step_table import (
        MAINTENANCE_INTERNAL_KEYS,
        MAINTENANCE_PROPAGATED_KEYS,
    )

    declared = _declared_maintenance_keys() | {"newly_added_unclassified_key"}
    dropped = declared - set(MAINTENANCE_PROPAGATED_KEYS) - set(MAINTENANCE_INTERNAL_KEYS)
    assert dropped == {"newly_added_unclassified_key"}


def test_dropped_maintenance_results_now_reach_the_payload() -> None:
    """PRD-CORE-263-FR04 — the three keys arrive in the session-start payload."""
    from typing import cast

    from trw_mcp.tools import _ceremony_step_table as table

    sweep = {
        "wal_checkpoint": {"checkpointed": True, "mode": "PASSIVE"},
        "embeddings_coverage_ratio": 0.87,
        "embedder_warmup_scheduled": {"scheduled": True},
    }
    results: dict[str, object] = {}
    sctx = table.SessionStartContext(
        query="",
        config=cast("object", None),  # type: ignore[arg-type]
        ctx=None,
        is_focused=False,
        results=cast("object", results),  # type: ignore[arg-type]
        errors=[],
    )
    with patch("trw_mcp.tools._ceremony_helpers.step_sanitize_and_maintain", return_value=sweep):
        table._ss_sanitize_maintain(sctx)

    assert results["wal_checkpoint"] == sweep["wal_checkpoint"]
    assert results["embeddings_coverage_ratio"] == 0.87
    assert results["embedder_warmup_scheduled"] == sweep["embedder_warmup_scheduled"]


# ---------------------------------------------------------------------------
# PRD-CORE-263-NFR03 — one emission per condition
# ---------------------------------------------------------------------------


def test_each_degradation_condition_emits_exactly_one_event(captured_structlog: list[dict[str, object]]) -> None:
    """PRD-CORE-263-NFR03 — a condition that emits twice looks like two to a counter.

    Three conditions this PRD owns are injected, and each must produce exactly
    one structured event. The field vocabulary is checked at the same time: an
    event may carry step names, error classes, counts, booleans and reasons, and
    must never carry learning summary or detail text.
    """
    from typing import cast

    from trw_mcp.tools import _pipeline_health as ph
    from trw_mcp.tools._ceremony_degradations import DegradationCollector
    from trw_mcp.tools._ceremony_step_table import SessionStartContext, Step, run_steps
    from trw_mcp.tools._sync_health import step_sync_health

    seeded_summary = "SEEDED-LEARNING-SUMMARY-do-not-log"
    seeded_detail = "SEEDED-LEARNING-DETAIL-do-not-log"

    # 1. A critical step failure.
    def _boom(_sctx: SessionStartContext) -> None:
        raise RuntimeError(seeded_summary[:0] or "critical boom")

    facade = type("Facade", (), {"_ss_boom": staticmethod(_boom)})
    results: dict[str, object] = {"learnings": [{"summary": seeded_summary, "detail": seeded_detail}]}
    sctx = SessionStartContext(
        query="",
        config=cast("object", None),  # type: ignore[arg-type]
        ctx=None,
        is_focused=False,
        results=cast("object", results),  # type: ignore[arg-type]
        errors=[],
    )
    run_steps((Step("crit", "_ss_boom", critical=True),), sctx, cast("object", facade))
    assert sum(1 for e in captured_structlog if e.get("event") == "crit_degraded") == 1

    # 2. An unreadable sync-state file.
    collector = DegradationCollector()
    with patch("pathlib.Path.is_file", return_value=True), patch("pathlib.Path.read_text", side_effect=OSError("io")):
        step_sync_health(Path("/nonexistent"), cast("object", None), collector)  # type: ignore[arg-type]
    assert sum(1 for e in captured_structlog if e.get("event") == "sync_health_degraded") == 1

    # 3. A probe that could not measure.
    #
    # DEF-10: this used to ALSO assert ``pipeline_health_unmeasured`` fired
    # once here — the SAME occurrence logged from a second call site
    # (``step_pipeline_health``'s aggregate rollup), which is the exact
    # duplicate-emission shape NFR03 forbids. The aggregator no longer emits
    # that second event; the per-probe ``pipeline_probe_failed`` (with the
    # real error class + traceback) is the one source of truth, and the
    # ``unmeasured`` list in the returned payload is the aggregate view.
    with patch.object(ph, "probe_sync_push", side_effect=RuntimeError("probe boom")):
        aggregate = ph.step_pipeline_health(Path("/nonexistent"))
    # DEF-08: bandit_state.json also does not exist under "/nonexistent", so
    # it too reports unmeasured now (a fabricated healthy 0.0-day age is the
    # separate defect that fix removed) — assert membership, not exact shape.
    assert "sync_push" in aggregate["unmeasured"]
    assert sum(1 for e in captured_structlog if e.get("event") == "pipeline_probe_failed") == 1
    assert sum(1 for e in captured_structlog if e.get("event") == "pipeline_health_unmeasured") == 0

    # No learning content anywhere in the captured field values.
    flat = " ".join(str(value) for event in captured_structlog for value in event.values())
    assert seeded_summary not in flat
    assert seeded_detail not in flat


def test_the_recall_deferral_emits_one_event_for_one_condition() -> None:
    """PRD-CORE-263-NFR03 — the two adjacent deferral events are collapsed.

    ``record_session_start_surfaces`` used to fire ``session_start_tracking_deferred``
    and ``session_start_surface_log_deferred`` unconditionally together, so one
    occurrence looked like two to anything counting. It now emits one event
    carrying the operations it covers.

    This limb landed via the sibling PRD-CORE-257-FR09 change to the same
    branch; the assertion is held here because NFR03 owns the invariant.

    A second instance of the SAME defect was found across the call boundary
    rather than within one function: ``record_session_start_surfaces`` also
    logged ``session_start_tracking_deferred`` on its own ``defer=True``
    branch, while its only caller that can pass ``defer=True`` —
    ``perform_session_recalls`` — logged ``session_start_side_effects_deferred``
    for that exact same writer-pressure condition with richer context (writer
    counts, threshold, deferral age). One pressured recall therefore emitted
    two deferral events. The inner one was dropped in favour of the caller's
    richer event, so this walks BOTH functions' source and asserts exactly one
    deferral event total across the pair.
    """
    import ast
    import inspect

    from trw_mcp.tools import _session_recall_helpers as recall_helpers
    from trw_mcp.tools import _session_recall_pressure as pressure

    def _deferral_events(fn: object) -> list[object]:
        source = inspect.getsource(fn)  # type: ignore[arg-type]
        events = [
            node.args[0].value
            for node in ast.walk(ast.parse(inspect.cleandoc(source)))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"warning", "info", "error"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]
        return [name for name in events if "deferred" in str(name)]

    inner_deferral_events = _deferral_events(pressure.record_session_start_surfaces)
    assert len(inner_deferral_events) == 0, (
        f"record_session_start_surfaces must not log its own deferral event — the caller's "
        f"session_start_side_effects_deferred already covers this condition with richer "
        f"context; found {inner_deferral_events}"
    )

    caller_deferral_events = _deferral_events(recall_helpers.perform_session_recalls)
    assert len(caller_deferral_events) == 1, (
        f"perform_session_recalls must be the sole emitter of the recall-deferral event; found {caller_deferral_events}"
    )

    total_deferral_events = inner_deferral_events + caller_deferral_events
    assert len(total_deferral_events) == 1, f"one condition, one event — found {total_deferral_events}"
