"""Coverage-targeted tests for trw_mcp/tools/orchestration.py.

Covers the uncovered branches identified in line-level analysis:
- Line 103: config_overrides in trw_init
- Line 214: wave_manifest_path alt location fallback
- Lines 242-248: wave_data processing in trw_status
- Line 259: version_warning in trw_status
- Lines 295, 302: shard_id in trw_checkpoint
- Lines 329-383: _compute_wave_progress (entire function)
- Lines 415-416, 420, 422, 429-430: _compute_reversion_metrics edge cases
- Line 463: _get_bundled_file with subdir
- Lines 475-476: _get_package_version fallback
- Line 580: empty run_framework in _check_framework_version_staleness
- Line 586: version_path doesn't exist
- Lines 593-599: version staleness warning when versions differ
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

import trw_mcp.tools.orchestration as orch_mod
from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools.orchestration import (
    _check_framework_version_staleness,
    _compute_reversion_metrics,
    _compute_wave_progress,
    _get_bundled_file,
    register_orchestration_tools,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests in this module."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _make_orch_tools() -> dict[str, Any]:
    """Return a dict of orchestration tools keyed by name."""
    srv = FastMCP("test")
    register_orchestration_tools(srv)
    return get_tools_sync(srv)


@pytest.fixture
def orch_tools() -> dict[str, Any]:
    """Orchestration tools dict for test use."""
    return _make_orch_tools()


# ===========================================================================
# 1. trw_init — config_overrides branch (line 103)
# ===========================================================================


class TestTrwInitConfigOverrides:
    """Line 103: config_overrides dict merged into config.yaml."""

    def test_config_overrides_written_to_config_yaml(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """When config_overrides is provided, values appear in .trw/config.yaml."""
        result = orch_tools["trw_init"].fn(
            task_name="override-task",
            config_overrides={"custom_key": "custom_value", "parallelism_max": "8"},
        )

        assert result["status"] == "initialized"
        reader = FileStateReader()
        config_path = tmp_path / ".trw" / "config.yaml"
        assert config_path.exists()
        data = reader.read_yaml(config_path)
        assert data.get("custom_key") == "custom_value"
        assert str(data.get("parallelism_max")) == "8"

    def test_config_overrides_none_still_writes_defaults(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """config_overrides=None (default) still writes the default config keys."""
        orch_tools["trw_init"].fn(task_name="no-override-task")

        reader = FileStateReader()
        data = reader.read_yaml(tmp_path / ".trw" / "config.yaml")
        assert "framework_version" in data

    def test_second_init_does_not_overwrite_config(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """Second trw_init call does NOT overwrite existing config.yaml (idempotent)."""
        orch_tools["trw_init"].fn(
            task_name="first-task",
            config_overrides={"sentinel_key": "original"},
        )

        # Second init with different overrides — existing file must be preserved
        orch_tools["trw_init"].fn(
            task_name="second-task",
            config_overrides={"sentinel_key": "should_not_appear"},
        )

        reader = FileStateReader()
        data = reader.read_yaml(tmp_path / ".trw" / "config.yaml")
        assert data.get("sentinel_key") == "original"


# ===========================================================================
# 2. trw_status — wave data processing (lines 214, 242-248)
# ===========================================================================


class TestTrwStatusWaveData:
    """Lines 214, 242-248: wave_manifest.yaml reading in trw_status."""

    def test_wave_manifest_in_meta_dir_fallback(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """wave_manifest.yaml under meta/ is used when shards/ location is absent (line 214)."""
        # Create run via trw_init so auto-detection works
        init_result = orch_tools["trw_init"].fn(task_name="wave-meta-task")
        run_path = Path(init_result["run_path"])

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1"]},
            ],
        }
        writer = FileStateWriter()
        # Put wave_manifest only under meta/ (not under shards/)
        writer.write_yaml(run_path / "meta" / "wave_manifest.yaml", wave_data)

        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert "waves" in status
        waves = status["waves"]
        assert isinstance(waves, list)
        assert len(waves) == 1

    def test_wave_manifest_in_shards_dir_primary(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """wave_manifest.yaml under shards/ is preferred over meta/ (primary path)."""
        init_result = orch_tools["trw_init"].fn(task_name="wave-shards-task")
        run_path = Path(init_result["run_path"])

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["sA", "sB"]},
                {"wave": 2, "status": "pending", "shards": []},
            ],
        }
        writer = FileStateWriter()
        writer.write_yaml(run_path / "shards" / "wave_manifest.yaml", wave_data)

        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert "waves" in status
        waves = status["waves"]
        assert isinstance(waves, list)
        assert len(waves) == 2

    def test_wave_progress_computed_when_waves_present(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """wave_progress key appears in status when wave data is present (lines 244-248)."""
        init_result = orch_tools["trw_init"].fn(task_name="wave-progress-task")
        run_path = Path(init_result["run_path"])

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
                {"wave": 2, "status": "active", "shards": ["s3"]},
            ],
        }
        writer = FileStateWriter()
        writer.write_yaml(run_path / "shards" / "wave_manifest.yaml", wave_data)

        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert "wave_progress" in status
        wp = status["wave_progress"]
        assert isinstance(wp, dict)
        assert wp["total_waves"] == 2
        assert wp["completed_waves"] == 1

    def test_no_wave_manifest_means_no_wave_keys(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """When no wave_manifest.yaml exists, status has no 'waves' or 'wave_progress' keys."""
        init_result = orch_tools["trw_init"].fn(task_name="no-wave-status-task")
        run_path = init_result["run_path"]

        status = orch_tools["trw_status"].fn(run_path=run_path)

        assert "waves" not in status
        assert "wave_progress" not in status


# ===========================================================================
# 3. trw_status — version_warning (line 259)
# ===========================================================================


class TestTrwStatusVersionWarning:
    """Line 259: version_warning in status result when framework is stale."""

    def test_version_warning_when_run_uses_older_framework(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When run framework differs from deployed version, version_warning appears."""
        # Run trw_init to deploy the framework at current version
        init_result = orch_tools["trw_init"].fn(task_name="stale-version-task")
        run_path = Path(init_result["run_path"])

        # Now patch the config so the deployed VERSION.yaml shows a newer version
        new_config = TRWConfig(framework_version="v99.0_TRW")
        monkeypatch.setattr("trw_mcp.tools.orchestration.get_config", lambda: new_config)
        monkeypatch.setattr("trw_mcp.tools._orchestration_helpers.get_config", lambda: new_config)

        # Re-deploy frameworks at the patched version
        orch_mod._deploy_frameworks(tmp_path / ".trw")

        # The run.yaml still has the old version (v24.0_TRW) from init
        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert "version_warning" in status
        warning = str(status["version_warning"])
        assert "v99.0_TRW" in warning


