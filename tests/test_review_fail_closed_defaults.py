"""Defaults on gate-feeding values must fail CLOSED, not open.

Companion to ``test_review_auto_suppression.py``. That module pins the incident
where an injected ``default_confidence=0.0`` filtered every unscored finding out
of a review. These pin the same shape one layer up: a value that is *absent*,
silently defaulted, and then gated on. The question each covers is not "did the
default look sensible" but "does it make the gate easier to satisfy than its
author believed" — because a gate that is easier to pass than documented is an
HB-1 misrepresentation, whatever the intent.

Covered here:
  1. ``trw_review`` defaulted ``substantive`` to True, so a mode handler that
     stopped stamping the bit would have unlocked the delivery review gate.
  2. ``record_review_receipt`` defaulted a missing ``verdict`` to ``"pass"``,
     minting authoritative PASS evidence for a review that stated no outcome.
  3. ``_prd_transition_gate`` resolved its two policy modes through
     ``getattr(config, ..., <weaker default>)``, a second copy of the policy that
     disagreed with the config field's real default in the permissive direction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateWriter


class TestSubstantiveDefaultsToFalse:
    """The bit that unlocks the review gate is never assumed."""

    def test_unstamped_handler_response_is_recorded_as_non_substantive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tests._ceremony_helpers import make_ceremony_server
        from trw_mcp.models.config import _reset_config

        run = tmp_path / "runs" / "task" / "run-1"
        (run / "meta").mkdir(parents=True)
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig())

        # A handler that stops stamping ``substantive`` is the regression this
        # default guards. Everything else about the response is well-formed, so
        # nothing but the missing key can explain the outcome.
        def _unstamped(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"review_id": "review-x", "verdict": "pass", "critical_count": 0, "total_findings": 1}

        monkeypatch.setattr("trw_mcp.tools._review_manual.handle_manual_mode", _unstamped)
        recorded: dict[str, object] = {}

        def _capture(_trw_dir: Path, verdict: str, p0_count: int = 0, *, substantive: bool = True) -> None:
            recorded.update(verdict=verdict, p0_count=p0_count, substantive=substantive)

        monkeypatch.setattr("trw_mcp.state.ceremony_progress.mark_review", _capture)

        tools["trw_review"].fn(
            findings=[{"category": "correctness", "severity": "info", "description": "x"}],
            run_path=str(run),
        )

        assert recorded["substantive"] is False

    def test_a_stamped_response_still_counts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-vacuity control: the fail-closed default did not break the real path."""
        from tests._ceremony_helpers import make_ceremony_server
        from trw_mcp.models.config import _reset_config

        run = tmp_path / "runs" / "task" / "run-2"
        (run / "meta").mkdir(parents=True)
        tools = make_ceremony_server(monkeypatch, tmp_path)
        # observe mode: the typed-receipt path is exercised by
        # test_core205_review_producers_enforce.py and is not what this control is
        # about. Under enforce a receipt failure legitimately clears substantive,
        # which would mask whether review.py's own default is the cause.
        _reset_config(TRWConfig(evidence_receipt_mode="observe"))
        recorded: dict[str, object] = {}

        def _capture(_trw_dir: Path, verdict: str, p0_count: int = 0, *, substantive: bool = True) -> None:
            recorded.update(verdict=verdict, p0_count=p0_count, substantive=substantive)

        monkeypatch.setattr("trw_mcp.state.ceremony_progress.mark_review", _capture)

        result = tools["trw_review"].fn(
            findings=[{"category": "correctness", "severity": "warning", "description": "real finding"}],
            run_path=str(run),
        )

        assert result["substantive"] is True
        assert recorded["substantive"] is True


