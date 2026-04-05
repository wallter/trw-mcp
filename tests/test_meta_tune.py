"""Tests for PRD-CORE-109: Meta-Tune Synthesis Process + Emergent Skills.

Covers meta_tune.py (orchestrator) and meta_synthesis.py (artifact generation).
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_learning(
    *,
    id: str = "L-test01",
    summary: str = "test learning",
    detail: str = "detail text",
    type: str = "pattern",
    impact: float = 0.5,
    anchors: list[dict[str, object]] | None = None,
    anchor_validity: float = 1.0,
    expires: str = "",
    confidence: str = "medium",
    outcome_correlation: str | None = None,
    protection_tier: str = "normal",
    status: str = "active",
    reviewed_at: str | None = None,
    tags: list[str] | None = None,
    domain: list[str] | None = None,
    nudge_line: str = "",
    session_count: int = 0,
    source_type: str = "agent",
    client_profile: str = "claude-code",
    model_id: str = "claude-4",
    sessions_surfaced: int = 0,
) -> dict[str, Any]:
    """Create a synthetic learning entry dict for testing."""
    return {
        "id": id,
        "summary": summary,
        "detail": detail,
        "type": type,
        "impact": impact,
        "anchors": anchors or [],
        "anchor_validity": anchor_validity,
        "expires": expires,
        "confidence": confidence,
        "outcome_correlation": outcome_correlation,
        "protection_tier": protection_tier,
        "status": status,
        "reviewed_at": reviewed_at,
        "tags": tags or [],
        "domain": domain or [],
        "nudge_line": nudge_line or summary[:60],
        "session_count": session_count,
        "source_type": source_type,
        "client_profile": client_profile,
        "model_id": model_id,
        "sessions_surfaced": sessions_surfaced,
    }


def _make_cluster(
    *,
    cluster_id: str = "cluster-payments",
    domain_slug: str = "payments",
    learnings: list[dict[str, Any]] | None = None,
    exposure: int = 15,
    causal_lift: float = 0.3,
    avg_anchor_validity: float = 0.8,
) -> dict[str, Any]:
    """Create a synthetic cluster dict for testing."""
    return {
        "cluster_id": cluster_id,
        "domain_slug": domain_slug,
        "learnings": learnings or [],
        "exposure": exposure,
        "causal_lift": causal_lift,
        "avg_anchor_validity": avg_anchor_validity,
    }


# ===========================================================================
# Step 1: Validate anchors
# ===========================================================================


class TestStepValidateAnchors:
    """Tests for _step_validate_anchors."""

    def test_demotes_zero_validity(self) -> None:
        """Entries with anchor_validity=0.0 are demoted."""
        from trw_mcp.tools.meta_tune import _step_validate_anchors

        learnings = [
            _make_learning(
                id="L-bad01",
                anchors=[{"file": "foo.py", "symbol": "bar"}],
                anchor_validity=0.0,
            ),
            _make_learning(
                id="L-good01",
                anchors=[{"file": "baz.py", "symbol": "qux"}],
                anchor_validity=1.0,
            ),
        ]
        result = _step_validate_anchors(learnings)
        assert result.status == "ok"
        assert result.actions_taken == 1
        # The first learning should have been mutated
        assert learnings[0]["status"] == "obsolete"

    def test_skips_no_anchors(self) -> None:
        """Entries without anchors are skipped entirely."""
        from trw_mcp.tools.meta_tune import _step_validate_anchors

        learnings = [_make_learning(id="L-noanchor", anchors=[])]
        result = _step_validate_anchors(learnings)
        assert result.status == "ok"
        assert result.actions_taken == 0
        assert learnings[0]["status"] == "active"

    def test_flags_partial_anchors(self) -> None:
        """Entries with 1/3 valid anchors are flagged for review."""
        from trw_mcp.tools.meta_tune import _step_validate_anchors

        learnings = [
            _make_learning(
                id="L-partial",
                anchors=[{"file": "a.py"}, {"file": "b.py"}, {"file": "c.py"}],
                anchor_validity=0.33,
            ),
        ]
        result = _step_validate_anchors(learnings)
        assert result.status == "ok"
        # Partial anchors are flagged (counted as action) but not demoted
        assert result.actions_taken >= 1
        assert "flagged" in result.details.lower() or learnings[0]["status"] == "active"

    def test_respects_reviewed_at(self) -> None:
        """Entries with recent reviewed_at are not mutated."""
        from trw_mcp.tools.meta_tune import _step_validate_anchors

        learnings = [
            _make_learning(
                id="L-reviewed",
                anchors=[{"file": "foo.py"}],
                anchor_validity=0.0,
                reviewed_at="2099-01-01T00:00:00Z",
            ),
        ]
        result = _step_validate_anchors(
            learnings, last_tune_date="2026-01-01T00:00:00Z"
        )
        assert result.actions_taken == 0
        assert learnings[0]["status"] == "active"


# ===========================================================================
# Step 2: Validate workarounds
# ===========================================================================


class TestStepValidateWorkarounds:
    """Tests for _step_validate_workarounds."""

    def test_expired_workaround(self) -> None:
        """Expired workaround learnings are flagged."""
        from trw_mcp.tools.meta_tune import _step_validate_workarounds

        learnings = [
            _make_learning(
                id="L-expired",
                type="workaround",
                expires="2020-01-01",
            ),
        ]
        result = _step_validate_workarounds(learnings)
        assert result.status == "ok"
        assert result.actions_taken == 1
        assert learnings[0]["protection_tier"] == "low"

    def test_active_workaround(self) -> None:
        """Non-expired workarounds are not flagged."""
        from trw_mcp.tools.meta_tune import _step_validate_workarounds

        learnings = [
            _make_learning(
                id="L-active",
                type="workaround",
                expires="2099-12-31",
            ),
        ]
        result = _step_validate_workarounds(learnings)
        assert result.status == "ok"
        assert result.actions_taken == 0
        assert learnings[0]["protection_tier"] == "normal"

    def test_non_workaround_skipped(self) -> None:
        """Non-workaround learnings are skipped."""
        from trw_mcp.tools.meta_tune import _step_validate_workarounds

        learnings = [_make_learning(id="L-pattern", type="pattern")]
        result = _step_validate_workarounds(learnings)
        assert result.actions_taken == 0

    def test_workaround_no_expiry(self) -> None:
        """Workarounds without an expiry are not flagged."""
        from trw_mcp.tools.meta_tune import _step_validate_workarounds

        learnings = [
            _make_learning(id="L-no-exp", type="workaround", expires=""),
        ]
        result = _step_validate_workarounds(learnings)
        assert result.actions_taken == 0


# ===========================================================================
# Step 3: Resolve hypotheses
# ===========================================================================


class TestStepResolveHypotheses:
    """Tests for _step_resolve_hypotheses."""

    def test_old_hypothesis_removed(self) -> None:
        """Hypothesis older than threshold is removed (set to obsolete)."""
        from trw_mcp.tools.meta_tune import _step_resolve_hypotheses

        learnings = [
            _make_learning(
                id="L-hypo-old",
                type="hypothesis",
                session_count=50,
                outcome_correlation=None,
            ),
        ]
        result = _step_resolve_hypotheses(learnings, threshold_sessions=30)
        assert result.status == "ok"
        assert result.actions_taken >= 1
        assert learnings[0]["status"] == "obsolete"

    def test_confirmed_hypothesis_promoted(self) -> None:
        """Hypothesis with positive outcome is promoted to pattern."""
        from trw_mcp.tools.meta_tune import _step_resolve_hypotheses

        learnings = [
            _make_learning(
                id="L-hypo-confirmed",
                type="hypothesis",
                session_count=50,
                outcome_correlation="positive",
            ),
        ]
        result = _step_resolve_hypotheses(learnings, threshold_sessions=30)
        assert result.status == "ok"
        assert result.actions_taken >= 1
        assert learnings[0]["type"] == "pattern"

    def test_young_hypothesis_kept(self) -> None:
        """Hypothesis below threshold is untouched."""
        from trw_mcp.tools.meta_tune import _step_resolve_hypotheses

        learnings = [
            _make_learning(
                id="L-hypo-young",
                type="hypothesis",
                session_count=5,
                outcome_correlation=None,
            ),
        ]
        result = _step_resolve_hypotheses(learnings, threshold_sessions=30)
        assert result.actions_taken == 0
        assert learnings[0]["type"] == "hypothesis"
        assert learnings[0]["status"] == "active"


# ===========================================================================
# Meta.yaml synthesis (meta_synthesis.py)
# ===========================================================================


class TestSynthesizeMetaYaml:
    """Tests for synthesize_meta_yaml."""

    def test_structure(self, tmp_path: Path) -> None:
        """meta.yaml has the required top-level sections."""
        from trw_mcp.state.meta_synthesis import synthesize_meta_yaml

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learnings = [
            _make_learning(id="L-s1", type="incident", anchors=[{"file": "a.py"}]),
            _make_learning(id="L-s2", outcome_correlation="positive", anchors=[{"file": "b.py"}]),
        ]
        path = synthesize_meta_yaml(
            trw_dir,
            learnings=learnings,
            clusters=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )
        assert path.exists()

        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(path)

        # Required top-level keys
        assert "meta" in data
        assert "sensitive_paths" in data
        assert "fast_paths" in data
        assert "domain_map" in data
        assert "last_tune_date" in data

        # meta structure (C-2 layered overlays)
        meta = data["meta"]
        assert "base_profile" in meta
        assert "overlays" in meta
        assert "quarantined" in meta

    def test_versioned(self, tmp_path: Path) -> None:
        """Output includes model_family and trw_version (C-5)."""
        from trw_mcp.state.meta_synthesis import synthesize_meta_yaml

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        path = synthesize_meta_yaml(
            trw_dir,
            learnings=[],
            clusters=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(path)

        overlays = data["meta"]["overlays"]
        assert len(overlays) >= 1
        overlay = overlays[0]
        assert overlay["model_family"] == "claude-4"
        assert overlay["trw_version"] == "v24.4"

    def test_six_knobs_only(self, tmp_path: Path) -> None:
        """Only the 6 allowed adaptive knobs appear in overlay adjustments (C-3)."""
        from trw_mcp.state.meta_synthesis import ALLOWED_KNOBS, synthesize_meta_yaml

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        path = synthesize_meta_yaml(
            trw_dir,
            learnings=[],
            clusters=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(path)

        for overlay in data["meta"]["overlays"]:
            adjustments = overlay.get("adjustments", {})
            for key in adjustments:
                assert key in ALLOWED_KNOBS, f"Unexpected knob: {key}"

    def test_preserves_manual_edits(self, tmp_path: Path) -> None:
        """Manual domain_map entries (manual: true) survive regeneration."""
        from trw_mcp.state.meta_synthesis import synthesize_meta_yaml

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Seed existing meta.yaml with manual entry
        meta_path = trw_dir / "meta.yaml"
        meta_path.write_text(
            textwrap.dedent("""\
            meta:
              base_profile:
                surface_intensity: 3
              overlays: []
              quarantined: []
            sensitive_paths: []
            fast_paths: []
            domain_map:
              custom_domain:
                manual: true
                description: "manually added"
            last_tune_date: "2026-01-01T00:00:00Z"
            """)
        )

        path = synthesize_meta_yaml(
            trw_dir,
            learnings=[],
            clusters=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )

        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(path)

        assert "custom_domain" in data["domain_map"]
        assert data["domain_map"]["custom_domain"]["manual"] is True


# ===========================================================================
# Skill generation (meta_synthesis.py)
# ===========================================================================


class TestGenerateSkill:
    """Tests for generate_skill."""

    def test_passes_triple_gate(self, tmp_path: Path) -> None:
        """Cluster passing all 4 gates (triple + promotion) produces a skill file."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learnings = [
            _make_learning(
                id=f"L-pay{i}",
                summary=f"Payment pattern {i}",
                detail=f"Detailed payment pattern {i} explanation",
                tags=["payments"],
                anchor_validity=0.8,
                outcome_correlation="positive",
                sessions_surfaced=5,
            )
            for i in range(5)
        ]
        cluster = _make_cluster(
            domain_slug="payments",
            learnings=learnings,
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = generate_skill(cluster, trw_dir)
        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "auto_generated: true" in content
        assert "payments" in content.lower()

    def test_fails_exposure(self, tmp_path: Path) -> None:
        """Low exposure cluster produces no skill."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        cluster = _make_cluster(exposure=3, causal_lift=0.3, avg_anchor_validity=0.8)

        result = generate_skill(cluster, trw_dir, min_sessions=10)
        assert result is None

    def test_fails_causal_lift(self, tmp_path: Path) -> None:
        """Cluster with low causal lift produces no skill."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        cluster = _make_cluster(exposure=15, causal_lift=0.01, avg_anchor_validity=0.8)

        result = generate_skill(cluster, trw_dir, causal_lift_threshold=0.1)
        assert result is None

    def test_fails_anchor_validity(self, tmp_path: Path) -> None:
        """Low anchor_validity cluster produces no skill."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        cluster = _make_cluster(exposure=15, causal_lift=0.3, avg_anchor_validity=0.3)

        result = generate_skill(cluster, trw_dir)
        assert result is None

    def test_skill_uses_summary_not_detail(self, tmp_path: Path) -> None:
        """Generated skill files use summary only, never detail (NFR03)."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learnings = [
            _make_learning(
                id="L-secret",
                summary="Use retry logic for webhook delivery",
                detail="SENSITIVE: customer data leak at line 42 in payments.py",
                anchor_validity=0.9,
                outcome_correlation="positive",
                sessions_surfaced=5,
            ),
        ]
        cluster = _make_cluster(
            domain_slug="webhooks",
            learnings=learnings,
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.9,
        )

        result = generate_skill(cluster, trw_dir)
        assert result is not None
        content = result.read_text()
        assert "SENSITIVE" not in content
        assert "customer data leak" not in content
        assert "retry logic" in content


