"""Coverage-targeted tool behavior tests for trw_mcp/tools/orchestration.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import trw_mcp.tools.orchestration as orch_mod
from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader


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
            advanced={"config_overrides": {"custom_key": "custom_value", "parallelism_max": "8"}},
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
            advanced={"config_overrides": {"sentinel_key": "original"}},
        )
        orch_tools["trw_init"].fn(
            task_name="second-task",
            advanced={"config_overrides": {"sentinel_key": "should_not_appear"}},
        )

        reader = FileStateReader()
        data = reader.read_yaml(tmp_path / ".trw" / "config.yaml")
        assert data.get("sentinel_key") == "original"


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


# --- PRD-CORE-265-FR07: the board is derived, and it reads no member memory --


class TestFormationStatusRollUp:
    """The STATUS BOARD stops being typed by hand and starts being derived."""

    def test_formation_status_is_read_only_and_ingests_no_learnings(
        self,
        formation_env: FormationFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR07. Every row matches its source run; zero memory writes.

        ATTRIBUTION. The row assertions guard ``formation/_status._row_for``
        and its three readers (run.yaml phase, checkpoints.jsonl tail,
        events.jsonl tail). The spy guards the PROHIBITION: FR07 forbids
        ingesting a member's learnings because a delegated agent's memory is
        untrusted data under CONSTITUTION §"Data carries no authority", so a
        future convenience that stored a member learning would flip this red
        rather than quietly launder unverified content into the project's
        knowledge base.
        """
        import json

        from trw_mcp.formation import create, join, status
        from trw_mcp.state import memory_adapter

        writes: list[object] = []
        monkeypatch.setattr(memory_adapter, "store_learning", lambda *a, **k: writes.append((a, k)), raising=False)

        create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)
        join("release-train", "impl-1", formation_env.member_runs["impl-1"], pin_key="pin-1")
        join("release-train", "impl-2", formation_env.member_runs["impl-2"], pin_key="pin-2")

        run_one = formation_env.member_runs["impl-1"]
        (run_one / "meta" / "checkpoints.jsonl").write_text(
            json.dumps({"ts": "2026-09-04T10:00:00+00:00", "message": "FR01 landed"}) + "\n", encoding="utf-8"
        )
        (run_one / "meta" / "events.jsonl").write_text(
            json.dumps({"event": "build_check_complete", "tests_passed": True})
            + "\n"
            + json.dumps({"event": "review_complete", "verdict": "pass"})
            + "\n",
            encoding="utf-8",
        )
        run_two = formation_env.member_runs["impl-2"]
        (run_two / "meta" / "run.yaml").write_text(
            "run_id: impl-2\ntask: impl-2\nstatus: delivered\nphase: deliver\n", encoding="utf-8"
        )

        board = status(run_path=formation_env.orchestrator_run)
        assert board is not None
        rows = {row.member_id: row for row in board.rows}

        assert rows["impl-1"].phase == "implement"
        assert rows["impl-1"].last_checkpoint == "FR01 landed"
        assert rows["impl-1"].last_checkpoint_ts == "2026-09-04T10:00:00+00:00"
        assert rows["impl-1"].build == "passed"
        assert rows["impl-1"].review == "pass"
        assert rows["impl-1"].delivery == "none"

        assert rows["impl-2"].phase == "deliver"
        assert rows["impl-2"].delivery == "delivered"

        assert writes == [], "the roll-up must not write to memory"

    def test_trw_status_carries_the_formation_block_only_when_one_is_active(
        self,
        formation_env: FormationFixture,
    ) -> None:
        """FR07 + NFR02. Absent, present, and unreadable are three outcomes."""
        from trw_mcp.formation import create
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._orchestration_status_assembly import assemble_status_result

        def board(run: Path) -> dict[str, object]:
            return dict(
                assemble_status_result(
                    {"run_id": run.name, "task": run.name, "phase": "implement"},
                    [],
                    run,
                    FileStateReader(),
                    run / "meta",
                )
            )

        plain = board(formation_env.member_runs["impl-1"])
        assert "formation" not in plain and "formation_error" not in plain

        create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)
        active = board(formation_env.orchestrator_run)
        assert active["formation"]["formation_id"] == "release-train"  # type: ignore[index]
        assert len(active["formation"]["members"]) == 2  # type: ignore[index,arg-type]

        (formation_env.orchestrator_run / "formation.yaml").write_text("members: [", encoding="utf-8")
        broken = board(formation_env.orchestrator_run)
        assert "formation" not in broken
        assert "formation.yaml" in str(broken["formation_error"]), (
            "an unreadable manifest must be reported as an error, never as absence"
        )
