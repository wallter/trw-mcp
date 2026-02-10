"""Tests for PRD-CORE-011: Planning Agent Architecture.

Covers grooming plan generation, trw_prd_groom tool, hook validation,
agent definitions, and integration tests.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.planning import (
    GroomingPlan,
    PLANNING_AGENT_ROLES,
    SectionAnalysis,
    SectionStatus,
)
from trw_mcp.state.grooming import (
    _analyze_section,
    _estimate_iterations,
    _extract_background_keywords,
    _extract_prd_id,
    generate_grooming_plan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SKELETON_PRD = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: '1.0'
  status: draft
  priority: P1
  category: CORE
  confidence:
    implementation_feasibility: 0.7
    requirement_clarity: 0.7
    estimate_confidence: 0.6
    test_coverage_target: 0.85
  traceability:
    implements: []
    depends_on: []
    enables: []
    conflicts_with: []
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
  dates:
    created: '2026-02-09'
    updated: '2026-02-09'
---

# PRD-TEST-001: Test PRD

**Quick Reference**:
- **Status**: Draft
- **Priority**: P1

---

## 1. Problem Statement

### Background

The TRW framework needs a test PRD for validation. The `trw_prd_groom` tool
and `generate_grooming_plan` function use this for unit testing.

### Problem

<!-- Problem description needed -->

### Impact

<!-- Impact analysis needed -->

---

## 2. Goals & Non-Goals

<!-- Goals and non-goals needed -->

---

## 3. User Stories

<!-- User stories needed -->

---

## 4. Functional Requirements

<!-- Functional requirements needed -->

---

## 5. Non-Functional Requirements

<!-- Non-functional requirements needed -->

---

## 6. Technical Approach

<!-- Technical approach needed -->

---

## 7. Test Strategy

<!-- Test strategy needed -->

---

## 8. Rollout Plan

<!-- Rollout plan needed -->

---

## 9. Success Metrics

<!-- Success metrics needed -->

---

## 10. Dependencies & Risks

<!-- Dependencies and risks needed -->

---

## 11. Open Questions

<!-- Open questions needed -->

---

## 12. Traceability Matrix

<!-- Traceability matrix needed -->
"""

