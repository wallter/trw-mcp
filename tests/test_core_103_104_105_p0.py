"""Tests for P0 fixes across CORE-103, CORE-104, and CORE-105.

Covers:
- CORE-103: SurfaceEvent & PropensityEntry metadata fields (client_profile, model_family, trw_version)
- CORE-104: session metrics enrichment
- CORE-105: CeremonyState.previous_phase, set_ceremony_phase, burst truncation fix
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
            log_surface_event(trw_dir, learning_id="L-noconfig", surface_type="recall")
        event = json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
        # When config fails, fields stay empty
        assert event["client_profile"] == ""
        assert event["model_family"] == ""
        assert event["trw_version"] == ""

    def test_metadata_auto_detected_from_config(self, tmp_path: Path) -> None:
        """SurfaceEvent auto-detects client_profile and trw_version from config."""
        from trw_mcp.state.surface_tracking import log_surface_event

        mock_cfg = MagicMock()
        mock_cfg.client_profile.client_id = "cursor-ide"
        mock_cfg.framework_version = "v99.9_TRW"

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.models.config.get_config", return_value=mock_cfg):
            log_surface_event(trw_dir, learning_id="L-auto", surface_type="nudge")
        event = json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
        assert event["client_profile"] == "cursor-ide"
        assert event["trw_version"] == "v99.9_TRW"

    def test_explicit_overrides_auto_detect(self, tmp_path: Path) -> None:
        """Explicit metadata values are NOT overridden by auto-detection."""
        from trw_mcp.state.surface_tracking import log_surface_event

        mock_cfg = MagicMock()
        mock_cfg.client_profile.client_id = "cursor-ide"
        mock_cfg.framework_version = "v99.9_TRW"

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.models.config.get_config", return_value=mock_cfg):
            log_surface_event(
                trw_dir,
                learning_id="L-explicit",
                surface_type="nudge",
                client_profile="my-client",
                trw_version="my-version",
            )
        event = json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
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
# CORE-104: session metrics enrichment
# ---------------------------------------------------------------------------


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

        state = _from_dict({"phase": "validate", "previous_phase": "implement", "pool_cooldowns": {}})
        assert state.phase == "validate"
        assert state.previous_phase == "implement"

    def test_previous_phase_from_dict_missing(self) -> None:
        """_from_dict defaults previous_phase to empty when missing."""
        from trw_mcp.state._nudge_state import _from_dict

        state = _from_dict({"phase": "validate", "pool_cooldowns": {}})
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
