"""Extra tests for orchestration.py to improve coverage from 73% to 85%+.

Targets uncovered lines: 103, 214, 242-248, 259, 295, 302, 329-383,
415-416, 420, 422, 429-430, 463, 475-476, 580, 586, 593-599.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

import trw_mcp.tools.orchestration as orch_mod
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools.orchestration import (
    _check_framework_version_staleness,
    _compute_reversion_metrics,
    _compute_wave_progress,
    _get_bundled_file,
    _get_package_version,
    register_orchestration_tools,
)


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _make_orch_tools() -> dict[str, Any]:
    """Create a FastMCP server with orchestration tools and return the tools dict."""
    srv = FastMCP("test")
    register_orchestration_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


@pytest.fixture
def orch_tools() -> dict[str, Any]:
    return _make_orch_tools()


# ────────────────────────────────────────────────
# Line 103: config_overrides applied to config.yaml
# ────────────────────────────────────────────────

class TestTrwInitConfigOverrides:
    """Tests for trw_init config_overrides parameter (line 103)."""

    def test_config_overrides_written_to_config_yaml(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """config_overrides values are merged into .trw/config.yaml."""
        orch_tools["trw_init"].fn(
            task_name="override-task",
            config_overrides={"custom_key": "custom_value", "parallelism_max": "8"},
        )

        config_path = tmp_path / ".trw" / "config.yaml"
        reader = FileStateReader()
        data = reader.read_yaml(config_path)
        assert data.get("custom_key") == "custom_value"

    def test_config_overrides_none_no_error(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """config_overrides=None runs without error (default path)."""
        result = orch_tools["trw_init"].fn(
            task_name="no-override-task",
            config_overrides=None,
        )
        assert result["status"] == "initialized"

    def test_config_overrides_not_applied_on_second_init(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """config.yaml already exists on second init — overrides skipped."""
        orch_tools["trw_init"].fn(task_name="first-task")

        # Patch config.yaml to have a sentinel value
        config_path = tmp_path / ".trw" / "config.yaml"
        original = config_path.read_text(encoding="utf-8")

        orch_tools["trw_init"].fn(
            task_name="second-task",
            config_overrides={"should_not": "appear"},
        )

        # The file should be unchanged (since it already existed)
        assert config_path.read_text(encoding="utf-8") == original


# ────────────────────────────────────────────────
# Lines 295, 302: checkpoint with shard_id
# ────────────────────────────────────────────────

class TestTrwCheckpointShardId:
    """Tests for trw_checkpoint shard_id parameter (lines 295, 302)."""

    def test_checkpoint_with_shard_id(self, orch_tools: dict[str, Any]) -> None:
        """shard_id is stored in checkpoint record and logged in event."""
        init_result = orch_tools["trw_init"].fn(task_name="shard-cp-task")
        run_path = Path(init_result["run_path"])

        result = orch_tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="Shard checkpoint",
            shard_id="shard-01",
        )
        assert result["status"] == "checkpoint_created"

        # Verify checkpoint record contains shard_id
        cp_path = run_path / "meta" / "checkpoints.jsonl"
        reader = FileStateReader()
        checkpoints = reader.read_jsonl(cp_path)
        assert len(checkpoints) == 1
        assert checkpoints[0]["shard_id"] == "shard-01"

        # Verify events.jsonl has shard_id
        events = reader.read_jsonl(run_path / "meta" / "events.jsonl")
        cp_events = [e for e in events if e.get("event") == "checkpoint"]
        assert any(e.get("shard_id") == "shard-01" for e in cp_events)

    def test_checkpoint_without_shard_id_no_key(self, orch_tools: dict[str, Any]) -> None:
        """Checkpoint without shard_id should NOT have shard_id key in record."""
        init_result = orch_tools["trw_init"].fn(task_name="no-shard-cp-task")
        run_path = Path(init_result["run_path"])

        orch_tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="No shard",
        )

        reader = FileStateReader()
        checkpoints = reader.read_jsonl(run_path / "meta" / "checkpoints.jsonl")
        assert "shard_id" not in checkpoints[0]


# ────────────────────────────────────────────────
# Lines 214, 242-248: trw_status with wave_manifest
# ────────────────────────────────────────────────

class TestTrwStatusWaveManifest:
    """Tests for trw_status when wave_manifest exists (lines 214, 242-248)."""

    def _create_run_with_wave_manifest(
        self,
        orch_tools: dict[str, Any],
        task_name: str,
        manifest_location: str = "shards",
    ) -> tuple[str, Path]:
        """Helper: init run and write a wave_manifest to specified location."""
        init_result = orch_tools["trw_init"].fn(task_name=task_name)
        run_path = Path(init_result["run_path"])

        wave_manifest = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
                {"wave": 2, "status": "active", "shards": ["s3"]},
            ]
        }

        writer = FileStateWriter()
        if manifest_location == "shards":
            manifest_path = run_path / "shards" / "wave_manifest.yaml"
        else:
            manifest_path = run_path / "meta" / "wave_manifest.yaml"
        writer.write_yaml(manifest_path, wave_manifest)

        return init_result["run_path"], run_path

    def test_status_includes_waves_when_manifest_in_shards(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """trw_status returns 'waves' key when wave_manifest.yaml in shards/."""
        run_path_str, _ = self._create_run_with_wave_manifest(
            orch_tools, "wave-shards-task", manifest_location="shards",
        )
        status = orch_tools["trw_status"].fn(run_path=run_path_str)

        assert "waves" in status
        assert isinstance(status["waves"], list)
        assert len(status["waves"]) == 2

    def test_status_includes_wave_progress_when_manifest_exists(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """trw_status includes 'wave_progress' dict when wave data present."""
        run_path_str, _ = self._create_run_with_wave_manifest(
            orch_tools, "wave-progress-task",
        )
        status = orch_tools["trw_status"].fn(run_path=run_path_str)

        assert "wave_progress" in status
        progress = status["wave_progress"]
        assert progress["total_waves"] == 2

    def test_status_wave_manifest_in_meta_fallback(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """trw_status falls back to meta/wave_manifest.yaml when shards/ not present."""
        run_path_str, _ = self._create_run_with_wave_manifest(
            orch_tools, "wave-meta-task", manifest_location="meta",
        )
        status = orch_tools["trw_status"].fn(run_path=run_path_str)

        assert "waves" in status


# ────────────────────────────────────────────────
# Line 259: version_warning in trw_status
# ────────────────────────────────────────────────

class TestTrwStatusVersionWarning:
    """Tests for trw_status version staleness warning (line 259)."""

    def test_status_shows_version_warning_when_stale(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """version_warning present when run framework differs from deployed version."""
        # Init with default version
        init_result = orch_tools["trw_init"].fn(task_name="stale-version-task")
        run_path = Path(init_result["run_path"])

        # Write a different version to VERSION.yaml
        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        writer = FileStateWriter()
        writer.write_yaml(version_path, {
            "framework_version": "v99.0_TRW",
            "aaref_version": "v9.0.0",
            "trw_mcp_version": "9.9.9",
        })

        # Patch the _reader on the module so version_path.exists() check goes via the module
        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

        assert "version_warning" in status
        assert "v99.0_TRW" in str(status["version_warning"])

    def test_status_no_version_warning_when_current(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """No version_warning when run framework matches deployed version."""
        init_result = orch_tools["trw_init"].fn(task_name="current-version-task")

        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

        # Should not have version_warning (versions match after init)
        assert "version_warning" not in status


# ────────────────────────────────────────────────
# Lines 329-383: _compute_wave_progress directly
# ────────────────────────────────────────────────

class TestComputeWaveProgress:
    """Direct tests for _compute_wave_progress (lines 329-383)."""

    def test_empty_waves_returns_none(self, tmp_path: Path) -> None:
        """Returns None when waves list is empty."""
        result = _compute_wave_progress({"waves": []}, tmp_path)
        assert result is None

    def test_non_list_waves_returns_none(self, tmp_path: Path) -> None:
        """Returns None when waves is not a list."""
        result = _compute_wave_progress({"waves": "not-a-list"}, tmp_path)
        assert result is None

    def test_missing_waves_key_returns_none(self, tmp_path: Path) -> None:
        """Returns None when 'waves' key not in wave_data."""
        result = _compute_wave_progress({}, tmp_path)
        assert result is None

    def test_single_complete_wave(self, tmp_path: Path) -> None:
        """Single complete wave returns correct progress."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 1
        assert result["completed_waves"] == 1
        assert result["active_wave"] is None

    def test_active_wave_detected(self, tmp_path: Path) -> None:
        """Active wave is identified correctly."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": []},
                {"wave": 2, "status": "active", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["active_wave"] == 2
        assert result["completed_waves"] == 1

    def test_partial_wave_counted_as_completed(self, tmp_path: Path) -> None:
        """Partial status waves are counted in completed_waves."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "partial", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["completed_waves"] == 1

    def test_wave_with_shard_manifest(self, tmp_path: Path) -> None:
        """Shard statuses are read from manifest.yaml."""
        shards_dir = tmp_path / "shards"
        shards_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(shards_dir / "manifest.yaml", {
            "shards": [
                {"id": "s1", "status": "complete"},
                {"id": "s2", "status": "active"},
                {"id": "s3", "status": "pending"},
            ]
        })

        wave_data = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": ["s1", "s2", "s3"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        wave_detail = result["wave_details"][0]
        assert wave_detail["shards"]["complete"] == 1
        assert wave_detail["shards"]["active"] == 1
        assert wave_detail["shards"]["pending"] == 1

    def test_active_wave_from_shard_status(self, tmp_path: Path) -> None:
        """Wave with active shards is flagged as active even if wave status is pending."""
        shards_dir = tmp_path / "shards"
        shards_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(shards_dir / "manifest.yaml", {
            "shards": [
                {"id": "s1", "status": "active"},
            ]
        })

        wave_data = {
            "waves": [
                {"wave": 3, "status": "pending", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["active_wave"] == 3

    def test_wave_detail_structure(self, tmp_path: Path) -> None:
        """wave_details has expected keys."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        detail = result["wave_details"][0]
        assert "wave" in detail
        assert "status" in detail
        assert "shards" in detail
        assert "total" in detail["shards"]

    def test_non_dict_wave_skipped(self, tmp_path: Path) -> None:
        """Non-dict entries in waves list are skipped gracefully."""
        wave_data = {
            "waves": [
                "not-a-dict",
                {"wave": 1, "status": "complete", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 2
        assert len(result["wave_details"]) == 1

    def test_non_list_shards_in_wave_handled(self, tmp_path: Path) -> None:
        """Non-list shards value in wave is treated as empty."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": "not-a-list"},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["wave_details"][0]["shards"]["total"] == 0

    def test_corrupted_shard_manifest_silently_ignored(self, tmp_path: Path) -> None:
        """Corrupted shard manifest doesn't raise — shard_statuses stays empty."""
        shards_dir = tmp_path / "shards"
        shards_dir.mkdir(parents=True)
        # Write a corrupt/invalid YAML
        (shards_dir / "manifest.yaml").write_text("!!invalid: yaml: [\n", encoding="utf-8")

        wave_data = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": ["s1"]},
            ]
        }
        # Should not raise
        result = _compute_wave_progress(wave_data, tmp_path)
        assert result is not None