class TestReceiptRefusesAVerdictlessReview:
    """No verdict recorded means no receipt — never a PASS receipt."""

    @staticmethod
    def _project_run(tmp_path: Path) -> tuple[Path, Path]:
        # Root at tmp_path: the suite's path-isolation harness reports tmp_path
        # as the project root and ignores TRW_PROJECT_ROOT.
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds.mkdir(parents=True, exist_ok=True)
        (prds / "PRD-CORE-205.md").write_text("# PRD-CORE-205\n\nReqs.\n", encoding="utf-8")
        run = tmp_path / ".trw" / "runs" / "task" / "run-1"
        (run / "meta").mkdir(parents=True, exist_ok=True)
        (run / "meta" / "events.jsonl").write_text(
            json.dumps({"event": "file_modified", "file": str(tmp_path / "src" / "feature.py")}) + "\n",
            encoding="utf-8",
        )
        return tmp_path, run

    def test_missing_verdict_writes_no_receipt(self, tmp_path: Path) -> None:
        from trw_mcp.tools._review_receipt_writer import record_review_receipt

        _project, run = self._project_run(tmp_path)

        outcome = record_review_receipt(
            run,
            {"review_id": "review-1", "mode": "manual", "substantive": True, "timestamp": "2026-07-10T00:00:00Z"},
            ("PRD-CORE-205",),
            policy_mode="enforce",
        )

        assert outcome.ok is False
        assert outcome.receipt_id == ""
        assert outcome.reason_code == "review_verdict_missing"

    def test_present_verdict_still_writes_a_receipt(self, tmp_path: Path) -> None:
        """Non-vacuity control: the guard rejects only the verdictless payload."""
        from trw_mcp.tools._review_receipt_writer import record_review_receipt

        _project, run = self._project_run(tmp_path)

        outcome = record_review_receipt(
            run,
            {
                "review_id": "review-1",
                "mode": "manual",
                "substantive": True,
                "timestamp": "2026-07-10T00:00:00Z",
                "verdict": "pass",
            },
            ("PRD-CORE-205",),
            policy_mode="enforce",
        )

        assert outcome.ok is True

    def test_a_blank_verdict_is_treated_as_missing(self, tmp_path: Path) -> None:
        from trw_mcp.tools._review_receipt_writer import record_review_receipt

        _project, run = self._project_run(tmp_path)

        outcome = record_review_receipt(
            run,
            {"review_id": "review-1", "mode": "manual", "timestamp": "2026-07-10T00:00:00Z", "verdict": "   "},
            ("PRD-CORE-205",),
            policy_mode="enforce",
        )

        assert outcome.reason_code == "review_verdict_missing"


class TestTransitionGateHasNoSecondWeakerDefault:
    """The gate's posture comes from the config field, not a local fallback."""

    def test_default_config_blocks_a_coding_run(self) -> None:
        """With shipped defaults the gate blocks; the old getattr fallback did not.

        ``getattr(config, "deliver_gate_mode", "advisory")`` disagreed with the
        field's real default of ``block_coding``, so any config object that did
        not expose the attribute silently downgraded this gate to never-block.
        """
        from trw_mcp.tools._prd_transition_gate import _gate_mode_blocks_task

        config = TRWConfig()

        assert config.deliver_gate_mode == "block_coding"
        assert _gate_mode_blocks_task(config, "coding") is True

    @pytest.mark.parametrize("task_type", ["docs", "research", "planning", "unknown"])
    def test_non_artifact_task_types_never_block(self, task_type: str) -> None:
        from trw_mcp.tools._prd_transition_gate import _gate_mode_blocks_task

        assert _gate_mode_blocks_task(TRWConfig(), task_type) is False

    def test_explicit_advisory_still_disables_the_gate(self) -> None:
        """Non-vacuity control: the config is genuinely read, not hardcoded True."""
        from trw_mcp.tools._prd_transition_gate import _gate_mode_blocks_task

        assert _gate_mode_blocks_task(TRWConfig(deliver_gate_mode="advisory"), "coding") is False

    def test_per_task_type_override_still_wins(self) -> None:
        from trw_mcp.tools._prd_transition_gate import _gate_mode_blocks_task

        config = TRWConfig(deliver_gate_task_type_overrides={"coding": "advisory"})

        assert _gate_mode_blocks_task(config, "coding") is False

    def test_prd_transition_gate_default_is_block_not_warn(self) -> None:
        """The resolved gate_mode must match the declared field default.

        ``evaluate_transition_gate`` used ``getattr(config, "prd_transition_gate",
        "warn")``; the field's default is ``"block"``.
        """
        assert TRWConfig().prd_transition_gate == "block"

    def test_evaluate_transition_gate_reports_the_configured_mode(self, tmp_path: Path) -> None:
        from trw_mcp.tools._prd_transition_gate import evaluate_transition_gate

        run = tmp_path / ".trw" / "runs" / "task" / "run-1"
        (run / "meta").mkdir(parents=True)
        # docs task_type -> no block, but the OUTCOME still carries the resolved
        # mode, which is what the weaker fallback used to misreport.
        FileStateWriter().write_yaml(run / "meta" / "run.yaml", {"run_id": "r", "task_type": "docs"})

        outcome = evaluate_transition_gate(run)

        assert outcome.should_block is False
        assert outcome.mode == "block"


