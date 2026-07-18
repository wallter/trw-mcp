"""PRD-QUAL-105: deliver-gate audit trail in trw_status.

FR01 build_gate_ready, FR02 review_gate_ready, FR03 deliver_gate_summary,
FR04 fail-open gate scan. Tests assert real field values from constructed
event/ceremony fixtures (both ready and blocked branches) plus the fail-open
branch via a forced exception.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state.ceremony_progress import CeremonyState, write_ceremony_state

from ._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401

# ---------------------------------------------------------------------------
# Pure-unit tests of the gate-scan predicates (no filesystem I/O).
# ---------------------------------------------------------------------------


def _passing_build_event() -> dict[str, object]:
    return {
        "event": "build_check_complete",
        "data": {"tests_passed": True, "static_checks_clean": True},
    }


def _failing_build_event() -> dict[str, object]:
    return {
        "event": "build_check_complete",
        "data": {"tests_passed": False, "static_checks_clean": True},
    }


class TestBuildGateReadyPredicate:
    """FR01: build gate readiness reuses _delivery_build_gates._build_passed."""

    def test_passing_build_event_is_ready(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

        assert _build_gate_ready([_passing_build_event()]) is True

    def test_no_passing_build_is_not_ready(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

        assert _build_gate_ready([_failing_build_event()]) is False

    def test_empty_events_is_not_ready(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

        assert _build_gate_ready([]) is False

    def test_reuses_delivery_build_gate_predicate(self) -> None:
        """The build predicate is literally _delivery_build_gates._build_passed —
        proves FR01 does not duplicate the gate logic (risk R2)."""
        from trw_mcp.tools import _delivery_build_gates, _orchestration_gate_scan

        ev = _passing_build_event()
        assert _orchestration_gate_scan._build_gate_ready([ev]) is _delivery_build_gates._build_passed(ev)

    def test_build_check_disabled_is_ready_even_without_passing_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FINDING-1 regression: when build_check_enabled is False the deliver gate
        skips the build requirement entirely (deliver would ALLOW), so status must
        report build_gate_ready=True even with no passing build event — otherwise
        status says BLOCKED while deliver passes (false-signal divergence)."""
        from types import SimpleNamespace

        from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

        # _build_gate_ready lazy-imports get_config from trw_mcp.models.config,
        # so patch at the source module (the binding it actually looks up).
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: SimpleNamespace(build_check_enabled=False),
        )
        # No passing build event, yet the gate is disabled -> ready.
        assert _build_gate_ready([_failing_build_event()]) is True
        assert _build_gate_ready([]) is True

    def test_stale_build_evidence_is_not_ready_preview_parity(self) -> None:
        """codex cross-model review preview parity: the deliver-time build gate now
        treats a passing-but-stale build (edit AFTER the last passing build) as a
        warning/block; the status preview MUST agree (build_gate_ready=False) or it
        would report READY while deliver blocks — reintroducing the false-signal
        divergence PRD-QUAL-105 exists to prevent."""
        from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

        stale = [
            {
                "ts": "2026-06-11T00:00:00Z",
                "event": "build_check_complete",
                "tests_passed": True,
                "static_checks_clean": True,
            },
            {"ts": "2026-06-11T00:00:05Z", "event": "file_modified", "data": {"path": "src/x.py"}},
        ]
        assert _build_gate_ready(stale) is False

    def test_fresh_build_evidence_is_ready_preview_parity(self) -> None:
        """edit-then-pass: the preview reports READY (matches deliver)."""
        from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

        fresh = [
            {"ts": "2026-06-11T00:00:00Z", "event": "file_modified", "data": {"path": "src/x.py"}},
            {
                "ts": "2026-06-11T00:00:05Z",
                "event": "build_check_complete",
                "tests_passed": True,
                "static_checks_clean": True,
            },
        ]
        assert _build_gate_ready(fresh) is True

    def test_build_check_enabled_true_still_requires_passing_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The disabled-gate short-circuit must NOT fire when build_check_enabled
        is True — a failing/absent build event still yields not-ready."""
        from types import SimpleNamespace

        from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: SimpleNamespace(build_check_enabled=True),
        )
        assert _build_gate_ready([_failing_build_event()]) is False
        assert _build_gate_ready([_passing_build_event()]) is True


class TestReviewGateReadyPredicate:
    """FR02: review gate ready when review_called and verdict is not block."""

    def test_review_called_non_block_is_ready(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _review_gate_ready

        state = CeremonyState(review_called=True, review_verdict="acceptable")
        assert _review_gate_ready(state) is True

    def test_review_not_called_is_not_ready(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _review_gate_ready

        assert _review_gate_ready(CeremonyState(review_called=False)) is False

    def test_review_called_block_verdict_is_not_ready(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _review_gate_ready

        state = CeremonyState(review_called=True, review_verdict="block")
        assert _review_gate_ready(state) is False

    def test_review_called_no_verdict_is_ready(self) -> None:
        """A recorded review with no explicit verdict (None) counts as ready —
        only an explicit 'block' verdict keeps the gate closed."""
        from trw_mcp.tools._orchestration_gate_scan import _review_gate_ready

        state = CeremonyState(review_called=True, review_verdict=None)
        assert _review_gate_ready(state) is True


class TestDeliverGateSummary:
    """FR03 + F4: deliver_gate_summary mirrors deliver-time ENFORCEMENT.

    The summary reports ``BLOCKED: review`` ONLY when ``trw_deliver`` would
    actually hard-block on the review gate (``review_would_block``). A missing
    review that deliver would still ALLOW (warn-mode / sub-STANDARD) is an
    advisory, never a BLOCKED over-claim (round-2 transport e2e F4).
    """

    def test_both_ready_is_literal_ready(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _summarize_deliver_gate

        assert _summarize_deliver_gate(True, True, False) == "READY"

    def test_no_build_mentions_build_check(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import _summarize_deliver_gate

        # Build unready -> highest-priority block regardless of review flag.
        summary = _summarize_deliver_gate(False, True, False)
        assert "trw_build_check" in summary
        assert summary.startswith("BLOCKED")

    def test_build_passing_review_enforced_blocks(self) -> None:
        """Deliver WOULD hard-block review (block-mode / verdict=block / scope)
        -> summary says BLOCKED: review required."""
        from trw_mcp.tools._orchestration_gate_scan import _summarize_deliver_gate

        summary = _summarize_deliver_gate(True, False, True)
        assert summary.startswith("BLOCKED")
        assert "trw_review" in summary

    def test_build_passing_review_not_enforced_is_advisory(self) -> None:
        """F4 anti-over-claim: build passes, no review recorded, but deliver
        would SUCCEED (warn-mode) -> READY with advisory, NOT BLOCKED."""
        from trw_mcp.tools._orchestration_gate_scan import _summarize_deliver_gate

        summary = _summarize_deliver_gate(True, False, False)
        assert not summary.startswith("BLOCKED")
        assert summary.startswith("READY")
        assert "advisory" in summary
        assert "trw_review" in summary

    def test_build_is_higher_priority_than_review(self) -> None:
        """When both gates fail, the missing build is surfaced (not review)."""
        from trw_mcp.tools._orchestration_gate_scan import _summarize_deliver_gate

        summary = _summarize_deliver_gate(False, False, True)
        assert "trw_build_check" in summary
        assert "trw_review" not in summary


class TestApplyDeliverGateStatusFailOpen:
    """FR04: apply_deliver_gate_status omits fields and logs on scan error."""

    def test_no_events_omits_fields(self) -> None:
        from trw_mcp.tools._orchestration_gate_scan import apply_deliver_gate_status

        result: dict[str, object] = {}
        apply_deliver_gate_status(result, [])
        assert "build_gate_ready" not in result
        assert "review_gate_ready" not in result
        assert "deliver_gate_summary" not in result

    def test_forced_exception_is_fail_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A raise inside the scan must not propagate: the three fields are
        simply absent and a deliver_gate_scan_failed warning is emitted."""
        import structlog

        from trw_mcp.tools import _orchestration_gate_scan

        def _boom(*_a: object, **_k: object) -> Any:
            raise RuntimeError("malformed ceremony store")

        monkeypatch.setattr(_orchestration_gate_scan, "compute_deliver_gate_status", _boom)

        result: dict[str, object] = {}
        with structlog.testing.capture_logs() as logs:
            _orchestration_gate_scan.apply_deliver_gate_status(result, [_passing_build_event()])

        assert "build_gate_ready" not in result
        assert "deliver_gate_summary" not in result
        assert any(entry.get("event") == "deliver_gate_scan_failed" for entry in logs)


