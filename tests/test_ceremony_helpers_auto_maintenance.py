"""Tests for ceremony auto-maintenance helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests._ceremony_helpers_support import write_installed_version
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

    def test_embeddings_backfill_deferred_on_session_start_hot_path(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Session startup never performs a bulk synchronous embedding backfill."""
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
        assert result["embeddings_backfill_deferred"]["reason"] == "session_start_hot_path"

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
        # Healthy path still defers the synchronous bulk backfill off the hot path.
        assert result["embeddings_backfill_deferred"]["reason"] == "session_start_hot_path"