class TestAnEmptyVerdictIsNotAConclusion:
    """A substantive review with no verdict must not read as a completed one.

    Third instance of this file's shape, found by a wave-2 wiring review.
    ``mark_review``'s docstring promises to record REVIEW readiness "without
    letting empty artifacts satisfy it" — but that only ever covered the
    ``substantive`` bit. An empty *verdict* was stored verbatim, and
    ``build_ceremony_status_line`` then rendered it via ``or 'recorded'``: the
    calmest available word, printed directly beside a live ``p0=N`` count.
    Alarming number, reassuring label.
    """

    def test_an_empty_verdict_renders_as_unrecorded_not_recorded(self) -> None:
        from trw_mcp.state._ceremony_state_model import CeremonyState
        from trw_mcp.tools._ceremony_status import build_ceremony_status_line

        line = build_ceremony_status_line(
            CeremonyState(review_called=True, review_verdict="", review_p0_count=2)
        )

        assert "review=verdict_unrecorded" in line
        # The exact word the old fallback produced. Asserting its absence is the
        # point: "recorded" is what made a missing verdict look like a finished one.
        assert "review=recorded" not in line
        assert "p0=2" in line

    def test_a_real_verdict_is_still_rendered_verbatim(self) -> None:
        """Non-vacuity control.

        Without this, a renderer that printed ``verdict_unrecorded``
        unconditionally would pass the test above.
        """
        from trw_mcp.state._ceremony_state_model import CeremonyState
        from trw_mcp.tools._ceremony_status import build_ceremony_status_line

        line = build_ceremony_status_line(
            CeremonyState(review_called=True, review_verdict="pass", review_p0_count=0)
        )

        assert "review=pass" in line
        assert "verdict_unrecorded" not in line

    def test_mark_review_persists_the_absence_rather_than_an_empty_string(
        self, tmp_path: Path
    ) -> None:
        """Fix the state, not just the rendering.

        Every consumer of ``review_verdict`` — not only the status line — sees a
        distinguishable value.
        """
        from trw_mcp.state._ceremony_progress_state import mark_review, read_ceremony_state

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        mark_review(trw_dir, verdict="   ", p0_count=1)

        state = read_ceremony_state(trw_dir)
        assert state.review_called is True
        assert state.review_verdict == "verdict_unrecorded"

    def test_mark_review_leaves_a_real_verdict_untouched(self, tmp_path: Path) -> None:
        """Second non-vacuity control, at the state layer."""
        from trw_mcp.state._ceremony_progress_state import mark_review, read_ceremony_state

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        mark_review(trw_dir, verdict="block", p0_count=3)

        state = read_ceremony_state(trw_dir)
        assert state.review_verdict == "block"
        assert state.review_p0_count == 3