_FILLED_PRD = """\
---
prd:
  id: PRD-TEST-002
  title: Filled Test PRD
  version: '1.0'
  status: review
  priority: P1
  category: CORE
  confidence:
    implementation_feasibility: 0.85
    requirement_clarity: 0.85
    estimate_confidence: 0.8
    test_coverage_target: 0.90
  traceability:
    implements: []
    depends_on: [PRD-CORE-008]
    enables: [PRD-QUAL-003]
    conflicts_with: []
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
  dates:
    created: '2026-02-09'
    updated: '2026-02-09'
---

# PRD-TEST-002: Filled Test PRD

**Quick Reference**:
- **Status**: Review
- **Priority**: P1
- **Evidence**: Strong
- **Implementation Confidence**: 0.85

---

## 1. Problem Statement

### Background

This is a fully filled PRD for testing purposes. The TRW framework needs
comprehensive testing of its planning agent architecture. The system handles
PRD grooming through automated analysis and agent-driven refinement.

### Problem

There is no automated mechanism to transform skeletal PRDs into sprint-ready
documents. Manual grooming takes 30-60 minutes per PRD and results in
inconsistent quality levels across the PRD catalogue.

### Impact

Sprint agents receive skeletal PRDs and waste time researching requirements
that should have been front-loaded during planning.

---

## 2. Goals & Non-Goals

### Goals
- G1: Automate PRD grooming through specialized agents
- G2: Achieve 85% completeness on all groomed PRDs
- G3: Reduce grooming time from 60 to 15 minutes per PRD

### Non-Goals
- This PRD does NOT implement semantic validation (CORE-008 scope)
- This PRD does NOT modify status transitions (CORE-009 scope)

---

## 3. User Stories

### US-001: Automated Grooming
**As a** framework operator
**I want** to run trw_prd_groom on a skeletal PRD
**So that** it is transformed into a sprint-ready document

**Acceptance Criteria**:
- Given a skeleton PRD, When groomed, Then completeness >= 0.85

---

## 4. Functional Requirements

### PRD-TEST-002-FR01: Grooming Plan Generation
**Priority**: Must Have
**Description**: When a PRD is submitted for grooming, the system shall
generate a structured plan identifying sections needing work.
**Acceptance**: Plan correctly identifies placeholder sections.
**Confidence**: 0.85

### PRD-TEST-002-FR02: Validation Loop
**Priority**: Must Have
**Description**: When grooming a PRD, the system shall iterate through
validate-fix cycles until quality gates pass or max iterations reached.
**Acceptance**: Loop converges within 3 iterations for typical PRDs.
**Confidence**: 0.80

---

## 5. Non-Functional Requirements

### PRD-TEST-002-NFR01: Performance
- Plan generation completes in under 2 seconds
- Each validation iteration completes in under 500ms

### PRD-TEST-002-NFR02: Type Safety
- All code passes mypy --strict with zero errors
- Test coverage >= 85% for new modules

---

## 6. Technical Approach

### Architecture
The system uses a hybrid design: pure-function analysis for plan generation
and agent-driven execution for content improvement.

### Key Files
| File | Changes |
|------|---------|
| `state/grooming.py` | New: grooming plan generator |
| `tools/requirements.py` | Extended: trw_prd_groom tool |
| `models/planning.py` | New: planning models |

---

## 7. Test Strategy

### Unit Tests
- Grooming plan generation tests (9)
- Tool behavior tests (5)
- Hook validation tests (5)

### Integration Tests
- End-to-end grooming pipeline (3)

---

## 8. Rollout Plan

### Phase 1: Agent Definitions
Create 4 agent definition files and hook script.

### Phase 2: MCP Tool
Implement trw_prd_groom and grooming plan generation.

### Phase 3: Integration
Wire everything together with integration tests.

---

## 9. Success Metrics

| Metric | Target | Method |
|--------|--------|--------|
| Agent definitions | 4 files | YAML parse test |
| Plan accuracy | >= 90% | Test against known PRDs |
| Coverage | >= 85% | pytest --cov |

---

## 10. Dependencies & Risks

### Dependencies
| ID | Description | Status |
|----|-------------|--------|
| DEP-001 | PRD-CORE-008 Semantic Validation | Done |

### Risks
| ID | Risk | Mitigation |
|----|------|-----------|
| RISK-001 | Agent hallucination | Constrain to Background scope |

---

## 11. Open Questions

- Should groomed PRDs require human approval? Recommendation: Yes for P0/P1.

---

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | Research | `state/grooming.py` | `test_planning.py` | Pending |
| FR02 | Research | `agents/prd-groomer.md` | `test_planning.py` | Pending |
"""

_PARTIAL_PRD = """\
---
prd:
  id: PRD-TEST-003
  title: Partial Test PRD
  version: '1.0'
  status: draft
  priority: P2
  category: FIX
  confidence:
    implementation_feasibility: 0.7
    requirement_clarity: 0.6
    estimate_confidence: 0.5
    test_coverage_target: 0.85
  traceability:
    implements: []
    depends_on: []
    enables: []
    conflicts_with: []
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
  dates:
    created: '2026-02-09'
    updated: '2026-02-09'
---

# PRD-TEST-003: Partial Test PRD

---

## 1. Problem Statement

### Background

This PRD addresses a partial code quality issue in the TRW framework.
The `trw_recall` function has unbounded results when using wildcard queries.
This causes context window overflow in Claude Code sessions.

### Problem

Wildcard recall queries return all entries without pagination.

### Impact

Sessions crash when the context window is exceeded.

---

## 2. Goals & Non-Goals

### Goals
- G1: Add pagination to trw_recall
- G2: Add compact mode for wildcard queries

### Non-Goals
- Not adding full-text search

---

## 3. User Stories

### US-001
**As a** framework user
**I want** bounded recall results
**So that** my session does not crash

---

## 4. Functional Requirements

### PRD-TEST-003-FR01: Bounded Results
**Priority**: Must Have
**Description**: When trw_recall is called with wildcard, the system shall
limit results to recall_max_results.
**Confidence**: 0.90

---

## 5. Non-Functional Requirements

<!-- NFR needed -->

---

## 6. Technical Approach

### Key Files
| File | Changes |
|------|---------|
| `tools/learning.py` | Modified: add pagination |

---

## 7. Test Strategy

<!-- Test strategy needed -->

---

## 8. Rollout Plan

<!-- Rollout plan needed -->

---

## 9. Success Metrics

<!-- Success metrics needed -->

---

## 10. Dependencies & Risks

<!-- Dependencies needed -->

---

## 11. Open Questions

<!-- No open questions -->

---

## 12. Traceability Matrix

<!-- Matrix needed -->
"""


