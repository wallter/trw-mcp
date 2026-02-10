"""Tests for PRD-CORE-006: Dynamic wave adaptation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools.wave import register_wave_tools


@pytest.fixture
def reader() -> FileStateReader:
    """Provide a FileStateReader instance."""
    return FileStateReader()


@pytest.fixture
def writer() -> FileStateWriter:
    """Provide a FileStateWriter instance."""
    return FileStateWriter()


@pytest.fixture
def wave_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a run directory with wave manifest for adaptation tests."""
    run_dir = tmp_path / "docs" / "test" / "runs" / "20260210T120000Z-test"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "shards").mkdir()
    (run_dir / "scratch").mkdir()

    writer.write_yaml(meta / "run.yaml", {
        "run_id": "20260210T120000Z-test",
        "task": "test-task",
        "framework": "v18.0_TRW",
        "status": "active",
        "phase": "implement",
        "confidence": "medium",
    })

    writer.write_yaml(run_dir / "shards" / "wave_manifest.yaml", {
        "waves": [
            {"wave": 1, "shards": ["S1", "S2"], "status": "complete", "depends_on": []},
            {"wave": 2, "shards": ["S3"], "status": "pending", "depends_on": [1]},
        ],
        "version": 1,
        "adaptation_history": [],
    })

    writer.write_yaml(run_dir / "shards" / "manifest.yaml", {
        "shards": [
            {"id": "S1", "title": "Shard 1", "wave": 1, "status": "complete", "confidence": "high"},
            {"id": "S2", "title": "Shard 2", "wave": 1, "status": "complete", "confidence": "medium"},
            {"id": "S3", "title": "Shard 3", "wave": 2, "status": "pending", "confidence": "medium"},
        ],
    })

    writer.append_jsonl(meta / "events.jsonl", {
        "ts": "2026-02-10T12:00:00Z",
        "event": "run_init",
    })

    for sid in ("S1", "S2", "S3"):
        (run_dir / "scratch" / f"shard-{sid}").mkdir(parents=True, exist_ok=True)

    return run_dir


def _make_adapt_call(
    run_dir: Path,
    *,
    adaptation_enabled: bool = True,
    **kwargs: object,
) -> dict[str, object]:
    """Call trw_wave_adapt with mocked module-level dependencies.

    Args:
        run_dir: Path to the run directory.
        adaptation_enabled: Whether adaptation is enabled in config.
        **kwargs: Additional keyword arguments forwarded to the tool function.
    """
    config = TRWConfig(adaptation_enabled=adaptation_enabled)

    with (
        patch("trw_mcp.tools.wave._config", config),
        patch("trw_mcp.tools.wave._reader", FileStateReader()),
        patch("trw_mcp.tools.wave._writer", FileStateWriter()),
        patch("trw_mcp.tools.wave._events", FileEventLogger(FileStateWriter())),
    ):
        server = FastMCP("test")
        register_wave_tools(server)
        tool_fn = server._tool_manager._tools["trw_wave_adapt"].fn
        return tool_fn(run_path=str(run_dir), **kwargs)


# ---------------------------------------------------------------------------
# No triggers / disabled / event logging
# ---------------------------------------------------------------------------


class TestAdaptationBasics:
    """Tests for baseline adaptation behavior: no triggers, disabled config, event logging."""

    def test_no_triggers_returns_no_adaptation(self, wave_run_dir: Path) -> None:
        result = _make_adapt_call(wave_run_dir, wave_number=1)
        assert result["status"] == "no_adaptation"
        assert result["wave"] == 1

    def test_disabled_returns_immediately(self, wave_run_dir: Path) -> None:
        result = _make_adapt_call(
            wave_run_dir, adaptation_enabled=False, wave_number=1,
        )
        assert result["status"] == "disabled"

    def test_adaptation_event_logged(
        self, wave_run_dir: Path, reader: FileStateReader, writer: FileStateWriter,
    ) -> None:
        manifest = reader.read_yaml(wave_run_dir / "shards" / "manifest.yaml")
        for s in manifest["shards"]:
            if s["id"] == "S2":
                s["confidence"] = "low"
        writer.write_yaml(wave_run_dir / "shards" / "manifest.yaml", manifest)

        result = _make_adapt_call(wave_run_dir, wave_number=1)
        assert result["status"] == "adapted"

        events_content = (wave_run_dir / "meta" / "events.jsonl").read_text(encoding="utf-8")
        assert "wave_adapted" in events_content


