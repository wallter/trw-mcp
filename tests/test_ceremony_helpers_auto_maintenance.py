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
    """Auto-maintenance operations: upgrade, stale runs, WAL checkpoint."""

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
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert isinstance(result, dict)

    def test_never_opens_the_checkout_memory_db_for_a_wal_checkpoint(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """PRD-CORE-298: the daemon owns the checkout's WAL now (C12 finding 2).

        Before the fix, an upgraded project with a leftover ``memory.db-wal``
        made this path open a bare sqlite3 connection to the retired checkout
        db on every session start. Proof: seed a real db + wal pair, make
        ``sqlite3.connect`` raise, run maintenance, and require both files
        byte-identical and ``wal_checkpoint`` absent from the result.
        """
        import sqlite3

        memory_dir = trw_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        db_path = memory_dir / "memory.db"
        wal_path = memory_dir / "memory.db-wal"
        db_bytes = b"sqlite-db-fixture-bytes"
        wal_bytes = b"sqlite-wal-fixture-bytes"
        db_path.write_bytes(db_bytes)
        wal_path.write_bytes(wal_bytes)

        def _boom(*_args: object, **_kwargs: object) -> sqlite3.Connection:
            raise AssertionError("sqlite3.connect must not be called against the checkout db")

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
            patch("sqlite3.connect", side_effect=_boom),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "wal_checkpoint" not in result
        assert db_path.read_bytes() == db_bytes
        assert wal_path.read_bytes() == wal_bytes

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
        ):
            result = run_auto_maintenance(trw_dir, cfg)

        assert result["update_advisory"] == "v2.0 available"
        assert result["auto_upgrade"]["applied"] is True
        assert result["auto_upgrade"]["version"] == "2.0.0"

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


# ---------------------------------------------------------------------------
# PRD-CORE-263-FR04 — every maintenance result is propagated or declared internal
# ---------------------------------------------------------------------------


def _declared_maintenance_keys() -> set[str]:
    from trw_mcp.models.typed_dicts._ceremony import AutoMaintenanceDict

    return set(AutoMaintenanceDict.__annotations__)


def test_every_maintenance_key_is_propagated_or_declared_internal() -> None:
    """PRD-CORE-263-FR04 — totality over the declared result type.

    The propagation allowlist once omitted declared keys (``wal_checkpoint`` and
    ``embeddings_coverage_ratio`` among them) that were computed on the hot path
    of every session and dropped. Attribution: reverting FR04 turns this red
    naming them under ``dropped``.
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
    # PRD-CORE-280 FR01 dropped the writer-pressure deferral keys, FR04 the
    # three embedding-backfill keys, and PRD-CORE-298 FR01 `embeddings_migration`
    # with the in-process re-embed pass, the daemon cut-over the embedder
    # warm-up key, and 6.0.0 the embeddings advisory and coverage ratio, which
    # session start now takes from the daemon-measured pipeline health (5 remain).
    assert len(propagated) >= 5


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
    """PRD-CORE-263-FR04 — the once-dropped keys arrive in the session-start payload."""
    from typing import cast

    from trw_mcp.tools import _ceremony_step_table as table

    sweep = {
        "wal_checkpoint": {"checkpointed": True, "mode": "PASSIVE"},
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
