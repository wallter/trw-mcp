"""Tests for P0 re-audit fixes: session_metrics persistence.

P0-1 (CORE-104): session_metrics written to run.yaml after delivery_metrics step.
P0-2 (CORE-105): bandit_state.json tests removed (PRD-INFRA-054 -- meta_tune deleted).
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# P0-1: session_metrics persistence to run.yaml
# ---------------------------------------------------------------------------


class TestSessionMetricsPersistence:
    """P0-1: _run_deferred_steps writes session_metrics to run.yaml."""

    def test_delivery_metrics_persisted_to_run_yaml(self, tmp_path: Path) -> None:
        """After delivery_metrics step, results are written to run.yaml."""
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Create a resolved run with a run.yaml
        run_dir = tmp_path / "runs" / "test-run"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)

        writer = FileStateWriter()
        run_yaml_path = meta_dir / "run.yaml"
        writer.write_yaml(run_yaml_path, {"task": "test", "phase": "deliver"})

        # Import the function
        from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics

        # Create a fake metrics result that would be returned by _step_delivery_metrics
        metrics_result = _step_delivery_metrics(trw_dir, run_dir)

        # Now simulate what _run_deferred_steps should do:
        # persist session_metrics to run.yaml
        from trw_mcp.tools._deferred_delivery import _persist_session_metrics

        _persist_session_metrics(metrics_result, run_dir)

        reader = FileStateReader()
        run_data = reader.read_yaml(run_yaml_path)
        assert "session_metrics" in run_data
        assert run_data["session_metrics"]["status"] == "success"

    def test_session_metrics_not_persisted_when_no_run(self, tmp_path: Path) -> None:
        """When resolved_run is None, persistence is skipped gracefully."""
        from trw_mcp.tools._deferred_delivery import _persist_session_metrics

        # Should not raise
        _persist_session_metrics({"status": "success"}, None)
        assert list(tmp_path.iterdir()) == []

    def test_session_metrics_not_persisted_when_no_run_yaml(self, tmp_path: Path) -> None:
        """When run.yaml doesn't exist, persistence is skipped gracefully."""
        from trw_mcp.tools._deferred_delivery import _persist_session_metrics

        run_dir = tmp_path / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        # No run.yaml file

        # Should not raise
        _persist_session_metrics({"status": "success"}, run_dir)
        assert list(run_dir.iterdir()) == []

    def test_session_metrics_not_persisted_on_failure(self, tmp_path: Path) -> None:
        """When metrics step fails (status != success), skip persistence."""
        from trw_mcp.tools._deferred_delivery import _persist_session_metrics

        run_dir = tmp_path / "runs" / "test-run"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)

        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        run_yaml_path = meta_dir / "run.yaml"
        writer.write_yaml(run_yaml_path, {"task": "test"})

        _persist_session_metrics({"status": "error"}, run_dir)

        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        run_data = reader.read_yaml(run_yaml_path)
        assert "session_metrics" not in run_data


# ---------------------------------------------------------------------------
# P0-2: bandit_state.json wrapped format
# PRD-INFRA-054: TestBanditStateWrapping class removed -- _step_bandit_update
# was in tools/meta_tune.py which was deleted (intelligence code extracted
# to backend in PRD-INFRA-052).
# ---------------------------------------------------------------------------
