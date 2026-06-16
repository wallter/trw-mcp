"""Unit tests for PRD-SCALE-001 Scout classification (FR01/FR02/FR03/FR12/FR13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.cognitive_scaling import scout
from trw_mcp.models.cognitive_scaling import PlanningMode, ScoutSignals


def _signals(*, br: bool = False, churn: bool = False, prec: bool = False, available: bool = True) -> ScoutSignals:
    return ScoutSignals(
        blast_radius_hit=br,
        blast_radius_available=available,
        churn_hit=churn,
        churn_available=available,
        precedent_gap_hit=prec,
        precedent_gap_available=available,
    )


def _patch_signals(monkeypatch: pytest.MonkeyPatch, signals: ScoutSignals) -> None:
    monkeypatch.setattr(
        "trw_mcp.cognitive_scaling.scout.compute_signals",
        lambda **_k: signals,
    )


def test_zero_hits_classifies_direct(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR01: all signals below threshold -> DIRECT/MINIMAL (US-001)."""
    _patch_signals(monkeypatch, _signals())
    c = scout.classify(task_description="typo fix", project_root=tmp_path, trw_dir=tmp_path)
    assert c.planning_mode == PlanningMode.DIRECT
    assert c.ceremony_tier == "MINIMAL"
    assert c.probe_budget == 0
    assert c.degraded is False


def test_two_hits_classifies_triangulated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR01: 2 of 3 signals hit -> TRIANGULATED (hard rule: mode>=2 needs >=2)."""
    _patch_signals(monkeypatch, _signals(br=True, churn=True))
    c = scout.classify(task_description="big refactor", project_root=tmp_path, trw_dir=tmp_path)
    assert c.planning_mode == PlanningMode.TRIANGULATED
    assert c.ceremony_tier == "COMPREHENSIVE"
    assert c.escalation_reason is not None


def test_three_hits_classifies_with_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR01: all 3 signals hit -> TRIANGULATED_WITH_PROBE, probe budget 3."""
    _patch_signals(monkeypatch, _signals(br=True, churn=True, prec=True))
    c = scout.classify(task_description="auth rewrite", project_root=tmp_path, trw_dir=tmp_path)
    assert c.planning_mode == PlanningMode.TRIANGULATED_WITH_PROBE
    assert c.probe_budget == 3


def test_one_hit_classifies_dual_draft(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR01: a single threshold hit -> DUAL_DRAFT."""
    _patch_signals(monkeypatch, _signals(br=True))
    c = scout.classify(task_description="x", project_root=tmp_path, trw_dir=tmp_path)
    assert c.planning_mode == PlanningMode.DUAL_DRAFT
    assert c.probe_budget == 1


def test_degrade_on_too_few_signals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR12: <2 computable signals -> DIRECT, degraded, NEVER escalates."""
    # Only one available signal, and it is a "hit" — must STILL degrade to DIRECT.
    sig = ScoutSignals(
        blast_radius_hit=True,
        blast_radius_available=True,
        churn_available=False,
        precedent_gap_available=False,
    )
    _patch_signals(monkeypatch, sig)
    c = scout.classify(task_description="x", project_root=tmp_path, trw_dir=tmp_path)
    assert c.planning_mode == PlanningMode.DIRECT
    assert c.degraded is True
    assert c.ceremony_tier == "MINIMAL"  # no silent escalation
    assert c.downgrade_reason is not None
    # Sprint-97 adaptive-surface review F3: the computed signals are carried on
    # the degraded result via the CONSTRUCTOR (no post-construction mutation),
    # so the degraded state survives a future frozen=True on ScoutClassification.
    assert c.signals == sig
    assert c.signals.blast_radius_available is True
    assert c.signals.available_count() == 1


def test_compute_signals_exception_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR12: an unexpected signal-compute crash degrades to DIRECT (fail-open)."""

    def _boom(**_k: object) -> ScoutSignals:
        raise RuntimeError("git exploded")

    monkeypatch.setattr("trw_mcp.cognitive_scaling.scout.compute_signals", _boom)
    c = scout.classify(task_description="x", project_root=tmp_path, trw_dir=tmp_path)
    assert c.planning_mode == PlanningMode.DIRECT
    assert c.degraded is True


def test_user_override_forces_mode_and_records_original(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR13: override wins; the Scout-emitted mode is recorded for dissent."""
    # Scout would have said TRIANGULATED (2 hits); user forces DIRECT (US-005).
    _patch_signals(monkeypatch, _signals(br=True, churn=True))
    c = scout.classify(
        task_description="x",
        project_root=tmp_path,
        trw_dir=tmp_path,
        override_mode=PlanningMode.DIRECT,
    )
    assert c.planning_mode == PlanningMode.DIRECT
    assert c.source == "user_override"
    assert c.original_mode == PlanningMode.TRIANGULATED
    assert c.downgrade_reason is not None


def test_propose_probe_budget_matches_table() -> None:
    """FR07: probe budget sourced from canonical CORE-144 table."""
    assert scout.propose_probe_budget(PlanningMode.DIRECT) == 0
    assert scout.propose_probe_budget(PlanningMode.TRIANGULATED_WITH_PROBE) == 3


def test_write_session_profile_roundtrips(tmp_path: Path) -> None:
    """FR03: overlay file is written + reads back as the H2 session layer shape."""
    from ruamel.yaml import YAML

    _patch_dir = tmp_path / "task" / "run"
    c = scout.classify(
        task_description="typo",
        project_root=tmp_path,
        trw_dir=tmp_path,
        override_mode=PlanningMode.TRIANGULATED,
    )
    path = scout.write_session_profile(c, run_dir=_patch_dir)
    assert path is not None
    assert path == _patch_dir / "meta" / "session_profile.yaml"
    data = YAML(typ="safe").load(path.read_text())
    # The body is the H2-consumable Profile surface: ceremony_tier is the only
    # top-level Profile key; SCALE-001 join keys ride inside the rationale.
    assert data["ceremony_tier"] == "COMPREHENSIVE"
    assert set(data) == {"ceremony_tier", "rationale"}
    assert "planning_mode=2" in data["rationale"]
    assert "probe_budget=2" in data["rationale"]
    assert "user_override" in data["rationale"]


def test_write_session_profile_failopen_on_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR03/NFR09: a write failure returns None, never raises."""
    c = scout.classify(task_description="x", project_root=tmp_path, trw_dir=tmp_path)

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", _boom)
    assert scout.write_session_profile(c, run_dir=tmp_path / "r") is None
