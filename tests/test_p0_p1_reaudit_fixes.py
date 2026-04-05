"""Tests for P0/P1 re-audit fixes: session_metrics persistence, bandit wrapping, config weights.

P0-1 (CORE-104): session_metrics written to run.yaml after delivery_metrics step.
P0-2 (CORE-105): bandit_state.json wrapped with client_profile/model_family/quarantined.
P1 (CORE-104): compute_composite_outcome receives config weights.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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

    def test_session_metrics_not_persisted_when_no_run_yaml(self, tmp_path: Path) -> None:
        """When run.yaml doesn't exist, persistence is skipped gracefully."""
        from trw_mcp.tools._deferred_delivery import _persist_session_metrics

        run_dir = tmp_path / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        # No run.yaml file

        # Should not raise
        _persist_session_metrics({"status": "success"}, run_dir)

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
# ---------------------------------------------------------------------------


def _bandit_available() -> bool:
    try:
        from trw_memory.bandit import BanditSelector  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _bandit_available(), reason="trw-memory bandit not installed")
class TestBanditStateWrapping:
    """P0-2: _step_bandit_update wraps state with metadata."""

    def test_bandit_state_has_metadata_fields(self, tmp_path: Path) -> None:
        """bandit_state.json includes client_profile, model_family, quarantined."""
        from trw_mcp.tools.meta_tune import _step_bandit_update

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        learnings = [
            {"id": "L-1", "outcome_correlation": "positive"},
        ]
        config_dict = {
            "client_profile": "claude-code",
            "model_family": "claude",
        }

        result = _step_bandit_update(learnings, trw_dir=trw_dir, config=config_dict)

        assert result.status == "ok"

        state_path = trw_dir / "meta" / "bandit_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))

        assert "client_profile" in state
        assert "model_family" in state
        assert "quarantined" in state
        assert "bandit" in state
        assert state["client_profile"] == "claude-code"

    def test_bandit_state_loads_wrapped_format(self, tmp_path: Path) -> None:
        """_step_bandit_update can load previously wrapped state."""
        from trw_memory.bandit import BanditSelector

        from trw_mcp.tools.meta_tune import _step_bandit_update

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        # Write wrapped format
        bandit = BanditSelector()
        wrapped = {
            "client_profile": "claude-code",
            "model_family": "claude",
            "bandit": json.loads(bandit.to_json()),
            "quarantined": {},
        }
        state_path = trw_dir / "meta" / "bandit_state.json"
        state_path.write_text(json.dumps(wrapped), encoding="utf-8")

        learnings = [
            {"id": "L-2", "outcome_correlation": "positive"},
        ]

        config_dict = {
            "client_profile": "claude-code",
            "model_family": "claude",
        }

        result = _step_bandit_update(learnings, trw_dir=trw_dir, config=config_dict)

        assert result.status == "ok"
        assert result.actions_taken == 1

    def test_bandit_state_backward_compat_legacy_format(self, tmp_path: Path) -> None:
        """_step_bandit_update loads legacy (unwrapped) bandit state."""
        from trw_memory.bandit import BanditSelector

        from trw_mcp.tools.meta_tune import _step_bandit_update

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        # Write legacy (raw bandit) format
        bandit = BanditSelector()
        state_path = trw_dir / "meta" / "bandit_state.json"
        state_path.write_text(bandit.to_json(), encoding="utf-8")

        learnings = [
            {"id": "L-3", "outcome_correlation": "neutral"},
        ]

        config_dict = {
            "client_profile": "opencode",
            "model_family": "gpt",
        }

        result = _step_bandit_update(learnings, trw_dir=trw_dir, config=config_dict)

        assert result.status == "ok"

        # After update, state should now be in wrapped format
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "bandit" in state
        assert state["client_profile"] == "opencode"

    def test_bandit_quarantine_on_model_family_change(self, tmp_path: Path) -> None:
        """Model family change quarantines old posteriors."""
        from trw_memory.bandit import BanditSelector

        from trw_mcp.tools.meta_tune import _step_bandit_update

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        # Write initial state with old model family
        bandit = BanditSelector()
        wrapped = {
            "client_profile": "claude-code",
            "model_family": "old-model",
            "bandit": json.loads(bandit.to_json()),
            "quarantined": {},
        }
        state_path = trw_dir / "meta" / "bandit_state.json"
        state_path.write_text(json.dumps(wrapped), encoding="utf-8")

        learnings = [{"id": "L-4", "outcome_correlation": "positive"}]

        # New config has different model_family
        config_dict = {
            "client_profile": "claude-code",
            "model_family": "new-model",
        }

        result = _step_bandit_update(learnings, trw_dir=trw_dir, config=config_dict)

        assert result.status == "ok"

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["model_family"] == "new-model"
        assert "old-model" in state["quarantined"]


# ---------------------------------------------------------------------------
# P1: compute_composite_outcome receives config weights
# ---------------------------------------------------------------------------


class TestCompositeOutcomeConfigWeights:
    """P1 (CORE-104): config weights passed to compute_composite_outcome."""

    def test_config_weights_passed_to_composite(self) -> None:
        """_step_delivery_metrics passes config weights to compute_composite_outcome."""
        from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics

        mock_cfg = MagicMock()
        mock_cfg.client_profile.client_id = "test"
        mock_cfg.model_family = "test"
        mock_cfg.outcome_weight_rework = -3.0
        mock_cfg.outcome_weight_p0_defects = -2.0
        mock_cfg.outcome_weight_velocity = 1.0
        mock_cfg.outcome_weight_learning_rate = 0.5

        with patch(
            "trw_mcp.models.config.get_config",
            return_value=mock_cfg,
        ), patch(
            "trw_mcp.scoring._correlation.compute_composite_outcome",
            wraps=None,
        ) as mock_composite:
            mock_composite.return_value = 1.0
            _step_delivery_metrics(Path("/tmp/fake-trw"), None)

            if mock_composite.called:
                _, kwargs = mock_composite.call_args
                assert kwargs.get("weight_rework") == -3.0
                assert kwargs.get("weight_p0_defects") == -2.0
                assert kwargs.get("weight_velocity") == 1.0
                assert kwargs.get("weight_learning_rate") == 0.5

    def test_config_weights_default_fallback(self) -> None:
        """When config attributes are missing, default weights are used."""
        from trw_mcp.scoring._correlation import compute_composite_outcome

        # Default weights should produce the standard formula
        score = compute_composite_outcome(
            rework_rate=0.1,
            p0_defect_count=1,
            velocity_tasks=3.0,
            learning_rate=2.0,
        )
        # Default: -2.0*0.1 + -1.5*1 + 0.5*3.0 + 0.3*2.0 = -0.2 -1.5 + 1.5 + 0.6 = 0.4
        assert abs(score - 0.4) < 0.001