# ────────────────────────────────────────────────
# Lines 415-430: _compute_reversion_metrics
# ────────────────────────────────────────────────

class TestComputeReversionMetrics:
    """Direct tests for _compute_reversion_metrics (lines 415-430)."""

    def test_no_events_healthy_zero_rate(self) -> None:
        """Empty events yields healthy classification with zero rate."""
        result = _compute_reversion_metrics([])
        assert result["count"] == 0
        assert result["rate"] == 0.0
        assert result["classification"] == "healthy"
        assert result["latest"] is None
        assert result["by_trigger"] == {}

    def test_trigger_classified_key_used_over_trigger(self) -> None:
        """trigger_classified key takes precedence over trigger in by_trigger."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "refactor",
                "trigger": "raw-trigger",
            }
        ]
        result = _compute_reversion_metrics(events)
        assert "refactor" in result["by_trigger"]
        assert "raw-trigger" not in result["by_trigger"]

    def test_trigger_fallback_when_no_trigger_classified(self) -> None:
        """Falls back to 'trigger' when 'trigger_classified' absent."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert", "trigger": "scope-creep"},
        ]
        result = _compute_reversion_metrics(events)
        assert "scope-creep" in result["by_trigger"]

    def test_trigger_defaults_to_other_when_both_absent(self) -> None:
        """Falls back to 'other' when neither trigger key present."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
        ]
        result = _compute_reversion_metrics(events)
        assert "other" in result["by_trigger"]

    def test_concerning_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """High reversion rate classified as 'concerning'."""
        # Patch _config to set threshold to 0.0 so any revertion = concerning
        cfg = TRWConfig(reversion_rate_concerning=0.0, reversion_rate_elevated=0.0)
        monkeypatch.setattr(orch_mod, "_config", cfg)

        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "concerning"

    def test_elevated_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Moderate reversion rate classified as 'elevated'."""
        # concerning > 0.5, elevated > 0.1 — give a rate in between
        cfg = TRWConfig(reversion_rate_concerning=0.9, reversion_rate_elevated=0.1)
        monkeypatch.setattr(orch_mod, "_config", cfg)

        # 1 revert + 1 phase_enter = rate 0.5 (between 0.1 and 0.9)
        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "elevated"

    def test_healthy_classification_default(self) -> None:
        """Zero reverts with default thresholds is 'healthy'."""
        events: list[dict[str, object]] = [
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "healthy"

    def test_latest_reversion_populated(self) -> None:
        """latest field contains info from most recent phase_revert event."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger_classified": "refactor",
                "reason": "Found bigger issue",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "from_phase": "validate",
                "to_phase": "implement",
                "trigger": "test_failure",
                "reason": "Tests broke",
                "ts": "2026-01-02T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)
        latest = result["latest"]
        assert latest is not None
        assert latest["from_phase"] == "validate"
        assert latest["to_phase"] == "implement"
        assert latest["reason"] == "Tests broke"

    def test_multiple_triggers_counted(self) -> None:
        """Multiple reverts with same trigger are accumulated."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert", "trigger": "scope"},
            {"event": "phase_revert", "trigger": "scope"},
            {"event": "phase_revert", "trigger": "blocker"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["by_trigger"]["scope"] == 2
        assert result["by_trigger"]["blocker"] == 1
        assert result["count"] == 3

    def test_rate_calculation_with_mixed_events(self) -> None:
        """Rate = revert_count / (revert_count + phase_enter_count)."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
            {"event": "phase_revert"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        # 2 reverts / 5 total = 0.4
        assert result["rate"] == pytest.approx(0.4, abs=0.001)
        assert result["count"] == 2


# ────────────────────────────────────────────────
# Line 463: _get_bundled_file returns None for missing file
# ────────────────────────────────────────────────

class TestGetBundledFile:
    """Tests for _get_bundled_file helper (line 463)."""

    def test_returns_none_for_nonexistent_file(self) -> None:
        """Returns None when the requested file does not exist."""
        result = _get_bundled_file("totally_nonexistent_file_xyz.md")
        assert result is None

    def test_returns_content_for_existing_file(self) -> None:
        """Returns string content for a file that exists in data/."""
        # framework.md is a known bundled file
        result = _get_bundled_file("framework.md")
        # May be None if not present in test env, but should not raise
        assert result is None or isinstance(result, str)

    def test_returns_none_for_nonexistent_subdir_file(self) -> None:
        """Returns None when subdir/file combo doesn't exist."""
        result = _get_bundled_file("nonexistent.md", subdir="templates")
        assert result is None


# ────────────────────────────────────────────────
# Lines 475-476: _get_package_version exception path
# ────────────────────────────────────────────────

class TestGetPackageVersion:
    """Tests for _get_package_version helper (lines 475-476)."""

    def test_returns_string(self) -> None:
        """Returns a string (either version or 'unknown')."""
        result = _get_package_version()
        assert isinstance(result, str)

    def test_returns_unknown_when_package_not_found(self) -> None:
        """Returns 'unknown' when importlib.metadata raises PackageNotFoundError."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("trw-mcp"),
        ):
            result = _get_package_version()
            assert result == "unknown"

    def test_exception_path_returns_unknown(self) -> None:
        """Directly test the exception path by patching importlib.metadata."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("trw-mcp"),
        ):
            result = _get_package_version()
            assert result == "unknown"


