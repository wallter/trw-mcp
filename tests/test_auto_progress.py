"""Tests for PRD-CORE-025: PRD Status Auto-Progression.

Covers:
- PHASE_STATUS_MAPPING constant (FR01)
- auto_progress_prds function (FR02)
- Guard respect (FR05)
- Dry-run mode (FR07)
- Terminal/identity transition skipping (NFR03)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus
from trw_mcp.state.validation import (
    PHASE_STATUS_MAPPING,
    _TERMINAL_STATUSES,
    auto_progress_prds,
)


# --- Fixtures ---


@pytest.fixture()
def config() -> TRWConfig:
    """Default config for tests."""
    return TRWConfig()


@pytest.fixture()
def prds_dir(tmp_path: Path) -> Path:
    """Create a temp prds directory with sample PRD files."""
    d = tmp_path / "docs" / "requirements-aare-f" / "prds"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory with prd_scope."""
    d = tmp_path / "docs" / "task" / "runs" / "20260211T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _write_prd(prds_dir: Path, prd_id: str, status: str, density: float = 0.5) -> Path:
    """Write a PRD file with given status and enough content for density checks."""
    content_lines = "\n".join(
        f"Requirement {i}: description of the requirement for testing."
        for i in range(int(density * 100))
    )
    prd_file = prds_dir / f"{prd_id}.md"
    prd_file.write_text(
        f"---\nprd:\n  id: {prd_id}\n  title: Test PRD\n"
        f"  status: {status}\n  priority: P1\n  category: CORE\n---\n"
        f"# {prd_id}\n\n{content_lines}\n",
        encoding="utf-8",
    )
    return prd_file


def _write_run_yaml(run_dir: Path, prd_scope: list[str]) -> None:
    """Write run.yaml with prd_scope."""
    import yaml

    (run_dir / "meta" / "run.yaml").write_text(
        yaml.dump({
            "run_id": "test-run",
            "status": "active",
            "phase": "implement",
            "task_name": "test-task",
            "prd_scope": prd_scope,
        }),
        encoding="utf-8",
    )


# --- PHASE_STATUS_MAPPING ---


class TestPhaseStatusMapping:
    """PRD-CORE-025-FR01: Phase-to-status mapping."""

    def test_mapping_has_four_entries(self) -> None:
        """4 entries: plan, implement, validate, deliver."""
        assert len(PHASE_STATUS_MAPPING) == 4

    def test_plan_maps_to_review(self) -> None:
        assert PHASE_STATUS_MAPPING["plan"] == PRDStatus.REVIEW

    def test_implement_maps_to_implemented(self) -> None:
        assert PHASE_STATUS_MAPPING["implement"] == PRDStatus.IMPLEMENTED

    def test_validate_maps_to_done(self) -> None:
        assert PHASE_STATUS_MAPPING["validate"] == PRDStatus.DONE

    def test_deliver_maps_to_done(self) -> None:
        assert PHASE_STATUS_MAPPING["deliver"] == PRDStatus.DONE

    def test_research_not_in_mapping(self) -> None:
        assert "research" not in PHASE_STATUS_MAPPING


# --- auto_progress_prds ---


