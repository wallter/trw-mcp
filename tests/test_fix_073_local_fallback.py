"""Tests for PRD-FIX-073: Local Ceremony Fallback for MCP Outages.

FR01: Local subcommand for trw-mcp CLI
FR02: Shared service layer (orchestration_service)
FR03: Instruction fallback guidance
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# FR02 — Shared service layer
# ---------------------------------------------------------------------------


class TestOrchestrationServiceScaffold:
    """FR02: scaffold_run_directory creates correct directory structure."""

    def test_creates_run_directory_structure(self, tmp_path: Path) -> None:
        """Creates meta/, reports/, scratch/_orchestrator/, shards/ subdirs."""
        from trw_mcp.services.orchestration_service import scaffold_run_directory

        result = scaffold_run_directory(
            "my-task",
            runs_root=tmp_path / "runs",
            trw_dir=tmp_path,
        )

        assert result["status"] == "initialized"
        assert result["run_id"]

        run_path = Path(result["run_path"])
        assert (run_path / "meta").is_dir()
        assert (run_path / "reports").is_dir()
        assert (run_path / "scratch" / "_orchestrator").is_dir()
        assert (run_path / "shards").is_dir()

    def test_creates_run_yaml(self, tmp_path: Path) -> None:
        """Creates meta/run.yaml with correct fields."""
        from trw_mcp.services.orchestration_service import scaffold_run_directory

        result = scaffold_run_directory(
            "test-task",
            runs_root=tmp_path / "runs",
            trw_dir=tmp_path,
        )

        run_path = Path(result["run_path"])
        run_yaml = json.loads((run_path / "meta" / "run.yaml").read_text())

        assert run_yaml["task"] == "test-task"
        assert run_yaml["status"] == "active"
        assert run_yaml["phase"] == "research"
        assert run_yaml["source"] == "local_cli"
        assert run_yaml["run_id"] == result["run_id"]

    def test_creates_events_jsonl(self, tmp_path: Path) -> None:
        """Creates meta/events.jsonl with run_init event."""
        from trw_mcp.services.orchestration_service import scaffold_run_directory

        result = scaffold_run_directory(
            "evt-task",
            runs_root=tmp_path / "runs",
            trw_dir=tmp_path,
        )

        run_path = Path(result["run_path"])
        events_path = run_path / "meta" / "events.jsonl"
        assert events_path.exists()

        events = [json.loads(line) for line in events_path.read_text().strip().split("\n")]
        assert len(events) == 1
        assert events[0]["type"] == "run_init"
        assert events[0]["task"] == "evt-task"

    def test_run_id_format(self, tmp_path: Path) -> None:
        """Run ID is timestamp + hex suffix."""
        from trw_mcp.services.orchestration_service import scaffold_run_directory

        result = scaffold_run_directory(
            "fmt-task",
            runs_root=tmp_path / "runs",
            trw_dir=tmp_path,
        )

        run_id = result["run_id"]
        # Format: YYYYMMDDTHHMMSSz-hexhexhexhex
        parts = run_id.split("-")
        assert len(parts) == 2
        assert parts[0].endswith("Z")
        assert len(parts[1]) == 8  # 4 bytes hex


class TestOrchestrationServiceCheckpoint:
    """FR02: write_checkpoint appends to checkpoints.jsonl correctly."""

    def test_writes_checkpoint_to_jsonl(self, tmp_path: Path) -> None:
        """Checkpoint is appended to meta/checkpoints.jsonl."""
        from trw_mcp.services.orchestration_service import (
            scaffold_run_directory,
            write_checkpoint,
        )

        scaffold = scaffold_run_directory(
            "cp-task",
            runs_root=tmp_path / "runs",
            trw_dir=tmp_path,
        )

        result = write_checkpoint(
            "first milestone done",
            run_path=Path(scaffold["run_path"]),
        )

        assert result["status"] == "checkpoint_created"
        assert result["message"] == "first milestone done"
        assert result["timestamp"]

        cp_path = Path(scaffold["run_path"]) / "meta" / "checkpoints.jsonl"
        records = [json.loads(line) for line in cp_path.read_text().strip().split("\n")]
        assert len(records) == 1
        assert records[0]["message"] == "first milestone done"

    def test_multiple_checkpoints_append(self, tmp_path: Path) -> None:
        """Multiple checkpoints are appended sequentially."""
        from trw_mcp.services.orchestration_service import (
            scaffold_run_directory,
            write_checkpoint,
        )

        scaffold = scaffold_run_directory(
            "multi-cp",
            runs_root=tmp_path / "runs",
            trw_dir=tmp_path,
        )
        run_path = Path(scaffold["run_path"])

        write_checkpoint("cp1", run_path=run_path)
        write_checkpoint("cp2", run_path=run_path)
        write_checkpoint("cp3", run_path=run_path)

        cp_path = run_path / "meta" / "checkpoints.jsonl"
        records = [json.loads(line) for line in cp_path.read_text().strip().split("\n")]
        assert len(records) == 3
        assert [r["message"] for r in records] == ["cp1", "cp2", "cp3"]

    def test_checkpoint_with_shard_and_wave(self, tmp_path: Path) -> None:
        """Shard and wave IDs are included when provided."""
        from trw_mcp.services.orchestration_service import (
            scaffold_run_directory,
            write_checkpoint,
        )

        scaffold = scaffold_run_directory(
            "sw-task",
            runs_root=tmp_path / "runs",
            trw_dir=tmp_path,
        )

        result = write_checkpoint(
            "with-ids",
            run_path=Path(scaffold["run_path"]),
            shard_id="shard-01",
            wave_id="wave-a",
        )

        cp_path = Path(scaffold["run_path"]) / "meta" / "checkpoints.jsonl"
        record = json.loads(cp_path.read_text().strip())
        assert record["shard_id"] == "shard-01"
        assert record["wave_id"] == "wave-a"

    def test_checkpoint_raises_on_missing_path(self, tmp_path: Path) -> None:
        """FileNotFoundError when explicit run_path doesn't exist."""
        from trw_mcp.services.orchestration_service import write_checkpoint

        with pytest.raises(FileNotFoundError):
            write_checkpoint("msg", run_path=tmp_path / "nonexistent")

    def test_checkpoint_auto_detect(self, tmp_path: Path) -> None:
        """Auto-detection finds the most recent run."""
        from trw_mcp.services.orchestration_service import (
            scaffold_run_directory,
            write_checkpoint,
        )

        scaffold = scaffold_run_directory(
            "auto-task",
            runs_root=tmp_path / ".trw" / "runs",
            trw_dir=tmp_path / ".trw",
        )

        with patch("trw_mcp.services.orchestration_service.Path.cwd", return_value=tmp_path):
            result = write_checkpoint("auto-detected")

        assert result["status"] == "checkpoint_created"


