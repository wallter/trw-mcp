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

import pytest

# ---------------------------------------------------------------------------
# FR02 — Shared service layer
# ---------------------------------------------------------------------------


def _run_path_from_init(stdout: str) -> str:
    """Pull the run directory out of ``trw-mcp local init`` output.

    The printed path IS the handoff (PRD-FIX-132): init is the only offline
    command that can establish a run identity for a caller that has none.
    """
    for line in stdout.splitlines():
        if line.strip().startswith("Path:"):
            return line.split("Path:", 1)[1].strip()
    raise AssertionError(f"local init printed no run path:\n{stdout}")


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
        from trw_mcp.state.persistence import FileStateReader

        run_yaml = FileStateReader().read_yaml(run_path / "meta" / "run.yaml")

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

    def test_checkpoint_without_identity_refuses(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-132: no --run-path and no pin is a refusal, not a guess.

        This test previously asserted the opposite -- that the newest run on
        disk was auto-detected -- which is why nothing caught a pinless agent
        checkpointing into another session's run. The run scaffolded below is
        the only one present, and it is still not selected: being the only
        candidate is not evidence of ownership either.
        """
        from trw_mcp.services._local_run_identity import LocalRunIdentityError
        from trw_mcp.services.orchestration_service import (
            scaffold_run_directory,
            write_checkpoint,
        )

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.delenv("TRW_SESSION_ID", raising=False)
        scaffold = scaffold_run_directory(
            "auto-task",
            runs_root=tmp_path / ".trw" / "runs",
            trw_dir=tmp_path / ".trw",
        )

        with pytest.raises(LocalRunIdentityError) as exc_info:
            write_checkpoint("auto-detected")

        assert "--run-path" in str(exc_info.value)
        assert not (Path(scaffold["run_path"]) / "meta" / "checkpoints.jsonl").exists()


class TestOrchestrationServiceLearnParity:
    """write_local_learning mirrors the trw_learn MCP tool's contract.

    PRD-CORE-247 offline-parity fix: type/confidence/impact/evidence were
    accepted by the online tool but silently unreachable offline.
    """

    def test_forwards_type_confidence_impact_evidence_to_execute_learn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each new field reaches execute_learn unchanged — the payload mapping."""
        import trw_mcp.tools._learn_impl as _learn_impl
        from trw_mcp.services import orchestration_service

        captured: dict[str, object] = {}

        def _fake_execute_learn(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "recorded", "learning_id": "L-test", "path": "sqlite://L-test"}

        monkeypatch.setattr(_learn_impl, "execute_learn", _fake_execute_learn)

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = orchestration_service.write_local_learning(
            "summary text",
            "detail text",
            trw_dir=trw_dir,
            tags=["t1"],
            evidence=["path/to/file.py:10", "log excerpt"],
            impact=0.9,
            type="incident",
            confidence="high",
        )

        assert result["status"] == "recorded"
        assert captured["evidence"] == ["path/to/file.py:10", "log excerpt"]
        assert captured["impact"] == pytest.approx(0.9)
        assert captured["type"] == "incident"
        assert captured["confidence"] == "high"

    def test_invalid_type_is_rejected_not_raised(self, tmp_path: Path) -> None:
        """An out-of-range type returns the same structured rejection trw_learn does."""
        from trw_mcp.services.orchestration_service import write_local_learning

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = write_local_learning("summary text", "detail text", trw_dir=trw_dir, type="not-a-real-type")

        assert result["status"] == "rejected"
        assert result["reason"] == "invalid_type"

    def test_verified_without_evidence_is_refused(self, tmp_path: Path) -> None:
        """confidence='verified' still requires substantiation (reused validation).

        Enforced by trw-memory's own write-time schema contract
        (SchemaValidationError), the same gate the MCP tool goes through --
        not re-implemented in the offline path.
        """
        from trw_memory.exceptions import SchemaValidationError

        from trw_mcp.services.orchestration_service import write_local_learning

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with pytest.raises(SchemaValidationError):
            write_local_learning(
                "summary text long enough to pass the noise filter",
                "detail text",
                trw_dir=trw_dir,
                confidence="verified",
            )


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
        """trw-mcp local checkpoint works after init, threading the printed run path.

        PRD-FIX-132: ``init`` prints the run it created and the caller passes it
        back. That is the flow the degraded-mode protocol block now instructs,
        replacing the auto-detect this test used to rely on.
        """
        init = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local", "init", "--task", "cp-cli"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        run_path = _run_path_from_init(init.stdout)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "trw_mcp.server",
                "local",
                "checkpoint",
                "--message",
                "step done",
                "--run-path",
                run_path,
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Checkpoint created" in result.stdout

    def test_local_status_and_deliver_after_init(self, tmp_path: Path) -> None:
        """trw-mcp local status/deliver work without MCP transport."""
        init = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local", "init", "--task", "deliver-cli"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            check=True,
        )
        run_path = _run_path_from_init(init.stdout)

        status = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local", "status", "--run-path", run_path],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert status.returncode == 0, status.stdout + status.stderr
        assert "Status: active" in status.stdout

        delivered = subprocess.run(
            [
                sys.executable,
                "-m",
                "trw_mcp.server",
                "local",
                "deliver",
                "--message",
                "done",
                "--run-path",
                run_path,
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert delivered.returncode == 0, delivered.stdout + delivered.stderr
        assert "Run delivered" in delivered.stdout

        after = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "local", "status", "--run-path", run_path],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert "Status: delivered" in after.stdout

    def test_local_learn_persists_without_transport(self, tmp_path: Path) -> None:
        """trw-mcp local learn writes through the learning implementation."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "trw_mcp.server",
                "local",
                "learn",
                "--summary",
                "Local fallback test learning",
                "--detail",
                "Validated local CLI learn path for transport outages.",
                "--tag",
                "test",
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0, result.stderr
        assert "Learning" in result.stdout
        assert any((tmp_path / ".trw" / "memory").glob("**/*.yaml"))

    def test_local_learn_records_type_confidence_impact_and_evidence(self, tmp_path: Path) -> None:
        """The trw_learn-parity flags round-trip through the offline CLI (PRD-CORE-247)."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "trw_mcp.server",
                "local",
                "learn",
                "--summary",
                "Local fallback parity learning",
                "--detail",
                "Proves --type/--confidence/--impact/--evidence round-trip offline.",
                "--type",
                "incident",
                "--confidence",
                "high",
                "--impact",
                "0.9",
                "--evidence",
                "trw-mcp/src/trw_mcp/services/orchestration_service.py:210",
                "--evidence",
                "second evidence item",
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0, result.stderr
        assert "Learning" in result.stdout
        entry_files = list((tmp_path / ".trw" / "learnings" / "entries").glob("*.yaml"))
        assert entry_files, "no learning entry sidecar was written"
        entry_text = entry_files[0].read_text(encoding="utf-8")
        assert "type: incident" in entry_text
        assert "confidence: high" in entry_text
        assert "second evidence item" in entry_text

    def test_local_learn_verified_without_evidence_fails_cleanly(self, tmp_path: Path) -> None:
        """confidence=verified with no evidence is a clean error, not a traceback."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "trw_mcp.server",
                "local",
                "learn",
                "--summary",
                "Local fallback unsubstantiated verified learning",
                "--detail",
                "This claims verified confidence with no substantiation at all.",
                "--confidence",
                "verified",
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        # A clean, structured rejection message on stdout with a non-zero exit —
        # never an unhandled-exception traceback out of the CLI process itself
        # (structlog WARNING noise on stderr, e.g. an offline embedder falling
        # back, is unrelated background logging, not a crash).
        assert result.returncode != 0
        assert "Error:" in result.stdout
        assert "verified" in result.stdout
        assert "TRW MCP CRASH" not in result.stderr

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
