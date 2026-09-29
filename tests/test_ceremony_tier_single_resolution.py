"""PRD-FIX-141-FR06 — one resolver, and a stated basis on every surface.

``trw_session_start`` reported ``resolved_profile.ceremony_tier: COMPREHENSIVE``
while the profile-explain tool reported ``STANDARD`` in the same run on the same
machine (learning L-Rikf). Both surfaces call ``resolve_session_profile``; the
old divergence came from a run-directory ``session_profile.yaml`` layer that
nothing in the product wrote and that no longer exists. What remains is the
contract: one resolver returns one tier for one config, whatever run directory
the caller has, and every surface states the basis it resolved from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.profile import build_explanation, resolution_basis, resolve_session_profile


def _run_dir(tmp_path: Path, stale_session_profile: str | None = None) -> Path:
    """A run directory, optionally carrying a leftover ``meta/session_profile.yaml``."""
    run_dir = tmp_path / "runs" / "task" / "20260916T000000Z-abcd"
    (run_dir / "meta").mkdir(parents=True)
    if stale_session_profile is not None:
        (run_dir / "meta" / "session_profile.yaml").write_text(stale_session_profile, encoding="utf-8")
    return run_dir


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    path.mkdir()
    return path


def test_the_same_basis_yields_the_same_tier_on_every_surface(tmp_path: Path, trw_dir: Path) -> None:
    """One config: session_start and profile_explain agree."""
    config = TRWConfig()
    run_dir = _run_dir(tmp_path)

    session_start_resolved = resolve_session_profile(config, trw_dir=trw_dir)
    explain_payload = build_explanation(resolve_session_profile(config, trw_dir=trw_dir), run_dir=run_dir)

    assert explain_payload["resolved_profile"]["ceremony_tier"] == session_start_resolved.profile.ceremony_tier  # type: ignore[index]
    assert resolution_basis(session_start_resolved, run_dir=run_dir) == explain_payload["profile_resolution_basis"]


def test_no_session_layer_is_ever_applied(trw_dir: Path) -> None:
    """The run-directory session layer is gone; the chain has no ``session`` entry."""
    resolved = resolve_session_profile(TRWConfig(), trw_dir=trw_dir)

    assert "session" not in resolved.layers_applied


def test_the_basis_names_the_run_dir_and_the_layers(tmp_path: Path, trw_dir: Path) -> None:
    """A reader must be able to see WHAT produced the tier, not just the tier."""
    config = TRWConfig()
    run_dir = _run_dir(tmp_path)
    resolved = resolve_session_profile(config, trw_dir=trw_dir)

    basis = resolution_basis(resolved, run_dir=run_dir)

    assert basis["run_dir"] == str(run_dir)
    assert basis["ceremony_tier"] == resolved.profile.ceremony_tier
    assert "defaults" in basis["layers_applied"]  # type: ignore[operator]
    assert "session_layer_present" not in basis


def test_session_start_emits_the_basis_beside_the_resolved_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production step, not the helper: the key must reach the payload."""
    from trw_mcp.models.typed_dicts._tools import SessionStartResultDict
    from trw_mcp.state import _paths
    from trw_mcp.tools._ceremony_profile_step import step_resolve_profile

    trw_path = tmp_path / ".trw"
    trw_path.mkdir()
    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: trw_path)
    run_dir = _run_dir(tmp_path)

    results: SessionStartResultDict = {}
    step_resolve_profile(TRWConfig(), run_dir, results)

    basis = results["profile_resolution_basis"]
    assert basis["ceremony_tier"] is not None
    assert results["resolved_profile"]["ceremony_tier"] == basis["ceremony_tier"]  # type: ignore[index]


def test_profile_explain_emits_the_identical_block(tmp_path: Path, trw_dir: Path) -> None:
    """Byte-identical basis blocks are what make the two reports comparable."""
    config = TRWConfig()
    run_dir = _run_dir(tmp_path)
    resolved = resolve_session_profile(config, trw_dir=trw_dir)

    payload = build_explanation(resolved, run_dir=run_dir)

    assert payload["profile_resolution_basis"] == resolution_basis(resolved, run_dir=run_dir)
