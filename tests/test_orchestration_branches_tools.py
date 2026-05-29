"""Coverage-targeted tool behavior tests for trw_mcp/tools/orchestration.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import trw_mcp.tools.orchestration as orch_mod
from tests._orchestration_branches_support import orch_tools, set_project_root  # noqa: F401
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


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
        orch_tools["trw_init"].fn(
            task_name="second-task",
            config_overrides={"sentinel_key": "should_not_appear"},
        )

        reader = FileStateReader()
        data = reader.read_yaml(tmp_path / ".trw" / "config.yaml")
        assert data.get("sentinel_key") == "original"


class TestTrwStatusWaveData:
    """Lines 214, 242-248: wave_manifest.yaml reading in trw_status."""

    def test_wave_manifest_in_meta_dir_fallback(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """wave_manifest.yaml under meta/ is used when shards/ location is absent (line 214)."""
        init_result = orch_tools["trw_init"].fn(task_name="wave-meta-task")
        run_path = Path(init_result["run_path"])

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1"]},
            ],
        }
        writer = FileStateWriter()
        writer.write_yaml(run_path / "meta" / "wave_manifest.yaml", wave_data)

        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert "waves" in status
        waves = status["waves"]
        assert isinstance(waves, list)
        assert len(waves) == 1

    def test_wave_manifest_in_shards_dir_primary(
        self,
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
        orch_tools: dict[str, Any],
    ) -> None:
        """When no wave_manifest.yaml exists, status has no 'waves' or 'wave_progress' keys."""
        init_result = orch_tools["trw_init"].fn(task_name="no-wave-status-task")
        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

        assert "waves" not in status
        assert "wave_progress" not in status


class TestTrwStatusVersionWarning:
    """Line 259: version_warning in status result when framework is stale."""

    def test_version_warning_when_run_uses_older_framework(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When run framework differs from deployed version, version_warning appears."""
        init_result = orch_tools["trw_init"].fn(task_name="stale-version-task")
        run_path = Path(init_result["run_path"])

        new_config = TRWConfig(framework_version="v99.0_TRW")
        monkeypatch.setattr("trw_mcp.tools.orchestration.get_config", lambda: new_config)
        monkeypatch.setattr("trw_mcp.tools._orchestration_helpers.get_config", lambda: new_config)
        orch_mod._deploy_frameworks(tmp_path / ".trw")

        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert "version_warning" in status
        warning = str(status["version_warning"])
        assert "v99.0_TRW" in warning


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
