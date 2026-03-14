"""Tests for PRD-FIX-053-FR05: Multi-step PRD auto-progression.

Verifies that auto_progress_prds steps through intermediate states via BFS
instead of attempting an invalid direct jump and returning invalid_transition.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus
from trw_mcp.state.prd_utils import TransitionResult


def _write_prd(prds_dir: Path, prd_id: str, status: str) -> Path:
    """Write a minimal PRD file with the given status."""
    prds_dir.mkdir(parents=True, exist_ok=True)
    content = f"""---
prd:
  id: {prd_id}
  title: Test PRD
  status: {status}
  version: '1.0'
---

# {prd_id}

Test PRD content with enough substantive lines to pass content density checks.
This line adds more substantive content to ensure density threshold is met.
And this line provides additional content for the quality checks in the system.
The PRD describes the implementation approach and its goals for validation.
"""
    prd_file = prds_dir / f"{prd_id}.md"
    prd_file.write_text(content, encoding="utf-8")
    return prd_file


def _write_run(run_dir: Path, prd_ids: list[str]) -> None:
    """Write a run.yaml with prd_scope."""
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(exist_ok=True)
    run_yaml = meta_dir / "run.yaml"
    run_yaml.write_text(
        "run_id: test-run\ntask: test\nprd_scope:\n"
        + "".join(f"  - {pid}\n" for pid in prd_ids),
        encoding="utf-8",
    )


def _guard_always_allowed(
    current: PRDStatus, target: PRDStatus, content: str, config: object
) -> TransitionResult:
    """Mock guard that always allows transitions (for testing BFS logic)."""
    return TransitionResult(allowed=True, reason="test_mock_allow")


class TestMultiStepProgression:
    """FR05: BFS traversal through intermediate states."""

    def test_draft_to_done_via_intermediate_steps(
        self, tmp_path: Path
    ) -> None:
        """PRD in draft + phase=deliver → steps through review→approved→implemented→done."""
        from trw_mcp.state.validation.prd_progression import auto_progress_prds

        prds_dir = tmp_path / "prds"
        _write_prd(prds_dir, "PRD-TEST-001", "draft")

        run_dir = tmp_path / "runs" / "test-run"
        _write_run(run_dir, ["PRD-TEST-001"])

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with patch(
            "trw_mcp.state.validation.prd_progression.check_transition_guards",
            side_effect=_guard_always_allowed,
        ):
            results = auto_progress_prds(run_dir, "deliver", prds_dir, config)

        assert len(results) >= 1
        prd_result = next((r for r in results if r["prd_id"] == "PRD-TEST-001"), None)
        assert prd_result is not None, "PRD-TEST-001 must appear in results"
        assert prd_result.get("applied") is True, (
            f"Expected applied=True but got: {prd_result}"
        )
        assert prd_result.get("to_status") == "done"
        # Must NOT show invalid_transition
        assert prd_result.get("reason") != "invalid_transition"

    def test_implemented_to_done_single_step(
        self, tmp_path: Path
    ) -> None:
        """PRD in implemented + phase=deliver → single step to done."""
        from trw_mcp.state.validation.prd_progression import auto_progress_prds

        prds_dir = tmp_path / "prds"
        _write_prd(prds_dir, "PRD-TEST-002", "implemented")

        run_dir = tmp_path / "runs" / "test-run"
        _write_run(run_dir, ["PRD-TEST-002"])

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with patch(
            "trw_mcp.state.validation.prd_progression.check_transition_guards",
            side_effect=_guard_always_allowed,
        ):
            results = auto_progress_prds(run_dir, "deliver", prds_dir, config)

        prd_result = next((r for r in results if r["prd_id"] == "PRD-TEST-002"), None)
        assert prd_result is not None
        assert prd_result.get("applied") is True
        assert prd_result.get("to_status") == "done"

    def test_done_status_no_transition_attempted(
        self, tmp_path: Path
    ) -> None:
        """PRD already in done → terminal state, no transition."""
        from trw_mcp.state.validation.prd_progression import auto_progress_prds

        prds_dir = tmp_path / "prds"
        _write_prd(prds_dir, "PRD-TEST-003", "done")

        run_dir = tmp_path / "runs" / "test-run"
        _write_run(run_dir, ["PRD-TEST-003"])

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        results = auto_progress_prds(run_dir, "deliver", prds_dir, config)

        # done is terminal — should not appear in results
        prd_result = next((r for r in results if r.get("prd_id") == "PRD-TEST-003"), None)
        assert prd_result is None, "Terminal status 'done' must not produce a result entry"

    def test_no_invalid_transition_errors(
        self, tmp_path: Path
    ) -> None:
        """No result should have reason='invalid_transition' after BFS fix."""
        from trw_mcp.state.validation.prd_progression import auto_progress_prds

        prds_dir = tmp_path / "prds"
        _write_prd(prds_dir, "PRD-TEST-004", "draft")
        _write_prd(prds_dir, "PRD-TEST-005", "review")
        _write_prd(prds_dir, "PRD-TEST-006", "approved")

        run_dir = tmp_path / "runs" / "test-run"
        _write_run(run_dir, ["PRD-TEST-004", "PRD-TEST-005", "PRD-TEST-006"])

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with patch(
            "trw_mcp.state.validation.prd_progression.check_transition_guards",
            side_effect=_guard_always_allowed,
        ):
            results = auto_progress_prds(run_dir, "deliver", prds_dir, config)

        invalid = [r for r in results if r.get("reason") == "invalid_transition"]
        assert len(invalid) == 0, (
            f"Expected no invalid_transition errors, got: {invalid}"
        )

    def test_draft_file_updated_to_done(
        self, tmp_path: Path
    ) -> None:
        """PRD file on disk must reflect final 'done' status after multi-step progression."""
        from trw_mcp.state.validation.prd_progression import auto_progress_prds
        from trw_mcp.state.prd_utils import parse_frontmatter

        prds_dir = tmp_path / "prds"
        prd_file = _write_prd(prds_dir, "PRD-TEST-007", "draft")

        run_dir = tmp_path / "runs" / "test-run"
        _write_run(run_dir, ["PRD-TEST-007"])

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with patch(
            "trw_mcp.state.validation.prd_progression.check_transition_guards",
            side_effect=_guard_always_allowed,
        ):
            auto_progress_prds(run_dir, "deliver", prds_dir, config)

        content = prd_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        final_status = str(fm.get("status", "")).lower()
        assert final_status == "done", (
            f"PRD file must have status=done after multi-step progression, got: {final_status!r}"
        )

    def test_review_to_done_skips_two_steps(
        self, tmp_path: Path
    ) -> None:
        """PRD in review + phase=deliver → approved→implemented→done via BFS."""
        from trw_mcp.state.validation.prd_progression import auto_progress_prds

        prds_dir = tmp_path / "prds"
        _write_prd(prds_dir, "PRD-TEST-008", "review")

        run_dir = tmp_path / "runs" / "test-run"
        _write_run(run_dir, ["PRD-TEST-008"])

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with patch(
            "trw_mcp.state.validation.prd_progression.check_transition_guards",
            side_effect=_guard_always_allowed,
        ):
            results = auto_progress_prds(run_dir, "deliver", prds_dir, config)

        prd_result = next((r for r in results if r["prd_id"] == "PRD-TEST-008"), None)
        assert prd_result is not None
        assert prd_result.get("applied") is True
        assert prd_result.get("to_status") == "done"
        assert prd_result.get("reason") != "invalid_transition"

    def test_guard_failure_stops_progression_at_first_failure(
        self, tmp_path: Path
    ) -> None:
        """Guard failure at first step stops progression — PRD stays at original status."""
        from trw_mcp.state.validation.prd_progression import auto_progress_prds

        prds_dir = tmp_path / "prds"
        _write_prd(prds_dir, "PRD-TEST-009", "draft")

        run_dir = tmp_path / "runs" / "test-run"
        _write_run(run_dir, ["PRD-TEST-009"])

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        def guard_always_denied(
            current: PRDStatus, target: PRDStatus, content: str, cfg: object
        ) -> TransitionResult:
            return TransitionResult(allowed=False, reason="test_mock_deny")

        with patch(
            "trw_mcp.state.validation.prd_progression.check_transition_guards",
            side_effect=guard_always_denied,
        ):
            results = auto_progress_prds(run_dir, "deliver", prds_dir, config)

        prd_result = next((r for r in results if r["prd_id"] == "PRD-TEST-009"), None)
        assert prd_result is not None
        assert prd_result.get("applied") is False
        assert prd_result.get("guard_failed") is True

    def test_compute_transition_path_draft_to_done(self) -> None:
        """BFS finds draft→review→approved→implemented→done path."""
        from trw_mcp.state.validation.prd_progression import _compute_transition_path

        path = _compute_transition_path(PRDStatus.DRAFT, PRDStatus.DONE)
        assert path is not None
        # Path must include at least implemented→done
        assert PRDStatus.DONE in path
        assert path[-1] == PRDStatus.DONE

    def test_compute_transition_path_implemented_to_done(self) -> None:
        """BFS finds implemented→done single-step path."""
        from trw_mcp.state.validation.prd_progression import _compute_transition_path

        path = _compute_transition_path(PRDStatus.IMPLEMENTED, PRDStatus.DONE)
        assert path is not None
        assert path == [PRDStatus.DONE]

    def test_compute_transition_path_already_reachable(self) -> None:
        """BFS from draft to review returns [review] (direct step)."""
        from trw_mcp.state.validation.prd_progression import _compute_transition_path

        path = _compute_transition_path(PRDStatus.DRAFT, PRDStatus.REVIEW)
        assert path is not None
        assert path == [PRDStatus.REVIEW]
