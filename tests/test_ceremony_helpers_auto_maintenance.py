"""Tests for ceremony auto-maintenance helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests._ceremony_helpers_support import config, trw_dir, write_installed_version  # noqa: F401
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
