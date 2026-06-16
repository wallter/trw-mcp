"""H2 ↔ SCALE-001 integration — Scout session overlay supersedes (FR03).

This is the seam wiring test declared in PRD-SCALE-001 frontmatter:
``test_h2_integration.py::test_session_profile_supersedes``. It proves the
Scout's ``session_profile.yaml`` (FR03) is actually CONSUMED by the live H2
profile resolver as the session-layer overlay — the real cross-PRD path, not
a mock.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.cognitive_scaling import classify, write_session_profile
from trw_mcp.models.cognitive_scaling import PlanningMode
from trw_mcp.models.config import TRWConfig
from trw_mcp.profile import resolve_session_profile


def test_session_profile_supersedes(tmp_path: Path) -> None:
    """FR03: Scout's session overlay beats the persistent surface in H2.

    The defaults layer projects a STANDARD ceremony tier (from the client
    ceremony mode); a DIRECT Scout writes MINIMAL into the session overlay.
    After resolution the EFFECTIVE ceremony_tier must be MINIMAL — proving the
    session layer (written by Scout) supersedes the defaults/persistent layer.
    """
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
    run_dir = tmp_path / "runs" / "task" / "run-1"

    # Scout downgrades a low-blast task to DIRECT/MINIMAL and writes the overlay.
    classification = classify(
        task_description="typo fix",
        project_root=tmp_path,
        trw_dir=tmp_path / ".trw",
        override_mode=PlanningMode.DIRECT,
    )
    path = write_session_profile(classification, run_dir=run_dir)
    assert path is not None and path.exists()

    # The live H2 resolver reads the overlay as the session layer.
    resolved = resolve_session_profile(config, run_dir=run_dir)

    assert resolved.profile.ceremony_tier == "MINIMAL"
    assert "session" in resolved.layers_applied
    # The session-layer delta is recorded separately from the persistent hash.
    assert resolved.session_override_hash != ""


def test_escalated_session_profile_supersedes(tmp_path: Path) -> None:
    """FR03: an escalated Scout overlay (COMPREHENSIVE) also wins in H2."""
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
    run_dir = tmp_path / "runs" / "task" / "run-2"

    classification = classify(
        task_description="cross-module refactor",
        project_root=tmp_path,
        trw_dir=tmp_path / ".trw",
        override_mode=PlanningMode.TRIANGULATED,
    )
    write_session_profile(classification, run_dir=run_dir)

    resolved = resolve_session_profile(config, run_dir=run_dir)
    assert resolved.profile.ceremony_tier == "COMPREHENSIVE"
    assert "session" in resolved.layers_applied


def test_no_session_profile_keeps_persistent_surface(tmp_path: Path) -> None:
    """No Scout overlay -> session layer absent (resolver fail-open)."""
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
    run_dir = tmp_path / "runs" / "task" / "run-3"
    run_dir.mkdir(parents=True)

    resolved = resolve_session_profile(config, run_dir=run_dir)
    assert "session" not in resolved.layers_applied
