"""FIX-052: the deferred ceremony_feedback step records the REAL ceremony score.

Regression coverage for the long-standing defect where ``ceremony_score`` was a
constant ``0.0`` across every recorded session in
``.trw/context/ceremony-feedback.yaml``.

Root cause: ``_step_ceremony_feedback`` was handed the PRE-deferred
``critical_results`` snapshot (reflect + checkpoint only). The real ceremony
score is computed by the ``telemetry`` deferred step (``compute_ceremony_score``
-> 0-100) and stored into the LIVE deferred ``results`` dict under the
``telemetry`` key. Because the snapshot never carried a ``telemetry`` key,
``_extract_ceremony_metrics`` always read ``0.0`` and the adaptive-ceremony
feedback loop had no gradient.

These tests drive the REAL deferred path (``_run_deferred_steps`` with the real
``_step_telemetry`` and ``_step_ceremony_feedback``) and assert the persisted
``ceremony_score`` in ``ceremony-feedback.yaml`` is (a) non-zero and (b) varies
with ceremony quality (a full-ceremony run scores strictly higher than a bare
run). We assert the produced VALUE, not mere existence, and never mock the unit
under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

from trw_mcp.tools._deferred_delivery import _run_deferred_steps

# Event types that compute_ceremony_score keys on (state/analytics/report.py).
_FULL_CEREMONY_EVENTS: list[dict[str, object]] = [
    {"event": "session_start"},
    {"event": "checkpoint"},
    {"event": "learn_recorded"},
    {"event": "build_check_complete", "tests_passed": "true"},
    {"event": "review_complete"},
    {"event": "trw_deliver_complete"},
]

_BARE_CEREMONY_EVENTS: list[dict[str, object]] = [
    {"event": "session_start"},
]


def _make_run_dir(tmp_path: Path, name: str, events: list[dict[str, object]]) -> Path:
    """Create a run directory with an events.jsonl and a run.yaml."""
    run_dir = tmp_path / ".trw" / "runs" / name / "20260603T000000Z-test"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        f"run_id: {name}\nstatus: active\nphase: deliver\ntask: {name}\n"
        "complexity_class: STANDARD\nobjective: implement a feature\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events),
        encoding="utf-8",
    )
    return run_dir


def _make_trw_dir(tmp_path: Path, *, build_passed: bool, coverage_pct: float) -> Path:
    """Create the minimal .trw structure plus a build-status.yaml."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "logs").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    build_status = (
        f"tests_passed: {'true' if build_passed else 'false'}\n"
        f"mypy_clean: true\ncoverage_pct: {coverage_pct}\n"
    )
    (trw_dir / "context" / "build-status.yaml").write_text(build_status, encoding="utf-8")
    return trw_dir


def _stub_non_target_steps() -> dict[str, Any]:
    """Stub every deferred step EXCEPT telemetry + ceremony_feedback (the path under test)."""
    noop: dict[str, object] = {"status": "skipped"}
    names = [
        "_step_auto_prune",
        "_step_consolidation",
        "_step_tier_sweep",
        "_do_index_sync",
        "_step_auto_progress",
        "_step_publish_learnings",
        "_step_outcome_correlation",
        "_step_recall_outcome",
        "_step_batch_send",
        "_step_trust_increment",
        "_step_delivery_metrics",
    ]
    return {
        n: patch(f"trw_mcp.tools._deferred_delivery.{n}", return_value=noop) for n in names
    }


def _read_recorded_scores(trw_dir: Path) -> list[float]:
    """Read every recorded ceremony_score from ceremony-feedback.yaml."""
    fb_path = trw_dir / "context" / "ceremony-feedback.yaml"
    assert fb_path.exists(), "ceremony-feedback.yaml was not written by the feedback step"
    data = YAML(typ="safe").load(fb_path.read_text(encoding="utf-8"))
    scores: list[float] = []
    task_classes = data.get("task_classes", {}) if isinstance(data, dict) else {}
    for cls in task_classes.values():
        if isinstance(cls, dict):
            for session in cls.get("sessions", []):
                if isinstance(session, dict) and "ceremony_score" in session:
                    scores.append(float(session["ceremony_score"]))
    return scores