# ---------------------------------------------------------------------------
# Integration tests through the live trw_status tool.
# ---------------------------------------------------------------------------


def _init_run(orch_tools: dict[str, Any], task: str) -> Path:
    init = orch_tools["trw_init"].fn(task_name=task)
    return Path(init["run_path"])


def _append_event(run_path: Path, event: dict[str, object]) -> None:
    import json

    events_path = run_path / "meta" / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


class TestCeremonyStateRobustness:
    """FINDING-2: missing/malformed ceremony_state.json fails open to defaults
    inside read_ceremony_state (never raises), so the scan succeeds with
    review_gate_ready=False rather than hitting the wrapper's except clause."""

    def test_missing_ceremony_state_yields_review_not_ready(self, tmp_path: Path) -> None:
        from trw_mcp.tools._orchestration_gate_scan import compute_deliver_gate_status

        trw_dir = tmp_path / ".trw"  # no ceremony-state.json written
        gate = compute_deliver_gate_status([_passing_build_event()], trw_dir)
        assert gate["build_gate_ready"] is True
        assert gate["review_gate_ready"] is False
        assert "trw_review" in gate["deliver_gate_summary"]

    def test_malformed_ceremony_state_does_not_raise(self, tmp_path: Path) -> None:
        from trw_mcp.tools._orchestration_gate_scan import compute_deliver_gate_status

        trw_dir = tmp_path / ".trw"
        state_path = trw_dir / "context" / "ceremony-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not valid json", encoding="utf-8")

        # read_ceremony_state fails open to defaults -> no exception propagates.
        gate = compute_deliver_gate_status([_passing_build_event()], trw_dir)
        assert gate["review_gate_ready"] is False