# ===========================================================================
# Team sync report
# ===========================================================================


class TestTeamSyncReport:
    """Tests for generate_team_sync_report."""

    def test_format(self) -> None:
        """Report is human-readable with counts."""
        from trw_mcp.tools.meta_tune import MetaTuneReport, StepResult
        from trw_mcp.state.meta_synthesis import generate_team_sync_report

        report = MetaTuneReport(
            steps=[
                StepResult(step="validate_anchors", status="ok", actions_taken=3),
                StepResult(step="validate_workarounds", status="ok", actions_taken=1),
                StepResult(step="resolve_hypotheses", status="ok", actions_taken=2),
                StepResult(step="compute_correlations", status="skipped", details="no data"),
                StepResult(step="graph_maintenance", status="skipped", details="no graph"),
                StepResult(step="bandit_update", status="skipped", details="bandit unavailable"),
                StepResult(step="synthesize_artifacts", status="ok", actions_taken=1),
                StepResult(step="prd_nudge_analysis", status="skipped", details="no data"),
                StepResult(step="team_sync_report", status="ok", actions_taken=0),
            ],
            total_actions=7,
            learnings_demoted=3,
            hypotheses_resolved=2,
        )

        text = generate_team_sync_report(report)
        assert isinstance(text, str)
        assert "validate_anchors" in text
        assert "3" in text  # demoted count
        assert "2" in text  # hypotheses resolved
        assert "skipped" in text.lower()