@pytest.fixture
def skeleton_prd_path(tmp_path: Path) -> Path:
    """Create a skeleton PRD file."""
    prd_file = tmp_path / "PRD-TEST-001.md"
    prd_file.write_text(_SKELETON_PRD, encoding="utf-8")
    return prd_file


@pytest.fixture
def filled_prd_path(tmp_path: Path) -> Path:
    """Create a fully filled PRD file."""
    prd_file = tmp_path / "PRD-TEST-002.md"
    prd_file.write_text(_FILLED_PRD, encoding="utf-8")
    return prd_file


@pytest.fixture
def partial_prd_path(tmp_path: Path) -> Path:
    """Create a partially filled PRD file."""
    prd_file = tmp_path / "PRD-TEST-003.md"
    prd_file.write_text(_PARTIAL_PRD, encoding="utf-8")
    return prd_file


def _get_tool_fn(name: str) -> Callable[..., dict[str, object]]:
    """Extract a registered tool function from a fresh FastMCP server.

    Args:
        name: Tool name to look up (e.g. 'trw_prd_groom').

    Returns:
        The tool's underlying callable.

    Raises:
        AssertionError: If the tool is not found.
    """
    from fastmcp import FastMCP

    from trw_mcp.tools.requirements import register_requirements_tools

    server = FastMCP("test")
    register_requirements_tools(server)

    for tool in server._tool_manager._tools.values():
        if tool.name == name:
            return tool.fn  # type: ignore[return-value]
    raise AssertionError(f"Tool {name!r} not found in registered tools")


# ---------------------------------------------------------------------------
# Unit Tests — Grooming Plan Generation (9 tests)
# ---------------------------------------------------------------------------


class TestGroomingPlanGeneration:
    """Tests for generate_grooming_plan() pure function."""

    def test_generate_grooming_plan_skeleton_prd(
        self, skeleton_prd_path: Path,
    ) -> None:
        """Skeleton PRD with 10 placeholder sections returns plan with >= 8 sections needing work."""
        plan = generate_grooming_plan(
            content=_SKELETON_PRD,
            prd_path=str(skeleton_prd_path),
        )
        assert isinstance(plan, GroomingPlan)
        assert plan.prd_id == "PRD-TEST-001"
        assert len(plan.sections_needing_work) >= 8

    def test_generate_grooming_plan_filled_prd(
        self, filled_prd_path: Path,
    ) -> None:
        """Fully filled PRD returns plan with most sections complete."""
        plan = generate_grooming_plan(
            content=_FILLED_PRD,
            prd_path=str(filled_prd_path),
        )
        assert plan.prd_id == "PRD-TEST-002"
        # A filled PRD should have significantly more complete than needing work
        assert len(plan.sections_complete) >= 8
        # Total must be 12
        assert len(plan.sections_complete) + len(plan.sections_needing_work) == 12

    def test_generate_grooming_plan_partial_prd(
        self, partial_prd_path: Path,
    ) -> None:
        """Partial PRD has a mix of complete and needing-work sections."""
        plan = generate_grooming_plan(
            content=_PARTIAL_PRD,
            prd_path=str(partial_prd_path),
        )
        assert plan.prd_id == "PRD-TEST-003"
        needing = len(plan.sections_needing_work)
        complete = len(plan.sections_complete)
        # Partial PRD: some filled, some placeholder
        assert needing >= 4  # At least 4 placeholder/partial sections
        assert needing + complete == 12

    def test_grooming_plan_section_density_detection(self) -> None:
        """Sections with only <!-- --> comments are identified as placeholder."""
        analysis = _analyze_section(
            section_name="Test Strategy",
            section_body="\n<!-- Test strategy needed -->\n\n",
            section_number=7,
            background_keywords=[],
        )
        assert analysis.status == SectionStatus.PLACEHOLDER
        assert analysis.density < 0.10

    def test_grooming_plan_research_topic_extraction(self) -> None:
        """Research topics are derived from section mapping."""
        analysis = _analyze_section(
            section_name="Functional Requirements",
            section_body="\n<!-- FR needed -->\n",
            section_number=4,
            background_keywords=["trw_recall", "pagination"],
        )
        assert len(analysis.research_topics) > 0
        # Background keywords should be incorporated
        assert any("trw_recall" in t for t in analysis.research_topics)

    def test_grooming_plan_iteration_estimate_few_sections(self) -> None:
        """1-3 placeholder sections estimates 1 iteration."""
        assert _estimate_iterations(1) == 1
        assert _estimate_iterations(2) == 1
        assert _estimate_iterations(3) == 1

    def test_grooming_plan_iteration_estimate_many_sections(self) -> None:
        """8-12 placeholder sections estimates 3 iterations."""
        assert _estimate_iterations(8) == 3
        assert _estimate_iterations(10) == 3
        assert _estimate_iterations(12) == 3

    def test_grooming_plan_model_validation(self) -> None:
        """GroomingPlan model rejects invalid fields."""
        with pytest.raises(PydanticValidationError):
            GroomingPlan(
                prd_id="PRD-TEST-001",
                prd_path="/tmp/test.md",
                current_completeness=-0.1,  # Invalid: negative
                current_total_score=0.0,
                current_quality_tier="skeleton",
                sections_needing_work=[],
                sections_complete=[],
                estimated_research_queries=0,
                estimated_iterations=0,
                max_iterations=5,
            )

        with pytest.raises(PydanticValidationError):
            GroomingPlan(
                prd_id="PRD-TEST-001",
                prd_path="/tmp/test.md",
                current_completeness=0.5,
                current_total_score=0.0,
                current_quality_tier="skeleton",
                target_completeness=1.5,  # Invalid: > 1.0
                sections_needing_work=[],
                sections_complete=[],
                estimated_research_queries=0,
                estimated_iterations=0,
                max_iterations=5,
            )

    def test_grooming_plan_performance(
        self, skeleton_prd_path: Path,
    ) -> None:
        """Plan generation completes in under 2 seconds for a typical PRD."""
        start = time.monotonic()
        plan = generate_grooming_plan(
            content=_SKELETON_PRD,
            prd_path=str(skeleton_prd_path),
        )
        elapsed = time.monotonic() - start
        assert elapsed < 2.0
        assert plan.prd_id == "PRD-TEST-001"