class TestTrwStatusGateFieldsIntegration:
    """End-to-end: gate fields appear in trw_status output (FR01-FR03)."""

    def test_passing_build_sets_build_gate_ready_true(self, orch_tools: dict[str, Any], tmp_path: Path) -> None:
        run_path = _init_run(orch_tools, "build-ready")
        _append_event(run_path, _passing_build_event())

        result = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert result["build_gate_ready"] is True

    def test_no_passing_build_sets_build_gate_ready_false(self, orch_tools: dict[str, Any], tmp_path: Path) -> None:
        run_path = _init_run(orch_tools, "build-not-ready")
        _append_event(run_path, _failing_build_event())

        result = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert result["build_gate_ready"] is False
        # FR03: build is highest-priority missing action.
        assert "trw_build_check" in result["deliver_gate_summary"]

    def test_review_called_sets_review_gate_ready_true(self, orch_tools: dict[str, Any], tmp_path: Path) -> None:
        run_path = _init_run(orch_tools, "review-ready")
        _append_event(run_path, _passing_build_event())
        # Write ceremony state with a recorded, non-block review.
        trw_dir = tmp_path / ".trw"
        write_ceremony_state(
            trw_dir,
            CeremonyState(session_started=True, review_called=True, review_verdict="acceptable"),
        )

        result = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert result["review_gate_ready"] is True
        assert result["build_gate_ready"] is True
        # FR03: both gates satisfied -> READY.
        assert result["deliver_gate_summary"] == "READY"

    def test_build_passing_no_review_summary_mentions_review(self, orch_tools: dict[str, Any], tmp_path: Path) -> None:
        run_path = _init_run(orch_tools, "needs-review")
        _append_event(run_path, _passing_build_event())
        # Default ceremony state: review never called.

        result = orch_tools["trw_status"].fn(run_path=str(run_path))

        assert result["build_gate_ready"] is True
        assert result["review_gate_ready"] is False
        assert "trw_review" in result["deliver_gate_summary"]

    def test_fresh_run_no_work_events_still_has_gate_fields(self, orch_tools: dict[str, Any], tmp_path: Path) -> None:
        """A freshly-initialised run has init/session events (non-empty), so the
        scan runs and reports not-ready rather than omitting the fields."""
        run_path = _init_run(orch_tools, "fresh-run")

        result = orch_tools["trw_status"].fn(run_path=str(run_path))

        # trw_init logs events, so the gate scan runs; build is not ready.
        assert result["build_gate_ready"] is False

    def test_status_fail_open_does_not_crash(
        self, orch_tools: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR04 end-to-end: a forced scan error leaves trw_status successful with
        the three gate fields absent."""
        run_path = _init_run(orch_tools, "fail-open-status")
        _append_event(run_path, _passing_build_event())

        from trw_mcp.tools import _orchestration_gate_scan

        def _boom(*_a: object, **_k: object) -> Any:
            raise RuntimeError("forced scan failure")

        monkeypatch.setattr(_orchestration_gate_scan, "compute_deliver_gate_status", _boom)

        result = orch_tools["trw_status"].fn(run_path=str(run_path))

        # Status still returns the core fields.
        assert result["run_id"]
        assert "build_gate_ready" not in result
        assert "review_gate_ready" not in result
        assert "deliver_gate_summary" not in result