# ===========================================================================
# 4. trw_checkpoint — shard_id parameter (lines 295, 302)
# ===========================================================================


class TestTrwCheckpointShardId:
    """Lines 295, 302: shard_id included in checkpoint and event records."""

    def test_shard_id_in_checkpoint_record(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """shard_id appears in checkpoint JSONL record when provided."""
        init_result = orch_tools["trw_init"].fn(task_name="shard-cp-task")
        run_path = Path(init_result["run_path"])

        result = orch_tools["trw_checkpoint"].fn(
            run_path=str(run_path),
            message="Shard work done",
            shard_id="shard-1",
        )

        assert result["status"] == "checkpoint_created"

        reader = FileStateReader()
        checkpoints = reader.read_jsonl(run_path / "meta" / "checkpoints.jsonl")
        assert len(checkpoints) >= 1
        last = checkpoints[-1]
        assert last.get("shard_id") == "shard-1"

    def test_shard_id_in_event_record(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """shard_id appears in events.jsonl checkpoint event when provided (line 302)."""
        init_result = orch_tools["trw_init"].fn(task_name="shard-event-task")
        run_path = Path(init_result["run_path"])

        orch_tools["trw_checkpoint"].fn(
            run_path=str(run_path),
            message="Shard checkpoint",
            shard_id="shard-2",
        )

        reader = FileStateReader()
        events = reader.read_jsonl(run_path / "meta" / "events.jsonl")
        cp_events = [e for e in events if e.get("event") == "checkpoint"]
        assert len(cp_events) >= 1
        last_cp = cp_events[-1]
        assert last_cp.get("shard_id") == "shard-2"

    def test_no_shard_id_omits_key(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """When shard_id is not given, key is absent from both checkpoint and event."""
        init_result = orch_tools["trw_init"].fn(task_name="no-shard-task")
        run_path = Path(init_result["run_path"])

        orch_tools["trw_checkpoint"].fn(
            run_path=str(run_path),
            message="No shard",
        )

        reader = FileStateReader()
        checkpoints = reader.read_jsonl(run_path / "meta" / "checkpoints.jsonl")
        last = checkpoints[-1]
        assert "shard_id" not in last


# ===========================================================================
# 5. _compute_wave_progress — all branches (lines 329-383)
# ===========================================================================


class TestComputeWaveProgress:
    """Lines 329-383: _compute_wave_progress private function, fully uncovered."""

    # --- Guard branches ---

    def test_returns_none_for_empty_waves_list(self, tmp_path: Path) -> None:
        """Empty waves list returns None."""
        result = _compute_wave_progress({"waves": []}, tmp_path)
        assert result is None

    def test_returns_none_for_non_list_waves(self, tmp_path: Path) -> None:
        """Non-list waves value returns None."""
        result = _compute_wave_progress({"waves": "not-a-list"}, tmp_path)
        assert result is None

    def test_returns_none_when_waves_key_absent(self, tmp_path: Path) -> None:
        """Missing 'waves' key returns None (empty list default)."""
        result = _compute_wave_progress({}, tmp_path)
        assert result is None

    # --- Single complete wave ---

    def test_single_complete_wave(self, tmp_path: Path) -> None:
        """Single wave with status=complete increments completed_waves."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 1
        assert result["completed_waves"] == 1
        assert result["active_wave"] is None

    def test_single_partial_wave_counts_as_completed(self, tmp_path: Path) -> None:
        """Wave with status=partial also increments completed_waves."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "partial", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["completed_waves"] == 1

    # --- Active wave detection ---

    def test_active_wave_by_status(self, tmp_path: Path) -> None:
        """Wave with status=active sets active_wave."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": []},
                {"wave": 2, "status": "active", "shards": ["s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["active_wave"] == 2

    def test_active_wave_by_shard_active_count(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Wave with pending status but active shards also sets active_wave."""
        run_path = tmp_path / "run"
        shards_dir = run_path / "shards"
        shards_dir.mkdir(parents=True)

        # Write shard manifest with an active shard
        writer.write_yaml(
            shards_dir / "manifest.yaml",
            {
                "shards": [
                    {"id": "s1", "status": "active"},
                ]
            },
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        assert result["active_wave"] == 1

    # --- Shard manifest reading ---

    def test_shard_statuses_read_from_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Shard statuses from manifest.yaml are counted in wave details."""
        run_path = tmp_path / "run"
        shards_dir = run_path / "shards"
        shards_dir.mkdir(parents=True)

        writer.write_yaml(
            shards_dir / "manifest.yaml",
            {
                "shards": [
                    {"id": "s1", "status": "complete"},
                    {"id": "s2", "status": "complete"},
                    {"id": "s3", "status": "failed"},
                ]
            },
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1", "s2", "s3"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 1
        shard_counts = details[0]["shards"]
        assert isinstance(shard_counts, dict)
        assert shard_counts["complete"] == 2
        assert shard_counts["failed"] == 1

    def test_shard_manifest_missing_is_handled_gracefully(
        self,
        tmp_path: Path,
    ) -> None:
        """When shards/manifest.yaml does not exist, shard_statuses is empty (no error)."""
        run_path = tmp_path / "run"
        run_path.mkdir()

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1", "s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        # All shards default to pending since manifest is absent
        details = result["wave_details"]
        assert isinstance(details, list)
        shard_counts = details[0]["shards"]
        assert isinstance(shard_counts, dict)
        assert shard_counts["pending"] == 2

    # --- Multi-wave mixed states ---

    def test_multiple_waves_mixed_states(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Multiple waves: complete + active + pending all counted correctly."""
        run_path = tmp_path / "run"
        (run_path / "shards").mkdir(parents=True)

        writer.write_yaml(
            run_path / "shards" / "manifest.yaml",
            {
                "shards": [
                    {"id": "s1", "status": "complete"},
                    {"id": "s2", "status": "complete"},
                    {"id": "s3", "status": "active"},
                ]
            },
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
                {"wave": 2, "status": "active", "shards": ["s3"]},
                {"wave": 3, "status": "pending", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        assert result["total_waves"] == 3
        assert result["completed_waves"] == 1
        assert result["active_wave"] == 2
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 3

    def test_non_dict_wave_entries_skipped(self, tmp_path: Path) -> None:
        """Non-dict items in waves list are skipped without error."""
        wave_data: dict[str, object] = {
            "waves": [
                "not-a-dict",
                42,
                {"wave": 1, "status": "complete", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        # Only the dict entry is processed; non-dicts are skipped
        assert result is not None
        assert result["total_waves"] == 3  # raw list length
        assert result["completed_waves"] == 1

    def test_wave_details_structure(self, tmp_path: Path) -> None:
        """Wave details contain wave number, status, and shard counts dict."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 3, "status": "pending", "shards": ["s1", "s2", "s3"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 1
        entry = details[0]
        assert entry["wave"] == 3
        assert entry["status"] == "pending"
        shard_counts = entry["shards"]
        assert isinstance(shard_counts, dict)
        assert "total" in shard_counts
        assert shard_counts["total"] == 3

    def test_non_list_shards_treated_as_empty(self, tmp_path: Path) -> None:
        """When shards field is not a list, it is treated as empty."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": "not-a-list"},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        details = result["wave_details"]
        assert isinstance(details, list)
        shard_counts = details[0]["shards"]
        assert isinstance(shard_counts, dict)
        assert shard_counts["total"] == 0

    def test_shard_manifest_with_corrupt_data_handled_gracefully(
        self,
        tmp_path: Path,
    ) -> None:
        """Corrupt shards manifest (non-list shards key) is handled without error."""
        run_path = tmp_path / "run"
        (run_path / "shards").mkdir(parents=True)

        # Write malformed manifest where shards is not a list
        run_path.joinpath("shards", "manifest.yaml").write_text(
            "shards: not-a-list\n",
            encoding="utf-8",
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        # Should still return a result (shard_statuses stays empty)
        assert result is not None
        assert result["total_waves"] == 1
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 1
        # Shard status defaults to pending when manifest is corrupt
        assert details[0]["shards"]["pending"] == 1

    def test_shard_manifest_read_error_is_caught(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """StateError from read_yaml for shard manifest is caught (lines 344-345)."""
        from trw_mcp.exceptions import StateError as TRWStateError

        run_path = tmp_path / "run"
        (run_path / "shards").mkdir(parents=True)

        # Create the manifest file so .exists() returns True
        (run_path / "shards" / "manifest.yaml").write_text(
            "shards: []\n",
            encoding="utf-8",
        )

        # Patch _reader.read_yaml to raise StateError when reading the manifest
        original_read = orch_mod._reader.read_yaml

        def exploding_read(path: Path) -> dict[str, object]:
            if "manifest" in str(path):
                raise TRWStateError("simulated shard manifest read failure")
            return dict(original_read(path))

        monkeypatch.setattr(orch_mod._reader, "read_yaml", exploding_read)

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1"]},
            ]
        }
        # Should not raise; exception is swallowed
        result = _compute_wave_progress(wave_data, run_path)
        assert result is not None
        assert result["total_waves"] == 1
        # Shard status defaults to pending when manifest read fails
        details = result["wave_details"]
        assert isinstance(details, list)
        assert details[0]["shards"]["pending"] == 1


# ===========================================================================
# 6. _compute_reversion_metrics — edge cases (lines 415-416, 420, 422, 429-430)
# ===========================================================================


class TestComputeReversionMetrics:
    """Lines 415-416, 420, 422, 429-430: reversion classification and latest fields."""

    def test_empty_events_returns_healthy(self) -> None:
        """No events → rate=0 → classification=healthy, latest=None."""
        result = _compute_reversion_metrics([])

        assert result["count"] == 0
        assert result["rate"] == 0.0
        assert result["classification"] == "healthy"
        assert result["latest"] is None
        assert result["by_trigger"] == {}

    def test_healthy_classification_below_elevated(self) -> None:
        """Rate below elevated threshold gives classification=healthy."""
        # 1 revert, 9 phase_enter → rate=0.1 → below elevated (0.15)
        revert_event: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "blocker",
            "from_phase": "implement",
            "to_phase": "plan",
            "reason": "found bug",
            "ts": "2026-01-01T00:00:00Z",
        }
        enter_event: dict[str, object] = {"event": "phase_enter"}
        events: list[dict[str, object]] = [revert_event] + [enter_event for _ in range(9)]
        result = _compute_reversion_metrics(events)

        assert result["classification"] == "healthy"
        assert result["count"] == 1

    def test_elevated_classification_between_thresholds(self) -> None:
        """Rate between elevated (0.15) and concerning (0.30) → classification=elevated."""
        # 2 reverts, 10 phase_enter → rate = 2/12 ≈ 0.1667 → elevated
        revert1: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "scope_change",
            "from_phase": "implement",
            "to_phase": "plan",
            "reason": "r1",
            "ts": "2026-01-01T00:00:00Z",
        }
        revert2: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "blocker",
            "from_phase": "validate",
            "to_phase": "implement",
            "reason": "r2",
            "ts": "2026-01-02T00:00:00Z",
        }
        enter_event: dict[str, object] = {"event": "phase_enter"}
        events: list[dict[str, object]] = [revert1, revert2] + [enter_event for _ in range(10)]
        result = _compute_reversion_metrics(events)

        assert result["classification"] == "elevated"
        assert result["count"] == 2

    def test_concerning_classification_above_threshold(self) -> None:
        """Rate >= concerning threshold (0.30) → classification=concerning (line 420)."""
        # 4 reverts, 8 phase_enter → rate = 4/12 ≈ 0.333 → concerning
        revert: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "scope_change",
            "from_phase": "implement",
            "to_phase": "plan",
            "reason": "oops",
            "ts": "2026-01-01T00:00:00Z",
        }
        enter: dict[str, object] = {"event": "phase_enter"}
        events: list[dict[str, object]] = [dict(revert)] * 4 + [dict(enter)] * 8
        result = _compute_reversion_metrics(events)

        assert result["classification"] == "concerning"

    def test_by_trigger_grouping(self) -> None:
        """trigger_classified values are counted per-trigger (lines 415-416)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "scope_change",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "trigger_classified": "scope_change",
                "from_phase": "validate",
                "to_phase": "implement",
                "reason": "r",
                "ts": "2026-01-02T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "trigger_classified": "blocker",
                "from_phase": "plan",
                "to_phase": "research",
                "reason": "r",
                "ts": "2026-01-03T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        by_trigger = result["by_trigger"]
        assert isinstance(by_trigger, dict)
        assert by_trigger.get("scope_change") == 2
        assert by_trigger.get("blocker") == 1

    def test_by_trigger_falls_back_to_trigger_key(self) -> None:
        """When trigger_classified is absent, 'trigger' key is used (line 415)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger": "manual",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        by_trigger = result["by_trigger"]
        assert isinstance(by_trigger, dict)
        assert by_trigger.get("manual") == 1

    def test_by_trigger_defaults_to_other(self) -> None:
        """When neither trigger_classified nor trigger exists, key is 'other'."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        by_trigger = result["by_trigger"]
        assert isinstance(by_trigger, dict)
        assert by_trigger.get("other") == 1

    def test_latest_populated_from_last_revert_event(self) -> None:
        """latest dict is populated from the last phase_revert event (lines 429-430)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "first_trigger",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "reason-A",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "trigger_classified": "second_trigger",
                "from_phase": "validate",
                "to_phase": "implement",
                "reason": "reason-B",
                "ts": "2026-01-02T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        latest = result["latest"]
        assert latest is not None
        assert isinstance(latest, dict)
        assert latest["from_phase"] == "validate"
        assert latest["to_phase"] == "implement"
        assert latest["trigger"] == "second_trigger"
        assert latest["reason"] == "reason-B"

    def test_latest_trigger_falls_back_to_trigger_key(self) -> None:
        """latest['trigger'] uses 'trigger' key when trigger_classified absent (line 433)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger": "manual_fallback",
                "from_phase": "plan",
                "to_phase": "research",
                "reason": "changed mind",
                "ts": "2026-01-01T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        latest = result["latest"]
        assert latest is not None
        assert isinstance(latest, dict)
        assert latest["trigger"] == "manual_fallback"

    def test_rate_computed_correctly(self) -> None:
        """Rate is revert_count / (revert_count + phase_enter_count)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "x",
                "from_phase": "a",
                "to_phase": "b",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
            {"event": "phase_enter"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)

        assert result["rate"] == round(1 / 4, 4)


# ===========================================================================
# 7. _get_bundled_file — with subdir (line 463)
# ===========================================================================


class TestGetBundledFile:
    """Line 463: _get_bundled_file with subdir parameter."""

    def test_get_bundled_file_with_subdir_templates(self) -> None:
        """_get_bundled_file('claude_md.md', subdir='templates') returns non-empty string."""
        content = _get_bundled_file("claude_md.md", subdir="templates")

        assert content is not None
        assert isinstance(content, str)
        assert len(content) > 10

    def test_get_bundled_file_without_subdir(self) -> None:
        """_get_bundled_file('framework.md') (no subdir) returns content."""
        content = _get_bundled_file("framework.md")

        assert content is not None
        assert isinstance(content, str)
        assert len(content) > 10

    def test_get_bundled_file_nonexistent_returns_none(self) -> None:
        """_get_bundled_file for a file that doesn't exist returns None."""
        result = _get_bundled_file("does_not_exist.xyz")
        assert result is None

    def test_get_bundled_file_nonexistent_subdir_returns_none(self) -> None:
        """_get_bundled_file with nonexistent subdir returns None."""
        result = _get_bundled_file("framework.md", subdir="nonexistent_subdir")
        assert result is None


# ===========================================================================
# 8. _get_package_version — fallback (lines 475-476)
# ===========================================================================


class TestGetPackageVersion:
    """Lines 475-476: _get_package_version fallback when package not found."""

    def test_returns_string(self) -> None:
        """_get_package_version always returns a string."""
        from trw_mcp.tools._orchestration_helpers import _get_package_version

        result = _get_package_version()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_unknown_when_package_not_installed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When importlib.metadata raises an exception, returns 'unknown' (lines 475-476)."""
        import importlib.metadata as im

        from trw_mcp.tools._orchestration_helpers import _get_package_version

        def broken_version(distribution_name: str) -> str:
            raise Exception("simulated failure")

        monkeypatch.setattr(im, "version", broken_version)
        result = _get_package_version()
        assert result == "unknown"


# ===========================================================================
# 9. _check_framework_version_staleness — all branches (lines 580-599)
# ===========================================================================


class TestCheckFrameworkVersionStaleness:
    """Lines 580-599: _check_framework_version_staleness all branches."""

    def test_empty_string_returns_none(self, tmp_path: Path) -> None:
        """Empty run_framework string returns None immediately (line 580)."""
        result = _check_framework_version_staleness("")
        assert result is None

    def test_version_file_does_not_exist_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When VERSION.yaml does not exist, returns None (line 586)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        # Do NOT create .trw/frameworks/VERSION.yaml
        result = _check_framework_version_staleness("v18.0_TRW")
        assert result is None

    def test_matching_versions_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When run version matches deployed version, returns None (line 591)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        # Create VERSION.yaml with a known version
        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {
                "framework_version": "v24.0_TRW",
            },
        )

        result = _check_framework_version_staleness("v24.0_TRW")
        assert result is None

    def test_stale_version_returns_warning_string(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When versions differ, returns a warning message string (lines 593-599)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {
                "framework_version": "v24.0_TRW",
            },
        )

        result = _check_framework_version_staleness("v18.0_TRW")

        assert result is not None
        assert isinstance(result, str)
        assert "v18.0_TRW" in result
        assert "v24.0_TRW" in result

    def test_empty_current_version_in_file_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When VERSION.yaml has empty framework_version, returns None (line 590)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {
                "framework_version": "",
            },
        )

        result = _check_framework_version_staleness("v18.0_TRW")
        assert result is None

    def test_exception_during_read_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """StateError during version file read is caught and returns None (line 598)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        # Create frameworks dir but write an invalid YAML file that causes read error
        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        # Write a file that exists but causes a read error via patching reader
        (frameworks_dir / "VERSION.yaml").write_text(
            "framework_version: v99.0_TRW\n",
            encoding="utf-8",
        )

        from unittest.mock import patch

        from trw_mcp.exceptions import StateError as TRWStateError

        def exploding_read(path: Path) -> dict[str, object]:
            raise TRWStateError("simulated read failure")

        with patch.object(FileStateReader, "read_yaml", side_effect=exploding_read):
            result = _check_framework_version_staleness("v18.0_TRW")
        assert result is None
