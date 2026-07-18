"""Coverage tests for PRD enforcement validation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._validation_gates_support import _make_run_dir
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import _check_prd_enforcement


class TestCheckPrdEnforcementOff:
    """_check_prd_enforcement returns empty list when enforcement is off."""

    def test_enforcement_off_returns_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        assert result == []


class TestCheckPrdEnforcementResearchRunType:
    """Research run types skip PRD enforcement."""

    def test_research_run_type_returns_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "framework": "v24.0_TRW",
                "status": "active",
                "phase": "research",
                "confidence": "medium",
                "run_type": "research",
            },
        )
        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        assert result == []


class TestCheckPrdEnforcementNoPrds:
    """_check_prd_enforcement returns advisory warning when no PRDs found."""

    def test_no_prds_returns_advisory_warning(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="lenient")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        assert len(result) == 1
        assert result[0].rule == "prd_discovery"
        assert result[0].severity == "warning"


class TestCheckPrdEnforcementPrdFileNotFound:
    """_check_prd_enforcement fails when PRD file is missing."""

    def test_prd_file_not_found_returns_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "research",
                "prd_scope": ["PRD-FAKE-001"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)

        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        rules = [f.rule for f in result]
        assert "prd_exists" in rules


class TestCheckPrdEnforcementPrdStatusTooLow:
    """_check_prd_enforcement fails when PRD status is below required."""

    def test_draft_prd_fails_approved_requirement(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-TEST-001"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        prd_content = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: "1.0"
  status: draft
  priority: P1
---

# PRD-TEST-001
"""
        (prds_dir / "PRD-TEST-001.md").write_text(prd_content, encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        rules = [f.rule for f in result]
        assert "prd_status" in rules

    def test_approved_prd_passes_approved_requirement(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-TEST-002"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        prd_content = """\
---
prd:
  id: PRD-TEST-002
  title: Approved PRD
  version: "1.0"
  status: approved
  priority: P1
---

# PRD-TEST-002
"""
        (prds_dir / "PRD-TEST-002.md").write_text(prd_content, encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        rules = [f.rule for f in result]
        assert "prd_status" not in rules
        assert "prd_exists" not in rules


# ---------------------------------------------------------------------------
# PRD-QUAL-119-FR03: rollout state is not completion
# ---------------------------------------------------------------------------


def test_prd_qual_119_fr03() -> None:
    """FR03 acceptance: Given behavior defaults to observe, warn, shadow,
    canary-only, or disabled, When completion runs, Then it returns incomplete
    with reason rollout_not_default until normal default activation."""
    from trw_mcp.models.gate_decision import EffectiveCompletionOutcome
    from trw_mcp.tools._prd_transition_gate import (
        ROLLOUT_NOT_DEFAULT,
        CoherenceReport,
        derive_transition_decision,
        rollout_blocking,
    )

    for state in ("observe", "warn", "shadow", "canary", "canary-only", "disabled", "OBSERVE "):
        blocking = rollout_blocking({"rollout_state": state})
        assert blocking == [ROLLOUT_NOT_DEFAULT], state
        # And through the universal decision: incomplete, with the reason visible.
        decision = derive_transition_decision(
            "PRD-CORE-001", CoherenceReport(blocking=blocking), {"rollout_state": state}, "content"
        )
        assert decision.outcome is EffectiveCompletionOutcome.INCOMPLETE
        assert any(ROLLOUT_NOT_DEFAULT in reason for reason in decision.reasons)

    # Normal default activation (absent or explicit) adds nothing.
    assert rollout_blocking({}) == []
    assert rollout_blocking({"rollout_state": "default"}) == []
    clean = derive_transition_decision("PRD-CORE-001", CoherenceReport(), {}, "content")
    assert clean.outcome is EffectiveCompletionOutcome.COMPLETE


def test_prd_qual_119_nfr02() -> None:
    """NFR02: same PRD, receipts, configuration, and gate state -> same canonical
    decision; evaluated_at and decision_id are observation metadata only."""
    from trw_mcp.models.gate_decision import (
        CompletionComponent,
        CompletionComponentState,
        derive_effective_completion,
    )

    components = (CompletionComponent(component_id="build", state=CompletionComponentState.STALE),)
    first = derive_effective_completion(
        "PRD-X", components=components, source_digest="sha256:abc", evaluated_at="2026-07-11T01:00:00Z"
    )
    second = derive_effective_completion(
        "PRD-X",
        components=components,
        source_digest="sha256:abc",
        evaluated_at="2026-07-12T09:30:00Z",
        decision_id="different",
    )
    assert first.canonical_digest() == second.canonical_digest()