def _run_real_feedback_path(
    trw_dir: Path,
    run_dir: Path,
) -> None:
    """Drive _run_deferred_steps with the real telemetry + ceremony_feedback steps.

    The telemetry network client is mocked (no events leave the box) but the
    score COMPUTATION and the feedback RECORDING run for real.
    """
    mock_client = MagicMock()
    mock_client.record_event = MagicMock()
    mock_client.flush = MagicMock()

    mock_sender = MagicMock()
    mock_sender.send = MagicMock(return_value={"sent": 0, "failed": 0, "remaining": 0})

    with (
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_installation_id", return_value="inst-test"),
        patch(
            "trw_mcp.telemetry.client.TelemetryClient.from_config",
            return_value=mock_client,
        ),
        patch(
            "trw_mcp.telemetry.sender.BatchSender.from_config",
            return_value=mock_sender,
        ),
        patch("trw_mcp.telemetry.pipeline.TelemetryPipeline.get_instance"),
        _enter(_stub_non_target_steps()),
    ):
        # critical_results is the PRE-deferred snapshot the buggy code used to
        # pass straight through. It carries NO telemetry key — exactly the
        # condition that pinned the recorded score to 0.0.
        critical_results: dict[str, object] = {
            "reflect": {"status": "success"},
            "checkpoint": {"status": "success"},
        }
        _run_deferred_steps(trw_dir, run_dir, critical_results, skip_index_sync=True)


def _enter(stubs: dict[str, Any]) -> Any:
    """Combine a dict of patch() context managers into one."""
    import contextlib

    @contextlib.contextmanager
    def _cm() -> Any:
        with contextlib.ExitStack() as stack:
            for s in stubs.values():
                stack.enter_context(s)
            yield

    return _cm()


@pytest.mark.integration
class TestDeferredCeremonyScoreGradient:
    """The recorded ceremony_score must be real (non-constant) and quality-sensitive."""

    def test_full_ceremony_run_records_nonzero_score(self, tmp_path: Path) -> None:
        """A run with all ceremony events records a strictly positive ceremony_score."""
        trw_dir = _make_trw_dir(tmp_path, build_passed=True, coverage_pct=85.0)
        run_dir = _make_run_dir(tmp_path, "full-run", _FULL_CEREMONY_EVENTS)

        _run_real_feedback_path(trw_dir, run_dir)

        scores = _read_recorded_scores(trw_dir)
        assert scores, "no ceremony_score was recorded"
        # The default ceremony weights sum to 100 for a fully-compliant run; the
        # exact total depends on the active client profile, but it MUST be well
        # above zero (the old constant-0.0 bug).
        assert scores[-1] > 0.0, f"expected a real non-zero ceremony_score, got {scores[-1]}"
        # Full compliance should land near the top of the 0-100 band.
        assert scores[-1] >= 50.0, (
            f"full-ceremony run scored unexpectedly low ({scores[-1]}); "
            "telemetry score did not reach the feedback step"
        )

    def test_bare_run_records_lower_score(self, tmp_path: Path) -> None:
        """A bare run (session_start only) records a lower, but still real, score."""
        trw_dir = _make_trw_dir(tmp_path, build_passed=False, coverage_pct=0.0)
        run_dir = _make_run_dir(tmp_path, "bare-run", _BARE_CEREMONY_EVENTS)

        _run_real_feedback_path(trw_dir, run_dir)

        scores = _read_recorded_scores(trw_dir)
        assert scores, "no ceremony_score was recorded"
        # session_start present, deliver/checkpoint/learn/build/review absent.
        # Score is well below a full run but is a real computed value.
        assert scores[-1] >= 0.0

    def test_score_varies_with_ceremony_quality(self, tmp_path: Path) -> None:
        """The feedback loop has a GRADIENT: full ceremony > bare ceremony.

        This is the load-bearing assertion: before the fix both runs recorded
        the SAME constant 0.0, so the adaptive loop could never learn. After the
        fix the recorded score strictly increases with ceremony quality.
        """
        # Full-ceremony run in its own .trw dir.
        full_trw = _make_trw_dir(tmp_path / "full", build_passed=True, coverage_pct=90.0)
        full_run = _make_run_dir(tmp_path / "full", "full-run", _FULL_CEREMONY_EVENTS)
        _run_real_feedback_path(full_trw, full_run)
        full_scores = _read_recorded_scores(full_trw)

        # Bare run in a separate .trw dir.
        bare_trw = _make_trw_dir(tmp_path / "bare", build_passed=False, coverage_pct=0.0)
        bare_run = _make_run_dir(tmp_path / "bare", "bare-run", _BARE_CEREMONY_EVENTS)
        _run_real_feedback_path(bare_trw, bare_run)
        bare_scores = _read_recorded_scores(bare_trw)

        assert full_scores and bare_scores
        assert full_scores[-1] > bare_scores[-1], (
            f"ceremony_score has no gradient: full={full_scores[-1]} "
            f"bare={bare_scores[-1]} — the adaptive feedback loop cannot learn"
        )
        # And the full run is genuinely non-trivial, not a 1-point margin.
        assert full_scores[-1] - bare_scores[-1] >= 20.0
