from __future__ import annotations

import json
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
            config_overrides={"custom_key": "custom_value", "parallelism_max": "8"},
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
            config_overrides=None,
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
            config_overrides={"should_not": "appear"},
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
        """HARD complexity_hint -> COMPREHENSIVE -> review_required + override nudge."""
        result = orch_tools["trw_init"].fn(
            task_name="hard-review-task",
            complexity_hint="HARD",
        )
        assert result["review_required"] == "true"
        advisory = result["review_mandate_advisory"].lower()
        assert "review" in advisory
        assert "mandatory" in advisory
        assert "overrides the session ceremony tier" in advisory

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
        manifest_path = run_path / ("shards" if manifest_location == "shards" else "meta") / "wave_manifest.yaml"
        writer.write_yaml(manifest_path, wave_manifest)

        return init_result["run_path"], run_path

    def test_status_includes_waves_when_manifest_in_shards(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_status returns 'waves' key when wave_manifest.yaml in shards/."""
        run_path_str, _ = self._create_run_with_wave_manifest(
            orch_tools,
            "wave-shards-task",
            manifest_location="shards",
        )
        status = orch_tools["trw_status"].fn(run_path=run_path_str)

        assert "waves" in status
        assert isinstance(status["waves"], list)
        assert len(status["waves"]) == 2

    def test_status_includes_wave_progress_when_manifest_exists(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_status includes 'wave_progress' dict when wave data present."""
        run_path_str, _ = self._create_run_with_wave_manifest(
            orch_tools,
            "wave-progress-task",
        )
        status = orch_tools["trw_status"].fn(run_path=run_path_str)

        assert "wave_progress" in status
        progress = status["wave_progress"]
        assert progress["total_waves"] == 2

    def test_status_wave_manifest_in_meta_fallback(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_status falls back to meta/wave_manifest.yaml when shards/ not present."""
        run_path_str, _ = self._create_run_with_wave_manifest(
            orch_tools,
            "wave-meta-task",
            manifest_location="meta",
        )
        status = orch_tools["trw_status"].fn(run_path=run_path_str)

        assert "waves" in status


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


class TestRunCurrentness:
    """PRD-INFRA-164 FR08: run/session currentness uses deployed-canon + live-process
    fingerprints and preserves current/stale/unknown — absence never means current."""

    def test_run_currentness_uses_fingerprints_and_preserves_unknown(self) -> None:
        from trw_mcp.tools._orchestration_phase import evaluate_run_currentness

        # Exact three-way match => current.
        currentness, reasons = evaluate_run_currentness(
            "dep-1",
            "proc-1",
            current_deployed_fingerprint="dep-1",
            current_process_fingerprint="proc-1",
        )
        assert currentness == "current"
        assert reasons == []

        # Deployment moved => stale, and the reason names the deployment layer.
        currentness, reasons = evaluate_run_currentness(
            "dep-1",
            "proc-1",
            current_deployed_fingerprint="dep-2",
            current_process_fingerprint="proc-1",
        )
        assert currentness == "stale"
        assert any("deployed canon generation moved" in r for r in reasons)

        # Process changed/restarted => stale, reason names the process layer.
        currentness, reasons = evaluate_run_currentness(
            "dep-1",
            "proc-1",
            current_deployed_fingerprint="dep-1",
            current_process_fingerprint="proc-2",
        )
        assert currentness == "stale"
        assert any("process changed/restarted" in r for r in reasons)

        # Legacy run (no stamp) => unknown, NEVER current.
        currentness, reasons = evaluate_run_currentness(
            None,
            None,
            current_deployed_fingerprint="dep-1",
            current_process_fingerprint="proc-1",
        )
        assert currentness == "unknown"
        assert reasons

        # Missing process fingerprint (unattestable) => unknown, never current.
        currentness, reasons = evaluate_run_currentness(
            "dep-1",
            "proc-1",
            current_deployed_fingerprint="dep-1",
            current_process_fingerprint=None,
        )
        assert currentness == "unknown"

        # Malformed/unavailable current deployed generation => unknown.
        currentness, reasons = evaluate_run_currentness(
            "dep-1",
            "proc-1",
            current_deployed_fingerprint=None,
            current_process_fingerprint="proc-1",
        )
        assert currentness == "unknown"

    def test_trw_init_stamps_run_canon_fingerprints_and_summarizes_current(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """Integration: trw_init writes the fingerprint stamp; a same-process run
        summarizes as current (deployed + process both match live state)."""
        # Freeze a real live-process fingerprint for this test process so the
        # currentness comparison has both layers available.
        from fastmcp import FastMCP

        from trw_mcp.canons.fingerprint import (
            freeze_fingerprint,
            reset_frozen_fingerprint,
            set_frozen_fingerprint,
        )
        from trw_mcp.canons.registry import load_registry, managed_source_digests
        from trw_mcp.server._live_fingerprint import build_realized_surface
        from trw_mcp.tools._orchestration_phase import summarize_run_currentness

        reset_frozen_fingerprint()
        registry = load_registry()
        server = FastMCP("test")
        from trw_mcp.tools.learning import register_learning_tools

        register_learning_tools(server)
        fp = freeze_fingerprint(
            trw_mcp_version="0.0.0",
            framework_version="v26.1_TRW",
            aaref_version="v3.2.0",
            template_version="3.2",
            registry_digest=registry.digest,
            source_digests=managed_source_digests(registry),
            surface=build_realized_surface(server),
        )
        set_frozen_fingerprint(fp)
        try:
            init_result = orch_tools["trw_init"].fn(task_name="currentness-task")
            run_root = Path(init_result["run_path"])

            stamp_path = run_root / "meta" / "canon_fingerprints.yaml"
            assert stamp_path.exists(), "trw_init must stamp canon fingerprints"

            summary = summarize_run_currentness(run_root, run_framework="v26.1_TRW")
            assert summary["currentness"] == "current"
            assert summary["run_deployed_canon_fingerprint"] == registry.digest
            assert summary["run_live_process_fingerprint"] == fp.digest
        finally:
            reset_frozen_fingerprint()

    def test_run_without_stamp_is_unknown_not_current(
        self,
        orch_tools: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """A run directory with no fingerprint stamp is unknown, never current."""
        from trw_mcp.tools._orchestration_phase import summarize_run_currentness

        run_root = tmp_path / "legacy-run"
        (run_root / "meta").mkdir(parents=True)
        summary = summarize_run_currentness(run_root, run_framework="v26.1_TRW")
        assert summary["currentness"] == "unknown"


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
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """Phase revert events logged by other means appear in reversion metrics."""
        init_result = orch_tools["trw_init"].fn(task_name="rev-events-task")
        run_path = Path(init_result["run_path"])

        events_path = run_path / "meta" / "events.jsonl"
        with open(events_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "ts": "2026-01-01T00:00:00Z",
                        "event": "phase_revert",
                        "from_phase": "implement",
                        "to_phase": "plan",
                        "trigger": "scope_creep",
                    }
                )
                + "\n"
            )

        status = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert status["reversions"]["count"] == 1
        assert status["reversions"]["latest"] is not None