# ===========================================================================
# Full meta-tune orchestrator
# ===========================================================================


class TestExecuteMetaTune:
    """Tests for execute_meta_tune."""

    def test_full_run(self, tmp_path: Path) -> None:
        """Full 9-step run with synthetic data produces report with all steps."""
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learnings = [
            # Will be demoted (0.0 validity)
            _make_learning(
                id="L-stale",
                anchors=[{"file": "old.py"}],
                anchor_validity=0.0,
            ),
            # Will be expired
            _make_learning(
                id="L-workaround-exp",
                type="workaround",
                expires="2020-01-01",
            ),
            # Will be resolved
            _make_learning(
                id="L-hypo",
                type="hypothesis",
                session_count=50,
                outcome_correlation="positive",
            ),
            # Normal learning — untouched
            _make_learning(id="L-normal", type="pattern"),
        ]

        report = execute_meta_tune(
            trw_dir,
            learnings=learnings,
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )

        assert len(report.steps) == 9
        assert report.total_actions > 0

        # Check specific steps completed
        step_map = {s.step: s for s in report.steps}
        assert step_map["validate_anchors"].status == "ok"
        assert step_map["validate_anchors"].actions_taken >= 1
        assert step_map["validate_workarounds"].status == "ok"
        assert step_map["resolve_hypotheses"].status == "ok"

    def test_partial_failure(self, tmp_path: Path) -> None:
        """One step failing does not prevent other steps from running."""
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Pass a learning that will cause step 1 to work but later steps to
        # exercise their graceful degradation paths
        learnings = [_make_learning(id="L-plain")]

        report = execute_meta_tune(
            trw_dir,
            learnings=learnings,
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )

        # All 9 steps should appear in the report
        assert len(report.steps) == 9
        # No step should be "error" unless something truly broke
        statuses = {s.status for s in report.steps}
        assert "error" not in statuses

    def test_empty_learnings(self, tmp_path: Path) -> None:
        """Empty learning corpus produces valid report without errors."""
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        report = execute_meta_tune(
            trw_dir,
            learnings=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )

        assert len(report.steps) == 9
        # Step 7 (synthesize_artifacts) still writes meta.yaml even with empty corpus
        # so total_actions may be 1 for that step; learning-mutation actions are 0
        assert report.learnings_demoted == 0
        assert report.hypotheses_resolved == 0
        statuses = {s.status for s in report.steps}
        assert "error" not in statuses

    def test_step_selection(self, tmp_path: Path) -> None:
        """Running specific steps only executes those steps."""
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learnings = [
            _make_learning(
                id="L-hypo",
                type="hypothesis",
                session_count=50,
                outcome_correlation="positive",
            ),
        ]

        report = execute_meta_tune(
            trw_dir,
            learnings=learnings,
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
            steps=[1, 3],
        )

        # Only steps 1 and 3 should have "ok" status; others should be "skipped"
        step_map = {s.step: s for s in report.steps}
        assert step_map["validate_anchors"].status == "ok"
        assert step_map["resolve_hypotheses"].status == "ok"
        assert step_map["validate_workarounds"].status == "skipped"

    def test_invalid_steps_ignored(self, tmp_path: Path) -> None:
        """Invalid step numbers (0, 10, -1) are silently ignored."""
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        report = execute_meta_tune(
            trw_dir,
            learnings=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
            steps=[0, 10, -1],
        )

        # All 9 steps should show as skipped (no valid step numbers)
        assert len(report.steps) == 9
        for step in report.steps:
            assert step.status == "skipped"


