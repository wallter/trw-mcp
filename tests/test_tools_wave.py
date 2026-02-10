"""Tests for wave management tools — PRD-CORE-012.

Tests: trw_wave_plan, trw_shard_start, trw_shard_complete,
trw_wave_complete, trw_wave_context, trw_shard_prompt,
and enhanced trw_status wave progress.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.exceptions import ValidationError
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.orchestration as orch_mod
    import trw_mcp.tools.wave as wave_mod

    monkeypatch.setattr(orch_mod, "_config", orch_mod.TRWConfig())
    monkeypatch.setattr(wave_mod, "_config", wave_mod.TRWConfig())
    return tmp_path


@pytest.fixture
def wave_server() -> "FastMCP":
    """Create a fresh FastMCP server with orchestration + wave tools."""
    from fastmcp import FastMCP
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.wave import register_wave_tools

    srv = FastMCP("test-wave")
    register_orchestration_tools(srv)
    register_wave_tools(srv)
    return srv


def _get_tools(server: "FastMCP") -> dict:
    """Extract tool dict from server."""
    return {t.name: t for t in server._tool_manager._tools.values()}


def _init_run(server: "FastMCP", task_name: str = "test-task") -> dict:
    """Bootstrap a run and return result dict."""
    tools = _get_tools(server)
    return tools["trw_init"].fn(task_name=task_name)


def _simple_wave_plan() -> list[dict]:
    """Return a simple 2-wave plan for testing."""
    return [
        {
            "wave": 1,
            "shards": [
                {"id": "shard-001", "title": "Research auth", "wave": 1},
                {"id": "shard-002", "title": "Research db", "wave": 1},
            ],
            "depends_on": [],
        },
        {
            "wave": 2,
            "shards": [
                {"id": "shard-003", "title": "Implement", "wave": 2,
                 "input_refs": ["scratch/shard-001/findings.yaml"]},
            ],
            "depends_on": [1],
        },
    ]


# ---------------------------------------------------------------------------
# Wave Planning (FR01, FR02) — 8 tests
# ---------------------------------------------------------------------------


class TestTrwWavePlan:
    """Tests for trw_wave_plan tool."""

    def test_wave_plan_valid_single_wave(self, wave_server: "FastMCP") -> None:
        """Single wave with 3 shards creates manifest correctly."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)

        waves = [{
            "wave": 1,
            "shards": [
                {"id": "s1", "title": "Shard 1", "wave": 1},
                {"id": "s2", "title": "Shard 2", "wave": 1},
                {"id": "s3", "title": "Shard 3", "wave": 1},
            ],
        }]

        result = tools["trw_wave_plan"].fn(
            waves=waves, run_path=init["run_path"],
        )

        assert result["status"] == "wave_plan_created"
        assert result["wave_count"] == 1
        assert result["shard_count"] == 3

    def test_wave_plan_valid_multi_wave(self, wave_server: "FastMCP") -> None:
        """Multi-wave plan with dependencies."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)

        result = tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        assert result["wave_count"] == 2
        assert result["shard_count"] == 3
        assert len(result["waves"]) == 2
        assert result["waves"][1]["depends_on"] == [1]

    def test_wave_plan_circular_dependency_rejected(
        self, wave_server: "FastMCP",
    ) -> None:
        """Circular depends_on raises ValidationError."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)

        waves = [
            {"wave": 1, "shards": [{"id": "s1", "title": "A", "wave": 1}],
             "depends_on": [2]},
            {"wave": 2, "shards": [{"id": "s2", "title": "B", "wave": 2}],
             "depends_on": [1]},
        ]

        with pytest.raises(ValidationError, match="Circular dependency"):
            tools["trw_wave_plan"].fn(
                waves=waves, run_path=init["run_path"],
            )

    def test_wave_plan_duplicate_shard_id_rejected(
        self, wave_server: "FastMCP",
    ) -> None:
        """Same shard ID in two waves raises error."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)

        waves = [
            {"wave": 1, "shards": [{"id": "dup", "title": "A", "wave": 1}]},
            {"wave": 2, "shards": [{"id": "dup", "title": "B", "wave": 2}]},
        ]

        with pytest.raises(ValidationError, match="Duplicate shard ID"):
            tools["trw_wave_plan"].fn(
                waves=waves, run_path=init["run_path"],
            )

    def test_wave_plan_invalid_wave_number_rejected(
        self, wave_server: "FastMCP",
    ) -> None:
        """Wave 0 or negative rejected."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)

        waves = [{"wave": 0, "shards": [{"id": "s1", "title": "A"}]}]

        with pytest.raises(ValidationError, match="Invalid wave number"):
            tools["trw_wave_plan"].fn(
                waves=waves, run_path=init["run_path"],
            )

    def test_wave_plan_creates_scratch_dirs(
        self, wave_server: "FastMCP",
    ) -> None:
        """scratch/shard-{id}/ created for each shard."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)
        run_path = Path(init["run_path"])

        waves = [{"wave": 1, "shards": [
            {"id": "alpha", "title": "A", "wave": 1},
            {"id": "beta", "title": "B", "wave": 1},
        ]}]

        tools["trw_wave_plan"].fn(waves=waves, run_path=init["run_path"])

        assert (run_path / "scratch" / "shard-alpha").is_dir()
        assert (run_path / "scratch" / "shard-beta").is_dir()

    def test_wave_plan_writes_both_manifests(
        self, wave_server: "FastMCP",
    ) -> None:
        """wave_manifest.yaml and manifest.yaml both written."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)
        run_path = Path(init["run_path"])

        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        reader = FileStateReader()
        wave_manifest = reader.read_yaml(
            run_path / "shards" / "wave_manifest.yaml",
        )
        shard_manifest = reader.read_yaml(
            run_path / "shards" / "manifest.yaml",
        )

        assert "waves" in wave_manifest
        assert len(wave_manifest["waves"]) == 2
        assert "shards" in shard_manifest
        assert len(shard_manifest["shards"]) == 3

    def test_wave_plan_logs_event(self, wave_server: "FastMCP") -> None:
        """wave_plan_created event in events.jsonl."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)
        run_path = Path(init["run_path"])

        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        reader = FileStateReader()
        events = reader.read_jsonl(run_path / "meta" / "events.jsonl")
        plan_events = [e for e in events if e.get("event") == "wave_plan_created"]
        assert len(plan_events) == 1
        assert plan_events[0]["wave_count"] == 2
        assert plan_events[0]["shard_count"] == 3


# ---------------------------------------------------------------------------
# Shard Lifecycle (FR03) — 7 tests
# ---------------------------------------------------------------------------


class TestShardLifecycle:
    """Tests for trw_shard_start and trw_shard_complete."""

    def _setup_plan(self, server: "FastMCP") -> tuple[dict, dict]:
        """Init run and create wave plan. Returns (init_result, tools)."""
        init = _init_run(server)
        tools = _get_tools(server)
        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )
        return init, tools

    def test_shard_start_pending_to_active(
        self, wave_server: "FastMCP",
    ) -> None:
        """Valid transition pending -> active."""
        init, tools = self._setup_plan(wave_server)

        result = tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )

        assert result["status"] == "shard_started"
        assert result["shard_id"] == "shard-001"
        assert result["wave"] == 1

        # Verify manifest updated
        reader = FileStateReader()
        manifest = reader.read_yaml(
            Path(init["run_path"]) / "shards" / "manifest.yaml",
        )
        shard = next(
            s for s in manifest["shards"] if s["id"] == "shard-001"
        )
        assert shard["status"] == "active"

    def test_shard_start_unknown_shard_rejected(
        self, wave_server: "FastMCP",
    ) -> None:
        """Shard not in manifest raises ValidationError."""
        init, tools = self._setup_plan(wave_server)

        with pytest.raises(ValidationError, match="not found"):
            tools["trw_shard_start"].fn(
                shard_id="nonexistent", run_path=init["run_path"],
            )

    def test_shard_start_already_active_rejected(
        self, wave_server: "FastMCP",
    ) -> None:
        """Double-start rejected."""
        init, tools = self._setup_plan(wave_server)

        tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )

        with pytest.raises(ValidationError, match="expected 'pending'"):
            tools["trw_shard_start"].fn(
                shard_id="shard-001", run_path=init["run_path"],
            )

    def test_shard_complete_active_to_complete(
        self, wave_server: "FastMCP",
    ) -> None:
        """Valid completion: active -> complete."""
        init, tools = self._setup_plan(wave_server)

        tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )
        result = tools["trw_shard_complete"].fn(
            shard_id="shard-001", status="complete",
            run_path=init["run_path"],
        )

        assert result["status"] == "shard_complete"
        assert result["completion_status"] == "complete"

    def test_shard_complete_active_to_partial(
        self, wave_server: "FastMCP",
    ) -> None:
        """Partial completion."""
        init, tools = self._setup_plan(wave_server)

        tools["trw_shard_start"].fn(
            shard_id="shard-002", run_path=init["run_path"],
        )
        result = tools["trw_shard_complete"].fn(
            shard_id="shard-002", status="partial",
            run_path=init["run_path"],
        )

        assert result["completion_status"] == "partial"

    def test_shard_complete_active_to_failed(
        self, wave_server: "FastMCP",
    ) -> None:
        """Failure recorded."""
        init, tools = self._setup_plan(wave_server)

        tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )
        result = tools["trw_shard_complete"].fn(
            shard_id="shard-001", status="failed",
            run_path=init["run_path"],
        )

        assert result["completion_status"] == "failed"

    def test_shard_complete_logs_typed_event(
        self, wave_server: "FastMCP",
    ) -> None:
        """shard_completed event with correct fields."""
        init, tools = self._setup_plan(wave_server)

        tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )
        tools["trw_shard_complete"].fn(
            shard_id="shard-001", status="complete",
            run_path=init["run_path"],
        )

        reader = FileStateReader()
        events = reader.read_jsonl(
            Path(init["run_path"]) / "meta" / "events.jsonl",
        )
        complete_events = [
            e for e in events if e.get("event") == "shard_completed"
        ]
        assert len(complete_events) == 1
        assert complete_events[0]["shard_id"] == "shard-001"
        assert complete_events[0]["status"] == "complete"
        assert complete_events[0]["wave"] == 1


# ---------------------------------------------------------------------------
# Wave Completion (FR04) — 4 tests
# ---------------------------------------------------------------------------


class TestWaveComplete:
    """Tests for trw_wave_complete tool."""

    def _complete_wave_1(self, server: "FastMCP") -> tuple[dict, dict]:
        """Set up and complete all wave 1 shards. Returns (init, tools)."""
        init = _init_run(server)
        tools = _get_tools(server)
        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        for sid in ("shard-001", "shard-002"):
            tools["trw_shard_start"].fn(
                shard_id=sid, run_path=init["run_path"],
            )
            tools["trw_shard_complete"].fn(
                shard_id=sid, status="complete", run_path=init["run_path"],
            )

        return init, tools

    def test_wave_complete_all_shards_done(
        self, wave_server: "FastMCP",
    ) -> None:
        """Wave status becomes complete when all shards complete."""
        init, tools = self._complete_wave_1(wave_server)

        result = tools["trw_wave_complete"].fn(
            wave_number=1, run_path=init["run_path"],
        )

        assert result["wave_status"] == "complete"
        assert result["shards_complete"] == 2
        assert result["shards_failed"] == 0

    def test_wave_complete_with_failures(
        self, wave_server: "FastMCP",
    ) -> None:
        """Wave status becomes failed when a shard fails."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)
        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        # Complete one, fail the other
        tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )
        tools["trw_shard_complete"].fn(
            shard_id="shard-001", status="complete",
            run_path=init["run_path"],
        )
        tools["trw_shard_start"].fn(
            shard_id="shard-002", run_path=init["run_path"],
        )
        tools["trw_shard_complete"].fn(
            shard_id="shard-002", status="failed",
            run_path=init["run_path"],
        )

        result = tools["trw_wave_complete"].fn(
            wave_number=1, run_path=init["run_path"],
        )

        assert result["wave_status"] == "failed"

    def test_wave_complete_shards_still_active_rejected(
        self, wave_server: "FastMCP",
    ) -> None:
        """Incomplete shards block completion."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)
        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        # Only start one shard, don't complete
        tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )

        with pytest.raises(ValidationError, match="cannot complete"):
            tools["trw_wave_complete"].fn(
                wave_number=1, run_path=init["run_path"],
            )

    def test_wave_complete_creates_checkpoint(
        self, wave_server: "FastMCP",
    ) -> None:
        """Checkpoint appended to checkpoints.jsonl."""
        init, tools = self._complete_wave_1(wave_server)

        tools["trw_wave_complete"].fn(
            wave_number=1, run_path=init["run_path"],
        )

        reader = FileStateReader()
        checkpoints = reader.read_jsonl(
            Path(init["run_path"]) / "meta" / "checkpoints.jsonl",
        )
        assert len(checkpoints) >= 1
        assert checkpoints[-1]["wave"] == 1
        assert checkpoints[-1]["wave_status"] == "complete"


# ---------------------------------------------------------------------------
# Wave Context (FR05) — 3 tests
# ---------------------------------------------------------------------------


class TestWaveContext:
    """Tests for trw_wave_context tool."""

    def _setup_completed_wave(
        self, server: "FastMCP",
    ) -> tuple[dict, dict]:
        """Create plan, complete wave 1, return (init, tools)."""
        init = _init_run(server)
        tools = _get_tools(server)
        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        for sid in ("shard-001", "shard-002"):
            tools["trw_shard_start"].fn(
                shard_id=sid, run_path=init["run_path"],
            )
            tools["trw_shard_complete"].fn(
                shard_id=sid, status="complete", run_path=init["run_path"],
            )

        tools["trw_wave_complete"].fn(
            wave_number=1, run_path=init["run_path"],
        )
        return init, tools

    def test_wave_context_reads_findings(
        self, wave_server: "FastMCP",
    ) -> None:
        """Extracts summaries from findings.yaml files."""
        init, tools = self._setup_completed_wave(wave_server)
        run_path = Path(init["run_path"])

        # Write findings for shard-001
        writer = FileStateWriter()
        findings_dir = run_path / "scratch" / "shard-shard-001"
        findings_dir.mkdir(parents=True, exist_ok=True)
        writer.write_yaml(findings_dir / "findings.yaml", {
            "shard_id": "shard-001",
            "status": "complete",
            "summary": "Auth uses JWT tokens",
            "findings": [
                {"key": "jwt_auth", "detail": "..."},
                {"key": "refresh_tokens", "detail": "..."},
            ],
        })

        result = tools["trw_wave_context"].fn(
            wave_number=1, run_path=init["run_path"],
        )

        assert result["wave"] == 1
        assert result["status"] == "complete"
        assert len(result["shards"]) == 2
        # shard-001 should have findings
        s1 = next(s for s in result["shards"] if s["id"] == "shard-001")
        assert s1["summary"] == "Auth uses JWT tokens"
        assert "jwt_auth" in s1["key_findings"]

    def test_wave_context_missing_output_reported_as_gap(
        self, wave_server: "FastMCP",
    ) -> None:
        """Missing files are gaps not errors."""
        init, tools = self._setup_completed_wave(wave_server)

        result = tools["trw_wave_context"].fn(
            wave_number=1, run_path=init["run_path"],
        )

        assert len(result["gaps"]) > 0

    def test_wave_context_writes_blackboard(
        self, wave_server: "FastMCP",
    ) -> None:
        """Context written to scratch/_blackboard/wave-N-context.yaml."""
        init, tools = self._setup_completed_wave(wave_server)

        tools["trw_wave_context"].fn(
            wave_number=1, run_path=init["run_path"],
        )

        run_path = Path(init["run_path"])
        context_path = (
            run_path / "scratch" / "_blackboard" / "wave-1-context.yaml"
        )
        assert context_path.exists()

        reader = FileStateReader()
        context = reader.read_yaml(context_path)
        assert context["wave"] == 1


# ---------------------------------------------------------------------------
# Shard Prompt (FR06) — 2 tests
# ---------------------------------------------------------------------------


class TestShardPrompt:
    """Tests for trw_shard_prompt tool."""

    def _setup_plan(self, server: "FastMCP") -> tuple[dict, dict]:
        init = _init_run(server)
        tools = _get_tools(server)
        waves = [{
            "wave": 1,
            "shards": [{
                "id": "s1", "title": "Research auth", "wave": 1,
                "goals": ["Investigate JWT"],
                "output_contract": {
                    "file": "scratch/shard-s1/findings.yaml",
                    "keys": ["summary", "findings"],
                    "required": True,
                },
                "input_refs": ["docs/auth-spec.md"],
            }],
        }]
        tools["trw_wave_plan"].fn(waves=waves, run_path=init["run_path"])
        return init, tools

    def test_shard_prompt_includes_all_blocks(
        self, wave_server: "FastMCP",
    ) -> None:
        """Output contract, MCP guidance, persistence rules, instructions."""
        init, tools = self._setup_plan(wave_server)

        result = tools["trw_shard_prompt"].fn(
            shard_id="s1",
            instructions="Find all authentication mechanisms",
            run_path=init["run_path"],
        )

        prompt = result["prompt"]
        assert "<shard_identity>" in prompt
        assert "s1" in prompt
        assert "<output_contract>" in prompt
        assert "<mcp_guidance>" in prompt
        assert "<persistence_rules>" in prompt
        assert "<input_references>" in prompt
        assert "<instructions>" in prompt
        assert "Find all authentication mechanisms" in prompt

    def test_shard_prompt_renders_output_contract(
        self, wave_server: "FastMCP",
    ) -> None:
        """Contract YAML block present and accurate."""
        init, tools = self._setup_plan(wave_server)

        result = tools["trw_shard_prompt"].fn(
            shard_id="s1",
            instructions="test",
            run_path=init["run_path"],
        )

        prompt = result["prompt"]
        assert "findings.yaml" in prompt
        assert "summary" in prompt
        assert "token_estimate" in result


# ---------------------------------------------------------------------------
# Enhanced Status (FR07) — 2 tests
# ---------------------------------------------------------------------------


class TestEnhancedStatus:
    """Tests for trw_status wave progress enhancement."""

    def test_status_includes_wave_progress(
        self, wave_server: "FastMCP",
    ) -> None:
        """wave_progress key present when manifest exists."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)

        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        # Start one shard
        tools["trw_shard_start"].fn(
            shard_id="shard-001", run_path=init["run_path"],
        )

        result = tools["trw_status"].fn(run_path=init["run_path"])

        assert "wave_progress" in result
        wp = result["wave_progress"]
        assert wp["total_waves"] == 2

        # Wave 1 should show 1 active, 1 pending
        w1 = wp["wave_details"][0]
        assert w1["shards"]["active"] == 1
        assert w1["shards"]["pending"] == 1

    def test_status_no_wave_progress_without_manifest(
        self, wave_server: "FastMCP",
    ) -> None:
        """Backward compatible: no wave_progress when no manifest."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)

        result = tools["trw_status"].fn(run_path=init["run_path"])

        assert "wave_progress" not in result


# ---------------------------------------------------------------------------
# Integration Tests — 2 tests
# ---------------------------------------------------------------------------


class TestWaveLifecycleIntegration:
    """End-to-end wave lifecycle test."""

    def test_full_wave_lifecycle(self, wave_server: "FastMCP") -> None:
        """plan -> start all -> complete all -> wave_complete -> wave_context."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)
        run_path = Path(init["run_path"])

        # Plan
        tools["trw_wave_plan"].fn(
            waves=_simple_wave_plan(), run_path=init["run_path"],
        )

        # Wave 1: start all, complete all
        for sid in ("shard-001", "shard-002"):
            tools["trw_shard_start"].fn(
                shard_id=sid, run_path=init["run_path"],
            )

        # Write findings before completing
        writer = FileStateWriter()
        for sid in ("shard-001", "shard-002"):
            scratch = run_path / "scratch" / f"shard-{sid}"
            scratch.mkdir(parents=True, exist_ok=True)
            writer.write_yaml(scratch / "findings.yaml", {
                "shard_id": sid,
                "status": "complete",
                "summary": f"Findings for {sid}",
                "findings": [{"key": f"finding_{sid}"}],
            })

        for sid in ("shard-001", "shard-002"):
            tools["trw_shard_complete"].fn(
                shard_id=sid, status="complete",
                run_path=init["run_path"],
            )

        # Complete wave 1
        wave_result = tools["trw_wave_complete"].fn(
            wave_number=1, run_path=init["run_path"],
        )
        assert wave_result["wave_status"] == "complete"

        # Get wave context
        context = tools["trw_wave_context"].fn(
            wave_number=1, run_path=init["run_path"],
        )
        assert context["status"] == "complete"
        assert len(context["shards"]) == 2

        # Status should show wave progress
        status = tools["trw_status"].fn(run_path=init["run_path"])
        assert "wave_progress" in status
        assert status["wave_progress"]["completed_waves"] == 1

        # Wave 2: start, complete
        tools["trw_shard_start"].fn(
            shard_id="shard-003", run_path=init["run_path"],
        )
        tools["trw_shard_complete"].fn(
            shard_id="shard-003", status="complete",
            run_path=init["run_path"],
        )

        wave2_result = tools["trw_wave_complete"].fn(
            wave_number=2, run_path=init["run_path"],
        )
        assert wave2_result["wave_status"] == "complete"

    def test_wave_plan_with_output_contracts(
        self, wave_server: "FastMCP",
    ) -> None:
        """Shards with output contracts are validated at wave completion."""
        init = _init_run(wave_server)
        tools = _get_tools(wave_server)
        run_path = Path(init["run_path"])

        waves = [{
            "wave": 1,
            "shards": [{
                "id": "impl-1",
                "title": "Implement feature",
                "wave": 1,
                "output_contract": {
                    "file": "scratch/shard-impl-1/result.yaml",
                    "keys": ["status", "summary"],
                    "required": True,
                },
            }],
        }]

        tools["trw_wave_plan"].fn(waves=waves, run_path=init["run_path"])
        tools["trw_shard_start"].fn(
            shard_id="impl-1", run_path=init["run_path"],
        )

        # Write contract output
        writer = FileStateWriter()
        output_dir = run_path / "scratch" / "shard-impl-1"
        output_dir.mkdir(parents=True, exist_ok=True)
        writer.write_yaml(output_dir / "result.yaml", {
            "status": "complete",
            "summary": "Feature implemented",
        })

        tools["trw_shard_complete"].fn(
            shard_id="impl-1", status="complete",
            run_path=init["run_path"],
        )

        result = tools["trw_wave_complete"].fn(
            wave_number=1, run_path=init["run_path"],
        )

        assert result["wave_status"] == "complete"