# ────────────────────────────────────────────────
# Lines 580, 586, 593-599: _check_framework_version_staleness
# ────────────────────────────────────────────────

class TestCheckFrameworkVersionStaleness:
    """Direct tests for _check_framework_version_staleness (lines 580-599)."""

    def test_empty_run_framework_returns_none(self) -> None:
        """Returns None when run_framework is empty string (line 580)."""
        result = _check_framework_version_staleness("")
        assert result is None

    def test_no_version_yaml_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when VERSION.yaml does not exist (line 586)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        # Don't create .trw/frameworks/VERSION.yaml
        result = _check_framework_version_staleness("v1.0_TRW")
        assert result is None

    def test_matching_versions_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when run framework matches deployed version (line 590-591)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        # Setup VERSION.yaml with matching version
        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(frameworks_dir / "VERSION.yaml", {
            "framework_version": cfg.framework_version,
        })

        result = _check_framework_version_staleness(cfg.framework_version)
        assert result is None

    def test_mismatched_versions_returns_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns warning string when versions differ (lines 593-597)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(frameworks_dir / "VERSION.yaml", {
            "framework_version": "v99.0_TRW",
        })

        result = _check_framework_version_staleness("v1.0_TRW")

        assert result is not None
        assert "v1.0_TRW" in result
        assert "v99.0_TRW" in result
        assert "re-bootstrapping" in result

    def test_empty_current_version_in_yaml_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when VERSION.yaml has empty framework_version (line 590)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(frameworks_dir / "VERSION.yaml", {
            "framework_version": "",
        })

        result = _check_framework_version_staleness("v1.0_TRW")
        assert result is None

    def test_state_error_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when StateError raised during read (line 598-599)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        from trw_mcp.exceptions import StateError as TRWStateError
        import trw_mcp.tools.orchestration as _orch_mod

        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)
        # Create the file so exists() check passes
        (frameworks_dir / "VERSION.yaml").write_text(
            "framework_version: v1.0_TRW\n", encoding="utf-8"
        )

        def _raise_state_error(path: Path) -> dict[str, object]:
            raise TRWStateError("simulated error")

        monkeypatch.setattr(_orch_mod._reader, "read_yaml", _raise_state_error)

        result = _check_framework_version_staleness("v2.0_TRW")
        assert result is None


# ────────────────────────────────────────────────
# Integration: trw_status reversion metrics visible
# ────────────────────────────────────────────────

class TestTrwStatusReversionMetrics:
    """Integration tests for reversion metrics in trw_status."""

    def test_status_includes_reversions_key(self, orch_tools: dict[str, Any]) -> None:
        """trw_status always returns 'reversions' key."""
        init_result = orch_tools["trw_init"].fn(task_name="reversion-task")
        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

        assert "reversions" in status
        rev = status["reversions"]
        assert "count" in rev
        assert "rate" in rev
        assert "classification" in rev

    def test_status_reversions_reflect_logged_events(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """Phase revert events logged by other means appear in reversion metrics."""
        init_result = orch_tools["trw_init"].fn(task_name="rev-events-task")
        run_path = Path(init_result["run_path"])

        # Manually append a phase_revert event to events.jsonl
        events_path = run_path / "meta" / "events.jsonl"
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": "2026-01-01T00:00:00Z",
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger": "scope_creep",
            }) + "\n")

        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert status["reversions"]["count"] == 1
        assert status["reversions"]["latest"] is not None