# ---------------------------------------------------------------------------
# FR01 — Local CLI subcommand
# ---------------------------------------------------------------------------


class TestLocalCLISubcommand:
    """FR01: trw-mcp local subcommand works via CLI."""

    def test_local_init_creates_run(self, tmp_path: Path) -> None:
        """trw-mcp local init --task NAME creates a run."""
        result = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local", "init", "--task", "cli-test"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0
        assert "Run initialized" in result.stdout

        # Verify directory was created
        runs_dir = tmp_path / ".trw" / "runs" / "cli-test"
        assert runs_dir.exists()

    def test_local_checkpoint_after_init(self, tmp_path: Path) -> None:
        """trw-mcp local checkpoint works after init."""
        # First init
        subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local", "init", "--task", "cp-cli"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        # Then checkpoint
        result = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local", "checkpoint", "--message", "step done"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0
        assert "Checkpoint created" in result.stdout

    def test_local_no_subcommand_shows_help(self) -> None:
        """trw-mcp local (no subcommand) shows usage."""
        result = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Usage:" in result.stdout or "init" in result.stdout


# ---------------------------------------------------------------------------
# FR03 — Instruction fallback guidance
# ---------------------------------------------------------------------------


class TestFallbackGuidance:
    """FR03: Rendered instructions include local fallback troubleshooting."""

    def test_closing_reminder_includes_fallback(self) -> None:
        """render_closing_reminder mentions the local CLI commands."""
        from trw_mcp.state.claude_md._static_sections import render_closing_reminder

        content = render_closing_reminder()

        assert "trw-mcp local init --task" in content
        assert "trw-mcp local checkpoint --message" in content
        assert "fetch failed" in content
        assert "Troubleshooting" in content

    def test_closing_reminder_includes_session_boundaries(self) -> None:
        """render_closing_reminder still includes session boundary text."""
        from trw_mcp.state.claude_md._static_sections import render_closing_reminder

        content = render_closing_reminder()

        assert "Session Boundaries" in content
        assert "trw_session_start()" in content
