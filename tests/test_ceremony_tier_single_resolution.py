"""PRD-FIX-141-FR06 — one resolver, and a stated basis when two reports differ.

``trw_session_start`` reported ``resolved_profile.ceremony_tier: COMPREHENSIVE``
while ``trw_profile_explain`` reported ``STANDARD`` in the same run on the same
machine (learning L-Rikf).

The root cause is ORDERING, not two resolvers: both surfaces already call
``resolve_session_profile``. ``trw_session_start`` runs BEFORE ``trw_init``, so
no run directory — and therefore no Scout-written ``meta/session_profile.yaml``
— existed yet, and the tier came from the defaults layer. ``trw_profile_explain``
ran afterwards and read the session layer. Verified against the audit run: the
run directory's ``session_profile.yaml`` (``ceremony_tier: STANDARD``) was
written 61 seconds after the run id's own timestamp.

So the fix is not a fourth resolver. It is (a) proving the one resolver returns
the same value for the same basis, and (b) making every surface state the basis
so "the inputs changed" can never again read as "the surfaces disagree".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.profile import build_explanation, resolution_basis, resolve_session_profile


def _run_dir(tmp_path: Path, tier: str | None) -> Path:
    """A run directory, optionally carrying the Scout's session profile layer."""
    run_dir = tmp_path / "runs" / "task" / "20260916T000000Z-abcd"
    (run_dir / "meta").mkdir(parents=True)
    if tier is not None:
        (run_dir / "meta" / "session_profile.yaml").write_text(
            f"ceremony_tier: {tier}\nrationale: planning_mode=1\n", encoding="utf-8"
        )
    return run_dir


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    path.mkdir()
    return path


def test_the_same_basis_yields_the_same_tier_on_every_surface(tmp_path: Path, trw_dir: Path) -> None:
    """One run directory, one config: session_start and profile_explain agree."""
    config = TRWConfig()
    run_dir = _run_dir(tmp_path, "STANDARD")

    session_start_resolved = resolve_session_profile(config, run_dir=run_dir, trw_dir=trw_dir)
    explain_payload = build_explanation(
        resolve_session_profile(config, run_dir=run_dir, trw_dir=trw_dir), run_dir=run_dir
    )

    assert session_start_resolved.profile.ceremony_tier == "STANDARD"
    assert explain_payload["resolved_profile"]["ceremony_tier"] == "STANDARD"  # type: ignore[index]
    assert resolution_basis(session_start_resolved, run_dir=run_dir) == explain_payload["profile_resolution_basis"]


def test_the_observed_divergence_is_reproduced_and_explained(tmp_path: Path, trw_dir: Path) -> None:
    """The 2026-09-16 pair, from one resolver: no run dir, then a run dir.

    This is the test that would have turned "two surfaces contradict each other"
    into "the session layer arrived between the two calls" on the day.
    """
    config = TRWConfig()
    run_dir = _run_dir(tmp_path, "STANDARD")

    before_init = resolve_session_profile(config, run_dir=None, trw_dir=trw_dir)
    after_init = resolve_session_profile(config, run_dir=run_dir, trw_dir=trw_dir)

    assert before_init.profile.ceremony_tier != after_init.profile.ceremony_tier
    assert resolution_basis(before_init, run_dir=None)["session_layer_present"] is False
    assert resolution_basis(after_init, run_dir=run_dir)["session_layer_present"] is True


def test_the_basis_names_the_run_dir_and_the_layers(tmp_path: Path, trw_dir: Path) -> None:
    """A reader must be able to see WHAT produced the tier, not just the tier."""
    config = TRWConfig()
    run_dir = _run_dir(tmp_path, "MINIMAL")

    basis = resolution_basis(resolve_session_profile(config, run_dir=run_dir, trw_dir=trw_dir), run_dir=run_dir)

    assert basis["run_dir"] == str(run_dir)
    assert basis["ceremony_tier"] == "MINIMAL"
    assert "session" in basis["layers_applied"]  # type: ignore[operator]
    assert "defaults" in basis["layers_applied"]  # type: ignore[operator]


def test_a_run_dir_without_a_session_layer_is_not_reported_as_having_one(tmp_path: Path, trw_dir: Path) -> None:
    """Negative case: a run exists but the Scout never wrote a profile."""
    config = TRWConfig()
    run_dir = _run_dir(tmp_path, None)

    basis = resolution_basis(resolve_session_profile(config, run_dir=run_dir, trw_dir=trw_dir), run_dir=run_dir)

    assert basis["session_layer_present"] is False
    assert basis["run_dir"] == str(run_dir)


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
    run_dir = _run_dir(tmp_path, "STANDARD")

    results: SessionStartResultDict = {}
    step_resolve_profile(TRWConfig(), run_dir, results)

    basis = results["profile_resolution_basis"]
    assert basis["session_layer_present"] is True
    assert basis["ceremony_tier"] == "STANDARD"
    assert results["resolved_profile"]["ceremony_tier"] == basis["ceremony_tier"]  # type: ignore[index]


def test_profile_explain_emits_the_identical_block(tmp_path: Path, trw_dir: Path) -> None:
    """Byte-identical basis blocks are what make the two reports comparable."""
    config = TRWConfig()
    run_dir = _run_dir(tmp_path, "COMPREHENSIVE")
    resolved = resolve_session_profile(config, run_dir=run_dir, trw_dir=trw_dir)

    payload = build_explanation(resolved, run_dir=run_dir)

    assert payload["profile_resolution_basis"] == resolution_basis(resolved, run_dir=run_dir)