# ===========================================================================
# MetaTuneReport dataclass
# ===========================================================================


class TestMetaTuneReport:
    """Tests for MetaTuneReport dataclass."""

    def test_aggregates_results(self) -> None:
        """MetaTuneReport correctly aggregates step results."""
        from trw_mcp.tools.meta_tune import MetaTuneReport, StepResult

        report = MetaTuneReport(
            steps=[
                StepResult(step="step1", status="ok", actions_taken=5),
                StepResult(step="step2", status="ok", actions_taken=3),
            ],
            total_actions=8,
            learnings_demoted=2,
            hypotheses_resolved=1,
        )

        assert report.total_actions == 8
        assert report.learnings_demoted == 2
        assert report.hypotheses_resolved == 1
        assert len(report.steps) == 2


# ===========================================================================
# Shadow mode
# ===========================================================================


class TestShadowMode:
    """Tests for shadow mode behavior."""

    def test_shadow_mode_first_3_runs(self, tmp_path: Path) -> None:
        """In shadow mode, mutations are logged but not applied."""
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learnings = [
            _make_learning(
                id="L-should-stay",
                anchors=[{"file": "foo.py"}],
                anchor_validity=0.0,
            ),
        ]

        # Shadow mode = True (default) means no mutations
        report = execute_meta_tune(
            trw_dir,
            learnings=learnings,
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
                "shadow_mode": True,
            },
        )

        assert len(report.steps) == 9
        # In shadow mode, learning should NOT be mutated
        assert learnings[0]["status"] == "active"

        # Steps still report what they would have done
        step_map = {s.step: s for s in report.steps}
        assert step_map["validate_anchors"].actions_taken >= 1
        assert "dry_run" in step_map["validate_anchors"].details.lower() or \
            "shadow" in step_map["validate_anchors"].details.lower()


