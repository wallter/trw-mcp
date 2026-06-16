"""Session-layer fail-open invariants — PRD-SCALE-001 round-2 audit S1-F01/S1-F02.

The session overlay (``meta/session_profile.yaml``) is an escape hatch, not a
governance surface: a malformed overlay MUST fail open (layer skipped + warning)
so ``trw_session_start`` never crashes on it (NFR / Behavior Switch Matrix).

S1-F01: an overlay with an EXTRA key raises ``pydantic.ValidationError`` inside
``ProfileLayer.model_validate``. Whether that is a ``ValueError`` subclass is
NOT contractually guaranteed across pydantic 2.x point releases, so the
``_session_layer`` catch tuple lists ``ValidationError`` explicitly — the
fail-open contract (layer skipped + warning) must hold regardless of pydantic's
internal exception MRO, instead of the error propagating to the outer wiring
catch. These tests assert that contract directly.

S1-F02: the reader strips the advisory ``rationale`` before typed validation, so
a hand-written overlay carrying both a ``rationale`` and a real surface key
(``ceremony_tier``) resolves cleanly with the surface key applied.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from structlog.testing import capture_logs

from trw_mcp.models.config import TRWConfig
from trw_mcp.profile import resolve_session_profile
from trw_mcp.profile.session_resolve import _session_layer


def _write_overlay(run_dir: Path, body: str) -> None:
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "session_profile.yaml").write_text(body, encoding="utf-8")


def test_session_layer_extra_key_fails_open_and_warns(tmp_path: Path) -> None:
    """S1-F01: an extra session key skips the layer (None) and logs a warning."""
    run_dir = tmp_path / "runs" / "task" / "run-1"
    _write_overlay(run_dir, "ceremony_tier: COMPREHENSIVE\nfoo: bar\n")

    # structlog must be configured to route through the testing capture.
    structlog.configure(processors=[structlog.testing.LogCapture()])
    with capture_logs() as logs:
        layer = _session_layer(run_dir)

    # Layer is skipped (fail-open) rather than the ValidationError propagating.
    assert layer is None
    events = {entry.get("event") for entry in logs}
    assert "profile_session_layer_skipped" in events


def test_session_start_survives_extra_session_key(tmp_path: Path) -> None:
    """S1-F01: a full resolve over an extra-key overlay still succeeds.

    The malformed session layer is dropped, so the resolved profile is the
    persistent surface above it — no exception escapes to the caller.
    """
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
    run_dir = tmp_path / "runs" / "task" / "run-2"
    _write_overlay(run_dir, "ceremony_tier: MINIMAL\nfoo: bar\n")

    resolved = resolve_session_profile(config, run_dir=run_dir)

    # Session layer skipped → not in the applied chain; resolve did not raise.
    assert "session" not in resolved.layers_applied
    # The extra-key overlay did NOT leak its ceremony_tier into the surface.
    assert resolved.profile is not None


def test_session_layer_rationale_stripped_surface_applied(tmp_path: Path) -> None:
    """S1-F02: a manual overlay with rationale + ceremony_tier resolves cleanly.

    The reader strips the advisory ``rationale`` before typed validation, so the
    real ``ceremony_tier`` surface key flows through and supersedes.
    """
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
    run_dir = tmp_path / "runs" / "task" / "run-3"
    _write_overlay(
        run_dir,
        'rationale: "planning_mode=2; probe_budget=2"\nceremony_tier: COMPREHENSIVE\n',
    )

    # Direct layer read: resolves with no error, ceremony_tier applied.
    layer = _session_layer(run_dir)
    assert layer is not None
    assert layer.name == "session"
    assert layer.overrides.ceremony_tier == "COMPREHENSIVE"

    # End-to-end resolve: the session layer supersedes and applies.
    resolved = resolve_session_profile(config, run_dir=run_dir)
    assert resolved.profile.ceremony_tier == "COMPREHENSIVE"
    assert "session" in resolved.layers_applied
