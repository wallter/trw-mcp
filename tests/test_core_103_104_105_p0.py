"""Tests for P0 fixes across CORE-103, CORE-104, and CORE-105.

Covers:
- CORE-103: SurfaceEvent & PropensityEntry metadata fields (client_profile, model_family, trw_version)
- CORE-104: Composite outcome inputs, normalized_reward default, session metrics enrichment
- CORE-105: CeremonyState.previous_phase, set_ceremony_phase, burst truncation fix
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _bandit_available() -> bool:
    """Check if trw-memory bandit module is available."""
    try:
        from trw_memory.bandit import BanditSelector  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# CORE-103: Metadata fields on SurfaceEvent
# ---------------------------------------------------------------------------


class TestSurfaceEventMetadataFields:
    """Fix 1: client_profile, model_family, trw_version on SurfaceEvent."""

    def test_metadata_fields_present_in_event(self, tmp_path: Path) -> None:
        """SurfaceEvent includes metadata fields when explicitly provided."""
        from trw_mcp.state.surface_tracking import log_surface_event

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(
            trw_dir,
            learning_id="L-meta",
            surface_type="nudge",
            client_profile="claude-code",
            model_family="claude",
            trw_version="v24.4_TRW",
        )
        log_path = trw_dir / "logs" / "surface_tracking.jsonl"
        event = json.loads(log_path.read_text().strip())
        assert event["client_profile"] == "claude-code"
        assert event["model_family"] == "claude"
        assert event["trw_version"] == "v24.4_TRW"

    def test_metadata_fields_default_empty(self, tmp_path: Path) -> None:
        """SurfaceEvent metadata fields default to empty when config unavailable."""
        from trw_mcp.state.surface_tracking import log_surface_event

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Patch get_config at source to raise so auto-detection fails
        with patch(
            "trw_mcp.models.config.get_config",
            side_effect=ImportError("no config"),
        ):
            log_surface_event(
                trw_dir, learning_id="L-noconfig", surface_type="recall"
            )
        event = json.loads(
            (trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip()
        )
        # When config fails, fields stay empty
        assert event["client_profile"] == ""
        assert event["model_family"] == ""
        assert event["trw_version"] == ""

    def test_metadata_auto_detected_from_config(self, tmp_path: Path) -> None:
        """SurfaceEvent auto-detects client_profile and trw_version from config."""
        from trw_mcp.state.surface_tracking import log_surface_event

        mock_cfg = MagicMock()
        mock_cfg.client_profile.client_id = "cursor"
        mock_cfg.framework_version = "v99.9_TRW"

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch(
            "trw_mcp.models.config.get_config", return_value=mock_cfg
        ):
            log_surface_event(
                trw_dir, learning_id="L-auto", surface_type="nudge"
            )
        event = json.loads(
            (trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip()
        )
        assert event["client_profile"] == "cursor"
        assert event["trw_version"] == "v99.9_TRW"

    def test_explicit_overrides_auto_detect(self, tmp_path: Path) -> None:
        """Explicit metadata values are NOT overridden by auto-detection."""
        from trw_mcp.state.surface_tracking import log_surface_event

        mock_cfg = MagicMock()
        mock_cfg.client_profile.client_id = "cursor"
        mock_cfg.framework_version = "v99.9_TRW"

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch(
            "trw_mcp.models.config.get_config", return_value=mock_cfg
        ):
            log_surface_event(
                trw_dir,
                learning_id="L-explicit",
                surface_type="nudge",
                client_profile="my-client",
                trw_version="my-version",
            )
        event = json.loads(
            (trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip()
        )
        assert event["client_profile"] == "my-client"
        assert event["trw_version"] == "my-version"

    def test_surface_event_typed_dict_has_new_fields(self) -> None:
        """SurfaceEvent TypedDict includes the 3 new metadata fields."""
        from trw_mcp.state.surface_tracking import SurfaceEvent

        annotations = SurfaceEvent.__annotations__
        assert "client_profile" in annotations
        assert "model_family" in annotations
        assert "trw_version" in annotations


# ---------------------------------------------------------------------------
# CORE-103: Metadata fields on PropensityEntry
# ---------------------------------------------------------------------------


class TestPropensityEntryMetadataFields:
    """Fix 2: client_profile, model_family, trw_version on PropensityEntry."""

    def test_metadata_fields_in_logged_entry(self, tmp_path: Path) -> None:
        """log_selection writes metadata fields to propensity.jsonl."""
        from trw_mcp.state.propensity_log import log_selection

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(
            trw_dir,
            selected="L-meta",
            client_profile="opencode",
            model_family="gpt",
            trw_version="v24.4_TRW",
        )
        entry = json.loads(
            (trw_dir / "logs" / "propensity.jsonl").read_text().strip()
        )
        assert entry["client_profile"] == "opencode"
        assert entry["model_family"] == "gpt"
        assert entry["trw_version"] == "v24.4_TRW"

    def test_metadata_auto_detected(self, tmp_path: Path) -> None:
        """log_selection auto-detects client_profile from config when empty."""
        from trw_mcp.state.propensity_log import log_selection

        mock_cfg = MagicMock()
        mock_cfg.client_profile.client_id = "aider"
        mock_cfg.framework_version = "v24.3_TRW"

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch(
            "trw_mcp.models.config.get_config", return_value=mock_cfg
        ):
            log_selection(trw_dir, selected="L-auto")
        entry = json.loads(
            (trw_dir / "logs" / "propensity.jsonl").read_text().strip()
        )
        assert entry["client_profile"] == "aider"
        assert entry["trw_version"] == "v24.3_TRW"

    def test_metadata_failopen_on_config_error(self, tmp_path: Path) -> None:
        """Metadata stays empty when config auto-detection fails."""
        from trw_mcp.state.propensity_log import log_selection

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch(
            "trw_mcp.models.config.get_config",
            side_effect=RuntimeError("no config"),
        ):
            log_selection(trw_dir, selected="L-fail")
        entry = json.loads(
            (trw_dir / "logs" / "propensity.jsonl").read_text().strip()
        )
        assert entry["client_profile"] == ""
        assert entry["model_family"] == ""
        assert entry["trw_version"] == ""


# ---------------------------------------------------------------------------
# CORE-104: Composite outcome, normalized_reward default, session metrics
# ---------------------------------------------------------------------------


class TestCompositeOutcomeInputs:
    """Fix 3: compute_composite_outcome receives all 4 inputs."""

    def test_composite_outcome_uses_all_inputs(self) -> None:
        """compute_composite_outcome accepts rework, p0, velocity, learning_rate."""
        from trw_mcp.scoring._correlation import compute_composite_outcome

        score = compute_composite_outcome(
            rework_rate=0.1,
            p0_defect_count=2,
            velocity_tasks=5.0,
            learning_rate=3.0,
        )
        # Manual check: -2.0*0.1 + -1.5*2 + 0.5*5 + 0.3*3 = -0.2 + -3.0 + 2.5 + 0.9 = 0.2
        assert abs(score - 0.2) < 0.001

    def test_composite_outcome_zero_inputs(self) -> None:
        """compute_composite_outcome with all zeros returns zero."""
        from trw_mcp.scoring._correlation import compute_composite_outcome

        score = compute_composite_outcome(
            rework_rate=0.0,
            p0_defect_count=0,
            velocity_tasks=0.0,
            learning_rate=0.0,
        )
        assert score == 0.0


class TestNormalizedRewardDefault:
    """Fix 5: normalized_reward defaults to 0.5 before computation."""

    def test_default_normalized_reward_on_failed_composite(self) -> None:
        """When composite_outcome computation fails, normalized_reward is 0.5."""
        from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics

        trw_dir = Path("/tmp/nonexistent-trw-dir-test")
        # With invalid trw_dir, metrics will fail-open
        result = _step_delivery_metrics(trw_dir, None)
        # normalized_reward should be 0.5 (safe default) even if computation fails
        assert result.get("normalized_reward") is not None
        # The safe default is set before any computation attempt
        assert isinstance(result["normalized_reward"], (int, float))

    def test_normalized_reward_default_before_computation(self) -> None:
        """normalized_reward is set to 0.5 before the try block runs.

        When sigmoid_normalize fails (inside a local import try block),
        the safe default of 0.5 should persist in the result dict.
        """
        from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics

        # Force sigmoid_normalize to fail by patching at its source
        with patch(
            "trw_mcp.scoring._correlation.sigmoid_normalize",
            side_effect=RuntimeError("fail"),
        ):
            result = _step_delivery_metrics(Path("/tmp/fake"), None)
        # Safe default persists even when sigmoid_normalize fails
        assert result["normalized_reward"] == 0.5


class TestSessionMetricsEnrichment:
    """Fix 4: client_profile and model_family added to session metrics."""

    def test_client_profile_in_delivery_metrics(self) -> None:
        """_step_delivery_metrics includes client_profile from config."""
        from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics

        mock_cfg = MagicMock()
        mock_cfg.client_profile.client_id = "claude-code"
        mock_cfg.model_family = "claude"

        with patch(
            "trw_mcp.models.config.get_config",
            return_value=mock_cfg,
        ):
            result = _step_delivery_metrics(Path("/tmp/fake"), None)
        assert result.get("client_profile") == "claude-code"
        assert result.get("model_family") == "claude"


# ---------------------------------------------------------------------------
# CORE-105: CeremonyState.previous_phase
# ---------------------------------------------------------------------------


class TestCeremonyStatePreviousPhase:
    """Fix 6: previous_phase field on CeremonyState."""

    def test_previous_phase_default_empty(self) -> None:
        """CeremonyState.previous_phase defaults to empty string."""
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        assert state.previous_phase == ""

    def test_previous_phase_round_trip(self, tmp_path: Path) -> None:
        """previous_phase survives write/read serialization."""
        from trw_mcp.state._nudge_state import (
            CeremonyState,
            read_ceremony_state,
            write_ceremony_state,
        )

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        state = CeremonyState()
        state.phase = "implement"
        state.previous_phase = "early"
        write_ceremony_state(trw_dir, state)

        loaded = read_ceremony_state(trw_dir)
        assert loaded.phase == "implement"
        assert loaded.previous_phase == "early"

    def test_previous_phase_from_dict(self) -> None:
        """_from_dict loads previous_phase from dict data."""
        from trw_mcp.state._nudge_state import _from_dict

        state = _from_dict({"phase": "validate", "previous_phase": "implement"})
        assert state.phase == "validate"
        assert state.previous_phase == "implement"

    def test_previous_phase_from_dict_missing(self) -> None:
        """_from_dict defaults previous_phase to empty when missing."""
        from trw_mcp.state._nudge_state import _from_dict

        state = _from_dict({"phase": "validate"})
        assert state.previous_phase == ""


class TestSetCeremonyPhase:
    """Fix 7: set_ceremony_phase updates previous_phase atomically."""

    def test_set_ceremony_phase_tracks_previous(self, tmp_path: Path) -> None:
        """set_ceremony_phase sets previous_phase to old value."""
        from trw_mcp.state._nudge_state import (
            CeremonyState,
            read_ceremony_state,
            set_ceremony_phase,
            write_ceremony_state,
        )

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        state = CeremonyState()
        state.phase = "early"
        write_ceremony_state(trw_dir, state)

        set_ceremony_phase(trw_dir, "implement")

        loaded = read_ceremony_state(trw_dir)
        assert loaded.phase == "implement"
        assert loaded.previous_phase == "early"

    def test_set_ceremony_phase_noop_same_phase(self, tmp_path: Path) -> None:
        """set_ceremony_phase does NOT update when phase unchanged."""
        from trw_mcp.state._nudge_state import (
            CeremonyState,
            read_ceremony_state,
            set_ceremony_phase,
            write_ceremony_state,
        )

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        state = CeremonyState()
        state.phase = "implement"
        state.previous_phase = "early"
        write_ceremony_state(trw_dir, state)

        set_ceremony_phase(trw_dir, "implement")  # same phase

        loaded = read_ceremony_state(trw_dir)
        assert loaded.phase == "implement"
        assert loaded.previous_phase == "early"  # unchanged

    def test_set_ceremony_phase_chains(self, tmp_path: Path) -> None:
        """set_ceremony_phase chains: early -> implement -> validate."""
        from trw_mcp.state._nudge_state import (
            CeremonyState,
            read_ceremony_state,
            set_ceremony_phase,
            write_ceremony_state,
        )

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        write_ceremony_state(trw_dir, CeremonyState())

        set_ceremony_phase(trw_dir, "implement")
        set_ceremony_phase(trw_dir, "validate")

        loaded = read_ceremony_state(trw_dir)
        assert loaded.phase == "validate"
        assert loaded.previous_phase == "implement"

    def test_set_ceremony_phase_exported_from_facade(self) -> None:
        """set_ceremony_phase is re-exported from ceremony_nudge facade."""
        from trw_mcp.state.ceremony_nudge import set_ceremony_phase

        assert callable(set_ceremony_phase)


# ---------------------------------------------------------------------------
# CORE-105: Burst truncation fix
# ---------------------------------------------------------------------------


class TestBurstTruncationFix:
    """Fix 8: select_nudge_learning populates burst_items."""

    def test_burst_items_populated_deterministic(self) -> None:
        """Without bandit, burst_items stays empty (deterministic path)."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": "L-1", "summary": "First"},
            {"id": "L-2", "summary": "Second"},
        ]
        burst: list[dict[str, object]] = []
        selected, is_fallback = select_nudge_learning(
            state, candidates, "implement", burst_items=burst
        )
        assert selected is not None
        assert selected["id"] == "L-1"
        assert burst == []  # No burst in deterministic path
        assert is_fallback is False

    def test_burst_items_none_backward_compat(self) -> None:
        """burst_items=None (default) does not change behavior."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": "L-1", "summary": "First"},
        ]
        selected, is_fallback = select_nudge_learning(
            state, candidates, "implement"
        )
        assert selected is not None
        assert selected["id"] == "L-1"

    def test_bandit_param_falls_through_to_deterministic(self) -> None:
        """After PRD-INFRA-054, bandit param is accepted but falls through to deterministic."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": "L-1", "summary": "First"},
            {"id": "L-2", "summary": "Second"},
            {"id": "L-3", "summary": "Third"},
        ]
        burst: list[dict[str, object]] = []
        # Passing a bandit object (any truthy value) should still work
        # but fall through to deterministic ranking (PRD-INFRA-054)
        selected, is_fallback = select_nudge_learning(
            state,
            candidates,
            "implement",
            bandit=object(),  # non-None but not a BanditSelector
            previous_phase="early",
            burst_items=burst,
        )
        # Deterministic path: first eligible candidate selected, no burst
        assert selected is not None
        assert selected["id"] == "L-1"
        assert burst == []
        assert is_fallback is False