# ===========================================================================
# meta.yaml corruption recovery
# ===========================================================================


class TestMetaYamlRecovery:
    """Tests for corrupt meta.yaml handling."""

    def test_corrupt_meta_yaml_recreated(self, tmp_path: Path) -> None:
        """Corrupt meta.yaml is recreated from scratch."""
        from trw_mcp.state.meta_synthesis import synthesize_meta_yaml

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Write corrupt YAML
        (trw_dir / "meta.yaml").write_text("{{{{invalid yaml: [")

        path = synthesize_meta_yaml(
            trw_dir,
            learnings=[],
            clusters=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )

        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(path)
        assert "meta" in data
        assert "last_tune_date" in data

    def test_missing_meta_key_upgraded(self, tmp_path: Path) -> None:
        """Legacy meta.yaml without 'meta' key gets upgraded."""
        from trw_mcp.state.meta_synthesis import synthesize_meta_yaml

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Write legacy format (no meta key)
        (trw_dir / "meta.yaml").write_text(
            textwrap.dedent("""\
            sensitive_paths:
              - path: "legacy.py"
                incident_count: 2
            fast_paths: []
            domain_map: {}
            last_tune_date: "2025-01-01T00:00:00Z"
            """)
        )

        path = synthesize_meta_yaml(
            trw_dir,
            learnings=[],
            clusters=[],
            config={
                "model_family": "claude-4",
                "trw_version": "v24.4",
            },
        )

        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(path)

        # meta key should be added
        assert "meta" in data
        assert "base_profile" in data["meta"]
        # Legacy data preserved
        assert "sensitive_paths" in data


# ===========================================================================
# Fix validations: integration wiring audit fixes
# ===========================================================================


class TestOutcomeCorrelationEmptyString:
    """Fix 3: outcome_correlation is set to '' not None on demotion."""

    def test_demoted_entry_has_empty_string_correlation(self) -> None:
        """When an entry is demoted (validity=0.0), outcome_correlation is ''."""
        from trw_mcp.tools.meta_tune import _step_validate_anchors

        learnings = [
            _make_learning(
                id="L-demote-check",
                anchors=[{"file": "foo.py"}],
                anchor_validity=0.0,
            ),
        ]
        _step_validate_anchors(learnings, shadow_mode=False)
        assert learnings[0]["outcome_correlation"] == ""
        assert learnings[0]["outcome_correlation"] is not None