# ---------------------------------------------------------------------------
# Trigger scenarios
# ---------------------------------------------------------------------------


class TestLowConfidenceTrigger:
    """Low confidence shard fires adaptation trigger."""

    def test_low_confidence_shard_fires_trigger(
        self, wave_run_dir: Path, reader: FileStateReader, writer: FileStateWriter,
    ) -> None:
        manifest = reader.read_yaml(wave_run_dir / "shards" / "manifest.yaml")
        for s in manifest["shards"]:
            if s["id"] == "S2":
                s["confidence"] = "low"
        writer.write_yaml(wave_run_dir / "shards" / "manifest.yaml", manifest)

        result = _make_adapt_call(wave_run_dir, wave_number=1)
        assert result["status"] == "adapted"
        assert result["shards_added"] >= 1


class TestShardSignalTrigger:
    """Shard adaptation_signal.yaml fires trigger."""

    def test_shard_signal_fires_trigger(
        self, wave_run_dir: Path, writer: FileStateWriter,
    ) -> None:
        signal_dir = wave_run_dir / "scratch" / "shard-S1"
        writer.write_yaml(signal_dir / "adaptation_signal.yaml", {
            "trigger_type": "shard_signal",
            "description": "Need additional research shard",
            "severity": "minor",
            "add_shard": {
                "id": "S1-extra",
                "title": "Extra research for S1",
                "goals": ["Deep dive on findings"],
            },
        })

        result = _make_adapt_call(wave_run_dir, wave_number=1)
        assert result["status"] == "adapted"
        assert result["shards_added"] >= 1


# ---------------------------------------------------------------------------
# Approval tiers
# ---------------------------------------------------------------------------


class TestApprovalTiers:
    """Minor adaptations auto-approve; major ones require explicit approval."""

    def test_minor_adaptation_auto_approved(
        self, wave_run_dir: Path, writer: FileStateWriter,
    ) -> None:
        signal_dir = wave_run_dir / "scratch" / "shard-S1"
        writer.write_yaml(signal_dir / "adaptation_signal.yaml", {
            "trigger_type": "scope_expansion",
            "description": "Minor scope change",
            "severity": "minor",
            "add_shard": {"id": "scope-S1", "title": "Scope expansion"},
        })

        result = _make_adapt_call(wave_run_dir, wave_number=1, auto_approve=True)
        assert result["status"] == "adapted"
        assert result.get("auto_approved") is True

    def test_major_adaptation_requires_approval(
        self, wave_run_dir: Path, reader: FileStateReader, writer: FileStateWriter,
    ) -> None:
        # Mark S1 as failed (major trigger)
        manifest = reader.read_yaml(wave_run_dir / "shards" / "manifest.yaml")
        for s in manifest["shards"]:
            if s["id"] == "S1":
                s["status"] = "failed"
        writer.write_yaml(wave_run_dir / "shards" / "manifest.yaml", manifest)

        # Update wave status to failed
        wm = reader.read_yaml(wave_run_dir / "shards" / "wave_manifest.yaml")
        for w in wm["waves"]:
            if w["wave"] == 1:
                w["status"] = "failed"
        writer.write_yaml(wave_run_dir / "shards" / "wave_manifest.yaml", wm)

        result = _make_adapt_call(wave_run_dir, wave_number=1, auto_approve=True)
        assert result["status"] == "approval_required"
        assert result["severity"] == "major"


# ---------------------------------------------------------------------------
# Manifest versioning
# ---------------------------------------------------------------------------