class TestAutoProgressPrds:
    """PRD-CORE-025-FR02: auto_progress_prds function."""

    def test_returns_empty_for_unmapped_phase(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        _write_prd(prds_dir, "PRD-CORE-001", "draft")
        result = auto_progress_prds(run_dir, "research", prds_dir, config)
        assert result == []

    def test_advances_draft_to_review_on_plan_exit(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        _write_prd(prds_dir, "PRD-CORE-001", "draft", density=0.5)
        result = auto_progress_prds(run_dir, "plan", prds_dir, config)
        assert len(result) == 1
        assert result[0]["prd_id"] == "PRD-CORE-001"
        assert result[0]["from_status"] == "draft"
        assert result[0]["to_status"] == "review"
        assert result[0]["applied"] is True
        # Verify file was actually updated
        content = (prds_dir / "PRD-CORE-001.md").read_text(encoding="utf-8")
        assert "status: review" in content

    def test_advances_approved_to_implemented_on_implement_exit(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        _write_prd(prds_dir, "PRD-CORE-001", "approved")
        result = auto_progress_prds(run_dir, "implement", prds_dir, config)
        assert len(result) == 1
        assert result[0]["to_status"] == "implemented"
        assert result[0]["applied"] is True

    def test_advances_implemented_to_done_on_validate_exit(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        _write_prd(prds_dir, "PRD-CORE-001", "implemented")
        result = auto_progress_prds(run_dir, "validate", prds_dir, config)
        assert len(result) == 1
        assert result[0]["to_status"] == "done"
        assert result[0]["applied"] is True

    def test_skips_prds_not_in_scope(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        _write_prd(prds_dir, "PRD-CORE-001", "draft", density=0.5)
        _write_prd(prds_dir, "PRD-CORE-002", "draft", density=0.5)
        result = auto_progress_prds(run_dir, "plan", prds_dir, config)
        # Only PRD-CORE-001 is in scope
        prd_ids = [r["prd_id"] for r in result]
        assert "PRD-CORE-001" in prd_ids
        assert "PRD-CORE-002" not in prd_ids

    def test_skips_terminal_statuses(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001", "PRD-CORE-002", "PRD-CORE-003"])
        _write_prd(prds_dir, "PRD-CORE-001", "done")
        _write_prd(prds_dir, "PRD-CORE-002", "merged")
        _write_prd(prds_dir, "PRD-CORE-003", "deprecated")
        result = auto_progress_prds(run_dir, "deliver", prds_dir, config)
        assert result == []

    def test_terminal_statuses_set(self) -> None:
        assert PRDStatus.DONE in _TERMINAL_STATUSES
        assert PRDStatus.MERGED in _TERMINAL_STATUSES
        assert PRDStatus.DEPRECATED in _TERMINAL_STATUSES

    def test_skips_invalid_transition(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        # draft → implemented is not a valid transition
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        _write_prd(prds_dir, "PRD-CORE-001", "draft")
        result = auto_progress_prds(run_dir, "implement", prds_dir, config)
        assert len(result) == 1
        assert result[0]["applied"] is False
        assert result[0]["reason"] == "invalid_transition"

    def test_returns_empty_for_no_prd_scope(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, [])
        result = auto_progress_prds(run_dir, "plan", prds_dir, config)
        assert result == []


class TestAutoProgressGuards:
    """PRD-CORE-025-FR05: Guard respect."""

    def test_guard_failure_prevents_progression(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        # Very low content density — lots of non-substantive lines (blanks, headings, rules)
        # to push density below 0.30 threshold
        prd_file = prds_dir / "PRD-CORE-001.md"
        # 8 lines of frontmatter/headings/rules, only 1 substantive → density ~0.11
        prd_file.write_text(
            "---\nprd:\n  id: PRD-CORE-001\n  title: T\n"
            "  status: draft\n  priority: P1\n  category: CORE\n---\n"
            "# Heading\n\n---\n\n## Section\n\n---\n\n### Sub\n\n---\n\n",
            encoding="utf-8",
        )
        result = auto_progress_prds(run_dir, "plan", prds_dir, config)
        assert len(result) == 1
        assert result[0]["applied"] is False
        assert result[0]["guard_failed"] is True

    def test_multiple_prds_partial_progression(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001", "PRD-CORE-002"])
        # One with good density, one with low density (mostly non-substantive lines)
        _write_prd(prds_dir, "PRD-CORE-001", "draft", density=0.5)
        prd_file = prds_dir / "PRD-CORE-002.md"
        prd_file.write_text(
            "---\nprd:\n  id: PRD-CORE-002\n  title: T\n"
            "  status: draft\n  priority: P1\n  category: CORE\n---\n"
            "# Heading\n\n---\n\n## Section\n\n---\n\n### Sub\n\n---\n\n",
            encoding="utf-8",
        )
        result = auto_progress_prds(run_dir, "plan", prds_dir, config)
        applied = [r for r in result if r.get("applied")]
        failed = [r for r in result if r.get("guard_failed")]
        assert len(applied) == 1
        assert len(failed) == 1


class TestAutoProgressDryRun:
    """PRD-CORE-025-FR07: Dry-run mode."""

    def test_dry_run_does_not_write(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        _write_prd(prds_dir, "PRD-CORE-001", "draft", density=0.5)
        result = auto_progress_prds(
            run_dir, "plan", prds_dir, config, dry_run=True,
        )
        assert len(result) == 1
        assert result[0]["applied"] is False
        assert result[0]["would_apply"] is True
        # File should NOT be modified
        content = (prds_dir / "PRD-CORE-001.md").read_text(encoding="utf-8")
        assert "status: draft" in content

    def test_dry_run_guard_failure_shows_would_apply_false(
        self, run_dir: Path, prds_dir: Path, config: TRWConfig,
    ) -> None:
        _write_run_yaml(run_dir, ["PRD-CORE-001"])
        prd_file = prds_dir / "PRD-CORE-001.md"
        # Low density content (mostly headings, rules, blanks)
        prd_file.write_text(
            "---\nprd:\n  id: PRD-CORE-001\n  title: T\n"
            "  status: draft\n  priority: P1\n  category: CORE\n---\n"
            "# Heading\n\n---\n\n## Section\n\n---\n\n### Sub\n\n---\n\n",
            encoding="utf-8",
        )
        result = auto_progress_prds(
            run_dir, "plan", prds_dir, config, dry_run=True,
        )
        assert len(result) == 1
        assert result[0]["applied"] is False
        assert result[0].get("would_apply") is False
        assert result[0]["guard_failed"] is True