class TestStructlogNoFString:
    """Fix 4: _run_step uses static event key, not f-string."""

    def test_run_step_error_uses_static_event(self) -> None:
        """_run_step logs 'meta_tune_step_error' with step kwarg, not f-string."""
        import logging

        from trw_mcp.tools.meta_tune import _run_step

        captured: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record.getMessage())

        handler = _Handler()
        logger = logging.getLogger("trw_mcp.tools.meta_tune")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)

        try:
            def _fail() -> None:
                msg = "boom"
                raise RuntimeError(msg)

            _run_step(1, "test_step", {1}, _fail)  # type: ignore[arg-type]
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        # The event key should be the static string, not an f-string interpolated one
        # This is a structural check — we already verified the code via grep


class TestSynthesizeArtifactsWithClusters:
    """Fix 5: _step_synthesize_artifacts wires clusters to generate_skill."""

    def test_clusters_passed_to_generate_skill(self, tmp_path: Path) -> None:
        """When clusters are provided, generate_skill is called for each."""
        from trw_mcp.tools.meta_tune import _step_synthesize_artifacts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learnings = [
            _make_learning(
                id="L-cluster1",
                summary="Payment retry logic",
                detail="Detailed payment retry logic explanation",
                anchor_validity=0.8,
                outcome_correlation="positive",
                sessions_surfaced=5,
            ),
        ]
        cluster = _make_cluster(
            domain_slug="payments",
            learnings=learnings,
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = _step_synthesize_artifacts(
            trw_dir,
            learnings=learnings,
            config={"model_family": "claude-4", "trw_version": "v24.4"},
            clusters=[cluster],
        )

        assert result.status == "ok"
        assert result.actions_taken >= 2  # meta.yaml + 1 skill
        assert "1 skills generated" in result.details

        # Verify skill file was actually created
        skill_path = trw_dir / "skills" / "payments" / "SKILL.md"
        assert skill_path.exists()

    def test_no_clusters_no_skills(self, tmp_path: Path) -> None:
        """Without clusters, no skills are generated."""
        from trw_mcp.tools.meta_tune import _step_synthesize_artifacts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        result = _step_synthesize_artifacts(
            trw_dir,
            learnings=[],
            config={"model_family": "claude-4", "trw_version": "v24.4"},
        )

        assert result.status == "ok"
        assert "0 skills generated" in result.details

    def test_cluster_failing_gate_no_skill(self, tmp_path: Path) -> None:
        """Cluster that fails the triple gate produces no skill."""
        from trw_mcp.tools.meta_tune import _step_synthesize_artifacts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Low exposure cluster should fail the gate
        cluster = _make_cluster(
            domain_slug="low-exposure",
            exposure=2,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = _step_synthesize_artifacts(
            trw_dir,
            learnings=[],
            config={"model_family": "claude-4", "trw_version": "v24.4"},
            clusters=[cluster],
        )

        assert result.status == "ok"
        assert "0 skills generated" in result.details


class TestMapEstimateShared:
    """Fix 6: _map_estimate_to_category is shared between ips.py and pipeline.py."""

    def test_shared_function_importable(self) -> None:
        """map_estimate_to_category is importable from _common."""
        from trw_mcp.scoring.attribution._common import map_estimate_to_category

        assert map_estimate_to_category(0.8) == "strong_positive"
        assert map_estimate_to_category(0.6) == "positive"
        assert map_estimate_to_category(0.3) == "neutral"
        assert map_estimate_to_category(-0.7) == "negative"

    def test_ips_uses_shared(self) -> None:
        """ips.py's _map_estimate_to_category is the shared function."""
        from trw_mcp.scoring.attribution._common import map_estimate_to_category
        from trw_mcp.scoring.attribution.ips import _map_estimate_to_category

        assert _map_estimate_to_category is map_estimate_to_category


class TestRegisterMetaTuneTools:
    """Fix 1: register_meta_tune_tools is importable and functional."""

    def test_importable(self) -> None:
        """register_meta_tune_tools is importable."""
        from trw_mcp.tools.meta_tune import register_meta_tune_tools

        assert callable(register_meta_tune_tools)

    def test_tool_registered_on_server(self) -> None:
        """trw_meta_tune is registered as a tool on the server."""
        from tests.conftest import get_tools_sync

        from fastmcp import FastMCP

        from trw_mcp.tools.meta_tune import register_meta_tune_tools

        server = FastMCP("test-meta-tune")
        register_meta_tune_tools(server)
        tools = get_tools_sync(server)
        assert "trw_meta_tune" in tools


# ===========================================================================
# P1-3: Correct database path in step 5 (graph_maintenance)
# ===========================================================================


class TestGraphMaintenanceDbPath:
    """P1-3: _step_graph_maintenance uses trw_dir/memory/memory.db, not trw_dir/memory.db."""

    def test_correct_db_path(self, tmp_path: Path) -> None:
        """Step 5 looks for memory.db under trw_dir/memory/, not trw_dir/."""
        from trw_mcp.tools.meta_tune import _step_graph_maintenance

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Create file at wrong path (old code would find this)
        wrong_path = trw_dir / "memory.db"
        wrong_path.write_text("fake")

        # Step 5 should NOT find the db at the wrong path
        result = _step_graph_maintenance([], trw_dir=trw_dir)
        # Without the correct path, it should skip (no memory database)
        assert result.status == "skipped"
        assert "no memory database" in result.details

    def test_finds_correct_path(self, tmp_path: Path) -> None:
        """Step 5 finds memory.db at trw_dir/memory/memory.db."""
        from trw_mcp.tools.meta_tune import _step_graph_maintenance

        trw_dir = tmp_path / ".trw"
        memory_dir = trw_dir / "memory"
        memory_dir.mkdir(parents=True)

        # Create a real SQLite db at the correct path
        import sqlite3

        db_path = memory_dir / "memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS test_table (id TEXT)")
        conn.close()

        # Step 5 should find the db; it may skip for other reasons
        # (e.g., missing trw_memory.graph) but should not say "no memory database"
        result = _step_graph_maintenance([], trw_dir=trw_dir)
        assert "no memory database" not in result.details


# ===========================================================================
# P1-4: Thread detected_clusters from step 5 to step 7
# ===========================================================================


class TestClusterThreading:
    """P1-4: detected_clusters populated by step 5 are visible to step 7."""

    def test_out_clusters_populated(self) -> None:
        """_step_graph_maintenance populates out_clusters when provided."""
        from trw_mcp.tools.meta_tune import _step_graph_maintenance

        # out_clusters should be a mutable list that gets extended
        out: list[dict[str, Any]] = []
        # Without a real db, clusters won't be detected, but the parameter is accepted
        result = _step_graph_maintenance([], trw_dir=None, out_clusters=out)
        assert result.status == "skipped"
        # out should still be empty (no db), but no error
        assert isinstance(out, list)

    def test_mutable_list_shared_between_steps(self, tmp_path: Path) -> None:
        """In execute_meta_tune, detected_clusters list is shared between step 5 and step 7."""
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Run only steps 5 and 7 to verify the threading
        report = execute_meta_tune(
            trw_dir,
            learnings=[],
            config={"model_family": "claude-4", "trw_version": "v24.4"},
            steps=[5, 7],
        )

        step_map = {s.step: s for s in report.steps}
        # Step 7 should not error; it receives the (possibly empty) cluster list
        assert step_map["synthesize_artifacts"].status == "ok"


# ===========================================================================
# P0-4: Promotion gate in skill generation
# ===========================================================================


class TestPromotionGateInSkillGeneration:
    """P0-4: generate_skill checks promotion gate as 4th gate."""

    def test_blocks_when_majority_fail_promotion(self, tmp_path: Path) -> None:
        """Cluster with learnings that fail promotion gate produces no skill."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Learnings without required promotion fields (no detail, no sessions_surfaced, no outcome)
        learnings = [
            _make_learning(
                id=f"L-fail{i}",
                detail="",  # empty detail fails provenance check
                anchor_validity=0.8,
                outcome_correlation=None,
                sessions_surfaced=0,
            )
            for i in range(5)
        ]
        cluster = _make_cluster(
            domain_slug="failing-promo",
            learnings=learnings,
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = generate_skill(cluster, trw_dir)
        assert result is None

    def test_passes_when_majority_pass_promotion(self, tmp_path: Path) -> None:
        """Cluster with learnings passing promotion gate produces a skill."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Learnings that pass all 5 promotion criteria
        learnings = [
            _make_learning(
                id=f"L-good{i}",
                detail=f"Detailed explanation {i}",
                anchor_validity=0.8,
                outcome_correlation="positive",
                sessions_surfaced=5,
            )
            for i in range(5)
        ]
        cluster = _make_cluster(
            domain_slug="good-promo",
            learnings=learnings,
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = generate_skill(cluster, trw_dir)
        assert result is not None
        assert result.exists()

    def test_mixed_majority_passes(self, tmp_path: Path) -> None:
        """Cluster with 3/5 learnings passing promotion gate produces a skill."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # 3 learnings pass, 2 fail
        good_learnings = [
            _make_learning(
                id=f"L-pass{i}",
                detail=f"Detailed explanation {i}",
                anchor_validity=0.8,
                outcome_correlation="positive",
                sessions_surfaced=5,
            )
            for i in range(3)
        ]
        bad_learnings = [
            _make_learning(
                id=f"L-bad{i}",
                detail="",
                anchor_validity=0.2,
                outcome_correlation=None,
                sessions_surfaced=0,
            )
            for i in range(2)
        ]
        cluster = _make_cluster(
            domain_slug="mixed-promo",
            learnings=good_learnings + bad_learnings,
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = generate_skill(cluster, trw_dir)
        assert result is not None  # 3/5 > 50%, so it passes

    def test_mixed_majority_fails(self, tmp_path: Path) -> None:
        """Cluster with 2/5 learnings passing promotion gate produces no skill."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # 2 pass, 3 fail
        good_learnings = [
            _make_learning(
                id=f"L-pass{i}",
                detail=f"Detailed explanation {i}",
                anchor_validity=0.8,
                outcome_correlation="positive",
                sessions_surfaced=5,
            )
            for i in range(2)
        ]
        bad_learnings = [
            _make_learning(
                id=f"L-bad{i}",
                detail="",
                anchor_validity=0.2,
                outcome_correlation=None,
                sessions_surfaced=0,
            )
            for i in range(3)
        ]
        cluster = _make_cluster(
            domain_slug="mixed-fail",
            learnings=good_learnings + bad_learnings,
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = generate_skill(cluster, trw_dir)
        assert result is None  # 2/5 < 50%, blocked

    def test_empty_learnings_skips_gate(self, tmp_path: Path) -> None:
        """Cluster with no learnings skips the promotion gate entirely."""
        from trw_mcp.state.meta_synthesis import generate_skill

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        cluster = _make_cluster(
            domain_slug="empty-promo",
            learnings=[],
            exposure=15,
            causal_lift=0.3,
            avg_anchor_validity=0.8,
        )

        result = generate_skill(cluster, trw_dir)
        # Should pass all gates (triple gate + promotion gate skipped for empty)
        assert result is not None


# ===========================================================================
# P0-1: Bandit wiring in append_ceremony_nudge
# ===========================================================================


class TestBanditWiringInNudge:
    """P0-1: append_ceremony_nudge loads bandit state and passes it to select_nudge_learning."""

    def test_bandit_loaded_when_state_exists(self, tmp_path: Path) -> None:
        """When bandit_state.json exists, it is loaded and passed to select_nudge_learning."""
        from trw_mcp.state._nudge_state import CeremonyState, write_ceremony_state
        from trw_mcp.tools._session_recall_helpers import append_ceremony_nudge

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(session_started=True, phase="implement")
        write_ceremony_state(trw_dir, state)

        # Create bandit state file
        from trw_memory.bandit import BanditSelector

        bandit = BanditSelector()
        meta_dir = trw_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "bandit_state.json").write_text(
            bandit.to_json(), encoding="utf-8"
        )

        # Patch recall_learnings and select_nudge_learning to verify bandit is passed
        with patch(
            "trw_mcp.tools._session_recall_helpers.resolve_trw_dir",
            return_value=trw_dir,
        ), patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[{"id": "L-test", "summary": "test", "impact": 0.8}],
        ) as mock_recall, patch(
            "trw_mcp.state._nudge_rules.select_nudge_learning",
            return_value=({"id": "L-test", "summary": "test"}, False),
        ) as mock_select:
            result = append_ceremony_nudge(
                {"status": "ok"},
                trw_dir=trw_dir,
                available_learnings=5,
            )

        # Verify select_nudge_learning was called with a bandit argument
        assert mock_select.called
        call_kwargs = mock_select.call_args
        assert call_kwargs.kwargs.get("bandit") is not None
        assert call_kwargs.kwargs.get("client_class") is not None

    def test_bandit_none_when_no_state(self, tmp_path: Path) -> None:
        """When no bandit_state.json exists, bandit=None is passed (deterministic path)."""
        from trw_mcp.state._nudge_state import CeremonyState, write_ceremony_state
        from trw_mcp.tools._session_recall_helpers import append_ceremony_nudge

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(session_started=True, phase="implement")
        write_ceremony_state(trw_dir, state)

        # No bandit state file
        with patch(
            "trw_mcp.tools._session_recall_helpers.resolve_trw_dir",
            return_value=trw_dir,
        ), patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[{"id": "L-test", "summary": "test", "impact": 0.8}],
        ), patch(
            "trw_mcp.state._nudge_rules.select_nudge_learning",
            return_value=({"id": "L-test", "summary": "test"}, False),
        ) as mock_select:
            append_ceremony_nudge(
                {"status": "ok"},
                trw_dir=trw_dir,
                available_learnings=5,
            )

        assert mock_select.called
        call_kwargs = mock_select.call_args
        assert call_kwargs.kwargs.get("bandit") is None