class TestManifestVersioning:
    """Manifest version increments on each adaptation."""

    def test_version_increments(
        self, wave_run_dir: Path, reader: FileStateReader, writer: FileStateWriter,
    ) -> None:
        manifest = reader.read_yaml(wave_run_dir / "shards" / "manifest.yaml")
        for s in manifest["shards"]:
            if s["id"] == "S2":
                s["confidence"] = "low"
        writer.write_yaml(wave_run_dir / "shards" / "manifest.yaml", manifest)

        result = _make_adapt_call(wave_run_dir, wave_number=1)
        assert result["status"] == "adapted"
        assert result["version"] == 2

        wm = reader.read_yaml(wave_run_dir / "shards" / "wave_manifest.yaml")
        assert wm["version"] == 2
        assert len(wm["adaptation_history"]) == 1


# ---------------------------------------------------------------------------
# Safety constraints
# ---------------------------------------------------------------------------


class TestSafetyConstraints:
    """Safety budgets: max adaptations, max waves, max shards per adaptation."""

    def test_max_adaptations_budget_enforced(
        self, wave_run_dir: Path, reader: FileStateReader, writer: FileStateWriter,
    ) -> None:
        wm = reader.read_yaml(wave_run_dir / "shards" / "wave_manifest.yaml")
        wm["adaptation_history"] = [{"version": i} for i in range(5)]
        writer.write_yaml(wave_run_dir / "shards" / "wave_manifest.yaml", wm)

        result = _make_adapt_call(wave_run_dir, wave_number=1)
        assert result["status"] == "budget_exhausted"

    def test_max_total_waves_enforced(
        self, wave_run_dir: Path, reader: FileStateReader, writer: FileStateWriter,
    ) -> None:
        # Create 8 waves (at config max)
        wm = reader.read_yaml(wave_run_dir / "shards" / "wave_manifest.yaml")
        wm["waves"] = [
            {"wave": i, "shards": [], "status": "complete", "depends_on": []}
            for i in range(1, 9)
        ]
        # Wave 8 needs a shard with low confidence to trigger adaptation
        wm["waves"][-1]["shards"] = ["SX"]
        writer.write_yaml(wave_run_dir / "shards" / "wave_manifest.yaml", wm)

        sm = reader.read_yaml(wave_run_dir / "shards" / "manifest.yaml")
        sm["shards"].append({
            "id": "SX", "title": "X", "wave": 8,
            "status": "complete", "confidence": "low",
        })
        writer.write_yaml(wave_run_dir / "shards" / "manifest.yaml", sm)
        (wave_run_dir / "scratch" / "shard-SX").mkdir(parents=True, exist_ok=True)

        result = _make_adapt_call(wave_run_dir, wave_number=8)
        assert result["status"] == "max_waves_reached"

    def test_max_shards_per_adaptation_enforced(
        self, wave_run_dir: Path, reader: FileStateReader, writer: FileStateWriter,
    ) -> None:
        # Add extra shards to wave 1, all with low confidence
        wave1_shards = ["S1", "S2", "S4", "S5", "S6", "S7"]

        wm = reader.read_yaml(wave_run_dir / "shards" / "wave_manifest.yaml")
        for w in wm["waves"]:
            if w["wave"] == 1:
                w["shards"] = wave1_shards
        writer.write_yaml(wave_run_dir / "shards" / "wave_manifest.yaml", wm)

        manifest = reader.read_yaml(wave_run_dir / "shards" / "manifest.yaml")
        for sid in ["S4", "S5", "S6", "S7"]:
            manifest["shards"].append({
                "id": sid, "title": sid, "wave": 1,
                "status": "complete", "confidence": "low",
            })
            (wave_run_dir / "scratch" / f"shard-{sid}").mkdir(parents=True, exist_ok=True)
        for s in manifest["shards"]:
            s["confidence"] = "low"
        writer.write_yaml(wave_run_dir / "shards" / "manifest.yaml", manifest)

        result = _make_adapt_call(wave_run_dir, wave_number=1)
        assert result["status"] == "adapted"
        # Should cap at max_shards_added_per_adaptation (default: 3)
        assert result["shards_added"] <= 3