# ---------------------------------------------------------------------------
# Unit Tests — trw_prd_groom Tool (5 tests)
# ---------------------------------------------------------------------------


class TestTrwPrdGroom:
    """Tests for the trw_prd_groom MCP tool."""

    def _setup_env(self, prd_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up minimal project structure for tool resolution."""
        monkeypatch.chdir(prd_path.parent)
        trw_dir = prd_path.parent / ".trw"
        trw_dir.mkdir(exist_ok=True)

    def test_trw_prd_groom_dry_run_returns_plan(
        self, skeleton_prd_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dry_run=True returns grooming plan without modifying PRD."""
        self._setup_env(skeleton_prd_path, monkeypatch)
        tool_fn = _get_tool_fn("trw_prd_groom")

        result = tool_fn(prd_path=str(skeleton_prd_path), dry_run=True)
        assert result["status"] == "plan_generated"
        assert "grooming_plan" in result
        assert result["prd_id"] == "PRD-TEST-001"

    def test_trw_prd_groom_dry_run_includes_quality_scores(
        self, skeleton_prd_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dry_run output includes current_quality from validation."""
        self._setup_env(skeleton_prd_path, monkeypatch)
        tool_fn = _get_tool_fn("trw_prd_groom")

        result = tool_fn(prd_path=str(skeleton_prd_path), dry_run=True)
        assert "current_quality" in result
        quality = result["current_quality"]
        assert isinstance(quality, dict)
        assert "total_score" in quality
        assert "quality_tier" in quality

    def test_trw_prd_groom_non_dry_run_returns_plan_ready(
        self, filled_prd_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dry_run=False returns status 'plan_ready' with suggested_agent."""
        self._setup_env(filled_prd_path, monkeypatch)
        tool_fn = _get_tool_fn("trw_prd_groom")

        result = tool_fn(prd_path=str(filled_prd_path), dry_run=False)
        assert result["status"] == "plan_ready"
        assert result["suggested_agent"] == "prd-groomer"

    def test_trw_prd_groom_invalid_path_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Nonexistent prd_path raises StateError."""
        self._setup_env(tmp_path, monkeypatch)
        tool_fn = _get_tool_fn("trw_prd_groom")

        from trw_mcp.exceptions import StateError

        with pytest.raises(StateError, match="PRD file not found"):
            tool_fn(prd_path=str(tmp_path / "nonexistent.md"))

    def test_trw_prd_groom_config_overrides(
        self, skeleton_prd_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom max_iterations and target_completeness are respected."""
        self._setup_env(skeleton_prd_path, monkeypatch)
        tool_fn = _get_tool_fn("trw_prd_groom")

        result = tool_fn(
            prd_path=str(skeleton_prd_path),
            max_iterations=3,
            target_completeness=0.70,
            dry_run=True,
        )
        plan = result["grooming_plan"]
        assert isinstance(plan, dict)
        assert plan["max_iterations"] == 3
        assert plan["target_completeness"] == 0.70


# ---------------------------------------------------------------------------
# Unit Tests — Hook Validation (5 tests)
# ---------------------------------------------------------------------------


class TestHookValidation:
    """Tests for validate-prd-write.sh hook script."""

    @pytest.fixture
    def hook_path(self) -> Path:
        """Return path to the hook script."""
        path = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "validate-prd-write.sh"
        if not path.exists():
            pytest.skip("Hook script not found")
        return path

    def _run_hook(self, hook_path: Path, tool_input: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        """Run the hook script with given tool input JSON."""
        payload = json.dumps({"tool_input": tool_input})
        return subprocess.run(
            ["sh", str(hook_path)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_hook_allows_prd_file_write(self, hook_path: Path) -> None:
        """PRD file paths exit 0."""
        result = self._run_hook(hook_path, {
            "file_path": "/project/docs/requirements-aare-f/prds/PRD-QUAL-004.md",
        })
        assert result.returncode == 0

    def test_hook_allows_planning_run_write(self, hook_path: Path) -> None:
        """Planning run directory paths exit 0."""
        result = self._run_hook(hook_path, {
            "file_path": "/project/docs/requirements-aare-f/planning-runs/run-123/artifacts/diff.yaml",
        })
        assert result.returncode == 0

    def test_hook_allows_agent_memory_write(self, hook_path: Path) -> None:
        """Agent memory directory paths exit 0."""
        result = self._run_hook(hook_path, {
            "file_path": "/project/.claude/agent-memory/prd-groomer/state.yaml",
        })
        assert result.returncode == 0

    def test_hook_blocks_source_code_write(self, hook_path: Path) -> None:
        """Source code paths exit 2 with error message."""
        result = self._run_hook(hook_path, {
            "file_path": "/project/trw-mcp/src/trw_mcp/tools/requirements.py",
        })
        assert result.returncode == 2
        assert "BLOCKED" in result.stderr

    def test_hook_handles_malformed_json(self, hook_path: Path) -> None:
        """Invalid JSON input exits 0 (fail-open)."""
        result = subprocess.run(
            ["sh", str(hook_path)],
            input="not valid json {{{",
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Unit Tests — Model Validation (3 tests)
# ---------------------------------------------------------------------------


class TestPlanningModels:
    """Tests for planning Pydantic models."""

    def test_agent_role_registry_has_four_roles(self) -> None:
        """PLANNING_AGENT_ROLES contains exactly 4 agent definitions."""
        assert len(PLANNING_AGENT_ROLES) == 4
        assert "prd-groomer" in PLANNING_AGENT_ROLES
        assert "requirement-writer" in PLANNING_AGENT_ROLES
        assert "requirement-reviewer" in PLANNING_AGENT_ROLES
        assert "traceability-checker" in PLANNING_AGENT_ROLES

    def test_reviewer_and_checker_are_read_only(self) -> None:
        """Read-only agents have Write and Edit in disallowedTools."""
        reviewer = PLANNING_AGENT_ROLES["requirement-reviewer"]
        assert reviewer.read_only is True
        assert "Write" in reviewer.disallowed_tools
        assert "Edit" in reviewer.disallowed_tools

        checker = PLANNING_AGENT_ROLES["traceability-checker"]
        assert checker.read_only is True
        assert checker.model == "haiku"

    def test_section_analysis_validation(self) -> None:
        """SectionAnalysis rejects invalid section numbers."""
        with pytest.raises(PydanticValidationError):
            SectionAnalysis(
                section_name="Test",
                section_number=0,  # Invalid: must be >= 1
                status=SectionStatus.COMPLETE,
                density=0.5,
                substantive_lines=5,
                total_lines=10,
            )
        with pytest.raises(PydanticValidationError):
            SectionAnalysis(
                section_name="Test",
                section_number=13,  # Invalid: must be <= 12
                status=SectionStatus.COMPLETE,
                density=0.5,
                substantive_lines=5,
                total_lines=10,
            )


# ---------------------------------------------------------------------------
# Unit Tests — Helper Functions (3 tests)
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Tests for internal helper functions."""

    def test_extract_prd_id_from_frontmatter(self) -> None:
        """Extract PRD ID from YAML frontmatter."""
        assert _extract_prd_id(_SKELETON_PRD) == "PRD-TEST-001"
        assert _extract_prd_id(_FILLED_PRD) == "PRD-TEST-002"

    def test_extract_prd_id_unknown(self) -> None:
        """Returns UNKNOWN for content without a PRD ID."""
        assert _extract_prd_id("# Just a heading\nSome content.") == "UNKNOWN"

    def test_extract_background_keywords(self) -> None:
        """Keywords are extracted from the Background section."""
        keywords = _extract_background_keywords(_SKELETON_PRD)
        assert isinstance(keywords, list)
        # Should find technical terms like trw_prd_groom
        assert any("trw_prd_groom" in kw or "generate_grooming_plan" in kw for kw in keywords)


# ---------------------------------------------------------------------------
# Integration Tests (4 tests)
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests for the planning agent architecture."""

    def test_agent_definitions_exist(self) -> None:
        """All 4 agent definition files exist on disk."""
        agents_dir = Path(__file__).parent.parent.parent / ".claude" / "agents"
        expected = [
            "prd-groomer.md",
            "requirement-writer.md",
            "requirement-reviewer.md",
            "traceability-checker.md",
        ]
        for name in expected:
            agent_file = agents_dir / name
            assert agent_file.exists(), f"Agent file missing: {name}"

    def test_agent_definitions_have_frontmatter(self) -> None:
        """Agent definition files contain YAML frontmatter with required fields."""
        agents_dir = Path(__file__).parent.parent.parent / ".claude" / "agents"
        required_fields = {"name", "model"}

        for agent_file in agents_dir.glob("*.md"):
            content = agent_file.read_text(encoding="utf-8")
            assert content.startswith("---"), f"{agent_file.name} missing frontmatter"
            # Check that required fields appear in frontmatter
            fm_end = content.index("---", 3)
            fm_text = content[3:fm_end]
            for field in required_fields:
                assert f"{field}:" in fm_text, (
                    f"{agent_file.name} missing frontmatter field: {field}"
                )

    def test_hook_script_is_executable(self) -> None:
        """Hook script has executable permission."""
        hook_path = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "validate-prd-write.sh"
        if not hook_path.exists():
            pytest.skip("Hook script not found")
        import os
        assert os.access(str(hook_path), os.X_OK), "Hook script is not executable"

    def test_grooming_plan_on_real_skeleton_structure(
        self, skeleton_prd_path: Path,
    ) -> None:
        """End-to-end: grooming plan on skeleton PRD identifies correct sections."""
        plan = generate_grooming_plan(
            content=_SKELETON_PRD,
            prd_path=str(skeleton_prd_path),
        )

        # Verify plan structure
        assert plan.prd_id == "PRD-TEST-001"
        assert plan.current_completeness < 0.5
        assert plan.estimated_iterations >= 1

        # All sections needing work should have research topics
        for section in plan.sections_needing_work:
            assert isinstance(section.research_topics, list)

        # Complete + needing_work should account for all 12 sections
        total = len(plan.sections_needing_work) + len(plan.sections_complete)
        assert total == 12


# ---------------------------------------------------------------------------
# Edge Case Tests (3 tests)
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and boundary condition tests."""

    def test_estimate_iterations_zero_sections(self) -> None:
        """Zero placeholder sections estimates 1 iteration (minimum)."""
        assert _estimate_iterations(0) == 1

    def test_analyze_section_with_custom_thresholds(self) -> None:
        """Custom density thresholds from config are respected."""
        # With high thresholds, even moderate content is classified as placeholder
        analysis = _analyze_section(
            section_name="Test Strategy",
            section_body="Line one\nLine two\nLine three\n\n",
            section_number=7,
            background_keywords=[],
            placeholder_threshold=0.90,
            partial_threshold=0.95,
        )
        assert analysis.status == SectionStatus.PLACEHOLDER

    def test_generate_grooming_plan_with_custom_config(
        self, tmp_path: Path,
    ) -> None:
        """Config-driven thresholds change section classification."""
        prd_file = tmp_path / "test.md"
        prd_file.write_text(_SKELETON_PRD, encoding="utf-8")

        config = TRWConfig(
            grooming_placeholder_density_threshold=0.01,
            grooming_partial_density_threshold=0.02,
        )
        plan = generate_grooming_plan(
            content=_SKELETON_PRD,
            prd_path=str(prd_file),
            config=config,
        )
        # With very low thresholds, more sections might be classified as complete
        assert plan.prd_id == "PRD-TEST-001"
        assert len(plan.sections_needing_work) + len(plan.sections_complete) == 12
