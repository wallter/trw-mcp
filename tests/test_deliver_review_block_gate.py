"""Tests for the review-block deliver gate (F4 truthfulness defect).

A ``review.yaml`` with ``verdict=block`` + critical findings is the deepest
truthfulness gate. Pre-fix it only emitted a ``review_warning`` and returned
``success=True`` — the block verdict carried zero enforcement weight for the
exact case it exists to catch. These tests drive the REAL ``trw_deliver`` path
and assert that:

- STANDARD / COMPREHENSIVE + verdict=block + critical findings -> success=False
  (delivery blocked, review-block reason present).
- The same with allow_unverified=True + reason -> delivery PROCEEDS (the
  sanctioned CONSTITUTION Deliver Gate Path 3 escape hatch stays open).
- verdict=pass / verdict=warn -> delivers normally (no over-block).
- MINIMAL complexity is NOT over-blocked by a block verdict.
- integration_review_block / review_scope_block behavior is unchanged.
- F4: a non-overridable hard gate (integration_review_block) collects a
  co-firing human review_block into the errors list instead of silently
  dropping it on the first cascade match.

Real success/blocked values are asserted — the deliver unit is not mocked.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.tools.ceremony import register_ceremony_tools


def _make_deliver_fn() -> Callable[..., dict[str, Any]]:
    server = FastMCP("test")
    register_ceremony_tools(server)
    return get_tools_sync(server)["trw_deliver"].fn


def _write_run(
    tmp_path: Path,
    *,
    complexity_class: str,
    review_verdict: str | None = None,
    review_critical: int = 0,
    integration_verdict: str | None = None,
    with_passing_build: bool = True,
) -> Path:
    """Build a synthetic run dir + .trw skeleton for a real deliver call.

    - run.yaml carries the complexity_class so the review gate can decide
      whether verdict=block is a hard block (STANDARD/COMPREHENSIVE) or a soft
      warning (MINIMAL).
    - review.yaml is written only when review_verdict is set.
    - integration-review.yaml is written only when integration_verdict is set.
    - events.jsonl carries a passing build_check_complete + a work event so the
      build gate / premature-delivery gate do not fire first and mask the
      review-block gate under test.
    """
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "reflections").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)

    run_dir = tmp_path / "docs" / "task" / "runs" / "20260604T000000Z-test"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        f"run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\ncomplexity_class: {complexity_class}\n",
        encoding="utf-8",
    )

    if review_verdict is not None:
        (meta / "review.yaml").write_text(
            f"verdict: {review_verdict}\ncritical_count: {review_critical}\n",
            encoding="utf-8",
        )
    if integration_verdict is not None:
        findings = ""
        if integration_verdict == "block":
            findings = "findings:\n  - severity: critical\n    message: boom\n"
        (meta / "integration-review.yaml").write_text(
            f"verdict: {integration_verdict}\n{findings}",
            encoding="utf-8",
        )

    lines: list[str] = [
        json.dumps({"ts": "2026-06-04T00:00:00Z", "event": "session_start"}),
        json.dumps({"ts": "2026-06-04T00:00:01Z", "event": "file_modified", "data": {"path": "src/x.py"}}),
    ]
    if with_passing_build:
        lines.append(
            json.dumps(
                {
                    "ts": "2026-06-04T00:00:02Z",
                    "event": "build_check_complete",
                    "tests_passed": True,
                    "static_checks_clean": True,
                }
            )
        )
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir


def _deliver(tmp_path: Path, run_dir: Path, **kwargs: Any) -> dict[str, Any]:
    deliver_fn = _make_deliver_fn()
    trw_dir = tmp_path / ".trw"
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch(
            "trw_mcp.tools.ceremony._do_reflect",
            return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0},
        ),
        patch(
            "trw_mcp.tools.ceremony._do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
    ):
        return deliver_fn(run_path=str(run_dir), skip_reflect=True, **kwargs)


@pytest.mark.integration
class TestReviewBlockGate:
    """verdict=block must actually block delivery for STANDARD+ runs."""

    @pytest.mark.parametrize("complexity_class", ["STANDARD", "COMPREHENSIVE"])
    def test_block_verdict_blocks_delivery(self, tmp_path: Path, complexity_class: str) -> None:
        """verdict=block + critical findings on STANDARD/COMPREHENSIVE -> blocked."""
        run_dir = _write_run(
            tmp_path,
            complexity_class=complexity_class,
            review_verdict="block",
            review_critical=3,
        )

        result = _deliver(tmp_path, run_dir)

        assert result["success"] is False
        assert "review_block" in result
        block = str(result["review_block"])
        assert "block" in block.lower()
        assert "3 critical" in block
        # The block reason is in the surfaced errors list.
        assert any("review verdict is 'block'" in str(e).lower() for e in result.get("errors", []))

    def test_block_verdict_overridden_by_allow_unverified(self, tmp_path: Path) -> None:
        """allow_unverified=True + STRUCTURED record -> delivery PROCEEDS (Deliver Gate Path 3).

        PRD-CORE-191: overriding a HARD review_block now requires a structured
        AcceptableFailureRecord (failed_command/residual_risk/owner/expiry_iso),
        not a free-text reason. The override is recorded under .trw/overrides/.
        """
        import json
        from datetime import datetime, timedelta, timezone

        run_dir = _write_run(
            tmp_path,
            complexity_class="STANDARD",
            review_verdict="block",
            review_critical=2,
        )
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        reason = json.dumps(
            {
                "failed_command": "trw_review",
                "residual_risk": "finding tracked in ISSUE-456, shipping known-good subset",
                "owner": "agent-run-xyz",
                "expiry_iso": future,
            }
        )

        result = _deliver(
            tmp_path,
            run_dir,
            allow_unverified=True,
            unverified_reason=reason,
        )

        # Override honored — deliver proceeds past the gate.
        assert result["success"] is True
        # The structured record is surfaced (FR04) and echoed into the bypass field.
        record = result.get("acceptable_failure_record")
        assert isinstance(record, dict)
        assert record["owner"] == "agent-run-xyz"
        assert result.get("truthfulness_gate_bypassed")
        # review_block is still reported for visibility, but it did not block.
        assert "review_block" in result

    def test_block_verdict_override_requires_reason(self, tmp_path: Path) -> None:
        """allow_unverified=True with an empty reason still blocks (no silent bypass)."""
        run_dir = _write_run(
            tmp_path,
            complexity_class="STANDARD",
            review_verdict="block",
            review_critical=1,
        )

        result = _deliver(tmp_path, run_dir, allow_unverified=True, unverified_reason="   ")

        assert result["success"] is False
        assert "review_block" in result

    @pytest.mark.parametrize("verdict", ["pass", "warn"])
    def test_non_block_verdict_delivers_normally(self, tmp_path: Path, verdict: str) -> None:
        """verdict=pass / verdict=warn -> delivers normally, no review_block."""
        run_dir = _write_run(
            tmp_path,
            complexity_class="STANDARD",
            review_verdict=verdict,
            review_critical=0,
        )

        result = _deliver(tmp_path, run_dir)

        assert result["success"] is True
        assert "review_block" not in result

    def test_minimal_complexity_not_over_blocked(self, tmp_path: Path) -> None:
        """MINIMAL + verdict=block -> soft warning only, delivery proceeds."""
        run_dir = _write_run(
            tmp_path,
            complexity_class="MINIMAL",
            review_verdict="block",
            review_critical=2,
        )

        result = _deliver(tmp_path, run_dir)

        assert result["success"] is True
        assert "review_block" not in result
        # The historical soft warning is preserved for trivial work.
        assert "review_warning" in result

    def test_integration_review_block_unchanged(self, tmp_path: Path) -> None:
        """integration_review_block still blocks (no allow_unverified bypass here)."""
        run_dir = _write_run(
            tmp_path,
            complexity_class="STANDARD",
            review_verdict="pass",
            integration_verdict="block",
        )

        result = _deliver(tmp_path, run_dir)

        assert result["success"] is False
        assert "integration_review_block" in result
        # review_block is NOT what blocked here — the review verdict was pass.
        assert "review_block" not in result

    def test_co_firing_review_block_and_integration_block_both_surfaced(self, tmp_path: Path) -> None:
        """F4: when integration_review_block fires, a co-firing review_block is ALSO in errors.

        Pre-fix the cascade returned on the FIRST non-overridable hard gate, so a
        simultaneously-active human review_block (review.yaml verdict=block) was
        silently dropped — the agent fixed integration, retried, and only then
        hit the hidden second block. The fix collects EVERY active hard-block
        message into ``errors`` before returning.

        ``integration_review_block`` + ``review_block`` is the genuinely
        reachable double-block: both review.yaml and integration-review.yaml can
        carry verdict=block at once. (``review_scope_block`` is mutually
        exclusive with ``review_block`` by construction — the scope gate only
        fires when NO review.yaml exists.)
        """
        run_dir = _write_run(
            tmp_path,
            complexity_class="STANDARD",
            review_verdict="block",
            review_critical=3,
            integration_verdict="block",
        )

        result = _deliver(tmp_path, run_dir)

        assert result["success"] is False
        errors = [str(e) for e in result.get("errors", [])]
        # BOTH hard-block messages are present — not just the first one in the cascade.
        assert any("integration review verdict is 'block'" in e.lower() for e in errors)
        assert any("review verdict is 'block'" in e.lower() for e in errors)
        # Both keys are surfaced on the result for visibility.
        assert "integration_review_block" in result
        assert "review_block" in result

    def test_scope_block_and_review_block_mutually_exclusive(self, tmp_path: Path) -> None:
        """F4 boundary: review_scope_block cannot co-fire with review_block.

        The scope gate (>5 files, R-01) only fires when review.yaml is ABSENT,
        and review_block requires review.yaml verdict=block. With >5 files AND a
        review.yaml verdict=block present, only review_block fires — the scope
        gate is suppressed by the review file's existence. This pins that the
        two are structurally exclusive, so the F4 collection of all three keys is
        defensive (review_scope_block is never simultaneously active).
        """
        run_dir = _write_run(
            tmp_path,
            complexity_class="STANDARD",
            review_verdict="block",
            review_critical=2,
        )
        # Append >5 file_modified events so the scope gate WOULD fire if review.yaml were absent.
        meta = run_dir / "meta"
        lines = (meta / "events.jsonl").read_text(encoding="utf-8").splitlines()
        for i in range(6):
            lines.append(
                json.dumps(
                    {"ts": f"2026-06-04T00:01:0{i}Z", "event": "file_modified", "data": {"path": f"src/extra{i}.py"}}
                )
            )
        (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _deliver(tmp_path, run_dir)

        assert result["success"] is False
        # review.yaml present -> scope gate suppressed; only review_block fired.
        assert "review_block" in result
        assert "review_scope_block" not in result

    def test_review_scope_block_unchanged(self, tmp_path: Path) -> None:
        """>5 files modified with NO review.yaml still hard-blocks (R-01)."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260604T000000Z-scope"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\ncomplexity_class: STANDARD\n",
            encoding="utf-8",
        )
        lines = [json.dumps({"ts": "2026-06-04T00:00:00Z", "event": "session_start"})]
        for i in range(6):
            lines.append(
                json.dumps({"ts": f"2026-06-04T00:00:0{i}Z", "event": "file_modified", "data": {"path": f"src/{i}.py"}})
            )
        lines.append(
            json.dumps(
                {
                    "ts": "2026-06-04T00:00:09Z",
                    "event": "build_check_complete",
                    "tests_passed": True,
                    "static_checks_clean": True,
                }
            )
        )
        (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _deliver(tmp_path, run_dir)

        assert result["success"] is False
        assert "review_scope_block" in result
        assert "review_block" not in result
