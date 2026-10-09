from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401


class TestTrwInitTaskNameLengthCap:
    """PRD-QUAL-042-FR01: task_name is a filesystem path component and must be
    length-capped so an over-long name cannot exceed NAME_MAX / fail mkdir."""

    def test_overlong_task_name_rejected(self, orch_tools: dict[str, Any]) -> None:
        from trw_mcp.exceptions import StateError
        from trw_mcp.tools import orchestration as orch_mod

        too_long = "a" * (orch_mod._MAX_TASK_NAME_CHARS + 1)
        with pytest.raises(StateError, match=r"exceeds \d+ chars"):
            orch_tools["trw_init"].fn(task_name=too_long)

    def test_max_length_task_name_accepted(self, orch_tools: dict[str, Any]) -> None:
        from trw_mcp.tools import orchestration as orch_mod

        # Exactly at the cap is allowed (boundary).
        at_cap = "a" * orch_mod._MAX_TASK_NAME_CHARS
        result = orch_tools["trw_init"].fn(task_name=at_cap)
        assert result.get("status") != "error"
        assert result.get("run_id")


class TestTrwInitConfigOverrides:
    """Tests for trw_init config_overrides parameter (line 103)."""

    def test_config_overrides_written_to_config_yaml(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """config_overrides values are merged into .trw/config.yaml."""
        orch_tools["trw_init"].fn(
            task_name="override-task",
            advanced={"config_overrides": {"custom_key": "custom_value", "parallelism_max": "8"}},
        )

        config_path = tmp_path / ".trw" / "config.yaml"
        reader = FileStateReader()
        data = reader.read_yaml(config_path)
        assert data.get("custom_key") == "custom_value"

    def test_config_overrides_none_no_error(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """config_overrides=None runs without error (default path)."""
        result = orch_tools["trw_init"].fn(
            task_name="no-override-task",
            advanced=None,
        )
        assert result["status"] == "initialized"

    def test_config_overrides_not_applied_on_second_init(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """config.yaml already exists on second init — overrides skipped."""
        orch_tools["trw_init"].fn(task_name="first-task")
        config_path = tmp_path / ".trw" / "config.yaml"
        original = config_path.read_text(encoding="utf-8")

        orch_tools["trw_init"].fn(
            task_name="second-task",
            advanced={"config_overrides": {"should_not": "appear"}},
        )

        assert config_path.read_text(encoding="utf-8") == original


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

        cp_path = run_path / "meta" / "checkpoints.jsonl"
        reader = FileStateReader()
        checkpoints = reader.read_jsonl(cp_path)
        assert len(checkpoints) == 1
        assert checkpoints[0]["shard_id"] == "shard-01"

        events = reader.read_jsonl(run_path / "meta" / "events.jsonl")
        cp_events = [event for event in events if event.get("event") == "checkpoint"]
        assert any(event.get("shard_id") == "shard-01" for event in cp_events)

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


class TestTrwInitReviewMandateAdvisory:
    """PRD-CORE-201 FR01/FR02: trw_init surfaces an UP-FRONT REVIEW-mandatory
    signal for STANDARD/COMPREHENSIVE runs, reconciling a possibly-misleading
    'Skip: REVIEW' SessionStart banner. Advisory only — does NOT touch the
    CORE-192 deliver gate."""

    def test_init_review_required_comprehensive(self, orch_tools: dict[str, Any]) -> None:
        """HARD complexity_hint -> COMPREHENSIVE -> review_required + actionable advisory.

        This asserted the rationale clause "overrides the session ceremony
        tier" until 2026-07-27. That clause explained WHY the flag was set and
        changed nothing the caller does, so it was moved to a source comment
        and the advisory cut from three sentences to one. What the advisory
        must still carry is the ACTION — call trw_review before trw_deliver —
        which is what these assertions now pin.
        """
        result = orch_tools["trw_init"].fn(
            task_name="hard-review-task",
            complexity_hint="HARD",
        )
        assert result["review_required"] == "true"
        advisory = result["review_mandate_advisory"].lower()
        assert "review" in advisory
        assert "mandatory" in advisory
        assert "trw_review" in advisory
        assert "trw_deliver" in advisory

    def test_advisory_does_not_claim_delivery_blocks_under_the_default_gate(self, orch_tools: dict[str, Any]) -> None:
        """review_gate_mode defaults to "warn" — a blocking claim would be false.

        The 2026-07-27 compaction of this advisory introduced the clause "or
        delivery blocks", which is false for every caller running the shipped
        default: a missing review emits a soft review_warning and delivery
        proceeds. The substring assertions in the tests above all passed with
        that clause present, so this pins the consequence explicitly.
        """
        result = orch_tools["trw_init"].fn(
            task_name="default-gate-task",
            complexity_hint="HARD",
        )
        advisory = result["review_mandate_advisory"].lower()
        assert "blocks" not in advisory, (
            "advisory asserts a hard block, but review_gate_mode defaults to "
            f"'warn' and delivery proceeds: {advisory!r}"
        )
        # The ACTION must still be there — this must not pass by going silent.
        assert "trw_review" in advisory and "trw_deliver" in advisory

    def test_init_review_required_standard(self, orch_tools: dict[str, Any]) -> None:
        """STANDARD complexity_hint -> REVIEW mandatory -> review_required true."""
        result = orch_tools["trw_init"].fn(
            task_name="standard-review-task",
            complexity_hint="STANDARD",
        )
        assert result["review_required"] == "true"
        advisory = result["review_mandate_advisory"].lower()
        assert "review" in advisory and "mandatory" in advisory

    def test_init_review_not_required_minimal(self, orch_tools: dict[str, Any]) -> None:
        """EASY complexity_hint -> MINIMAL -> NO review_required key (not 'false')."""
        result = orch_tools["trw_init"].fn(
            task_name="easy-no-review-task",
            complexity_hint="EASY",
        )
        assert "review_required" not in result
        assert "review_mandate_advisory" not in result

    def test_init_review_not_required_no_hint(self, orch_tools: dict[str, Any]) -> None:
        """No complexity hint -> phase_requirements is None -> fail-open, no key."""
        result = orch_tools["trw_init"].fn(task_name="no-hint-task")
        assert "review_required" not in result
        assert "review_mandate_advisory" not in result

    def test_init_review_advisory_respects_kill_switch(
        self,
        orch_tools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NFR04: review_mandate_advisory_enabled=False suppresses the advisory."""
        from trw_mcp.models.config import get_config

        cfg = get_config()
        disabled = cfg.model_copy(update={"review_mandate_advisory_enabled": False})
        # Patch at the consumer site: orchestration.py calls get_config() at
        # runtime, so redirecting it here flows the disabled config into trw_init.
        monkeypatch.setattr("trw_mcp.tools.orchestration.get_config", lambda: disabled)

        result = orch_tools["trw_init"].fn(
            task_name="killswitch-task",
            complexity_hint="HARD",
        )
        assert "review_required" not in result
        assert "review_mandate_advisory" not in result


class TestTrwStatusVersionWarning:
    """Tests for trw_status version staleness warning (line 259)."""

    def test_status_shows_version_warning_when_stale(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """version_warning present when run framework differs from deployed version."""
        # The staleness check lives in _orchestration_phase, which binds
        # resolve_project_root at module level via a from-import. Conftest only
        # patches the orchestration/_paths bindings, so redirect this one to the
        # test tmp_path so the test-written VERSION.yaml is the one consulted.
        monkeypatch.setattr(
            "trw_mcp.tools._orchestration_phase.resolve_project_root",
            lambda: tmp_path,
        )
        init_result = orch_tools["trw_init"].fn(task_name="stale-version-task")

        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        writer = FileStateWriter()
        writer.write_yaml(
            version_path,
            {
                "framework_version": "v99.0_TRW",
                "aaref_version": "v9.0.0",
                "trw_mcp_version": "9.9.9",
            },
        )

        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

        assert "version_warning" in status
        assert "v99.0_TRW" in str(status["version_warning"])

    def test_status_no_version_warning_when_current(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No version_warning when run framework matches deployed version."""
        # Redirect the staleness check's project root to the test tmp_path so it
        # reads the VERSION.yaml that trw_init deploys (current framework version),
        # not the real repo's VERSION.yaml. See sibling test for rationale.
        monkeypatch.setattr(
            "trw_mcp.tools._orchestration_phase.resolve_project_root",
            lambda: tmp_path,
        )
        init_result = orch_tools["trw_init"].fn(task_name="current-version-task")
        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])
        assert "version_warning" not in status


class TestTrwStatusNoReversionBlock:
    """The never-written phase_revert metric is no longer published."""

    def test_status_has_no_reversions_key(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="reversion-task")
        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

        assert "reversions" not in status
