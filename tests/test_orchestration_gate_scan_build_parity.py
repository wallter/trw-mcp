"""``deliver_gate_summary`` must not claim a build block the deliver gate will not apply.

PRD-QUAL-105 fixed exactly this over-claim for the REVIEW half of the summary
(round-2 transport e2e F4) and left the BUILD half asserting ``BLOCKED: no
passing build check`` whenever evidence was missing. Under the shipped
``deliver_gate_mode=block_coding`` that is false for every task type outside
coding/rca/eval — a docs or research run with no build check delivers
successfully — and under ``advisory`` it is false for all of them.

The correction is a CONSEQUENCE correction, not a removal: the missing build is
still surfaced, as an advisory. These tests bind both halves of that, so a
future change can neither restore the over-claim nor silently stop mentioning
the missing evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _orchestration_gate_scan as gate_scan


def _seed_run(tmp_path: Path, task_type: str) -> Path:
    run_dir = tmp_path / "runs" / "20260727T000000Z-parity"
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: 20260727T000000Z-parity\ntask: t\nstatus: active\nphase: implement\ntask_type: {task_type}\n",
        encoding="utf-8",
    )
    return run_dir


def _use_config(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    """Pin the config the gate-mode predicate reads, at its own import site.

    Both seams are patched on purpose. The preview no longer reads
    ``deliver_gate_mode`` itself — it calls
    ``_deliver_gate_mode.resolve_gate_mode_with_source``, the single resolver the
    deliver gate uses (WD-05) — and that module binds ``get_config`` at import
    time, so patching only ``trw_mcp.models.config.get_config`` would leave the
    predicate reading the real config singleton and make this parity suite
    order-dependent.
    """
    config = TRWConfig().model_copy(update=overrides)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: config)


# ---------------------------------------------------------------------------
# The predicate — parity with the deliver path's own dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_type", ["coding", "rca", "eval"])
def test_build_artifact_task_types_would_block_under_the_shipped_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task_type: str
) -> None:
    """Non-vacuity: the gate really does block the build-bearing task types."""
    _use_config(monkeypatch, deliver_gate_mode="block_coding")
    run_dir = _seed_run(tmp_path, task_type)

    assert gate_scan._build_gate_would_block(run_dir, missing_build=True) is True


@pytest.mark.parametrize("task_type", ["docs", "research", "planning", "unknown"])
def test_advisory_task_types_would_not_block_under_the_shipped_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task_type: str
) -> None:
    """These deliver successfully today — the preview must agree."""
    _use_config(monkeypatch, deliver_gate_mode="block_coding")
    run_dir = _seed_run(tmp_path, task_type)

    assert gate_scan._build_gate_would_block(run_dir, missing_build=True) is False


def test_advisory_mode_never_blocks_even_for_coding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(monkeypatch, deliver_gate_mode="advisory")
    run_dir = _seed_run(tmp_path, "coding")

    assert gate_scan._build_gate_would_block(run_dir, missing_build=True) is False


def test_per_task_type_override_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deliver gate reads the override map; the preview must read it too."""
    _use_config(
        monkeypatch,
        deliver_gate_mode="advisory",
        deliver_gate_task_type_overrides={"coding": "block_coding"},
    )
    run_dir = _seed_run(tmp_path, "coding")

    assert gate_scan._build_gate_would_block(run_dir, missing_build=True) is True


def test_unreadable_run_degrades_to_advisory_not_to_a_spurious_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An undeterminable task type is ``unknown``, which the deliver gate allows."""
    _use_config(monkeypatch, deliver_gate_mode="block_coding")

    assert gate_scan._build_gate_would_block(None, missing_build=True) is False
    assert gate_scan._build_gate_would_block(tmp_path / "absent", missing_build=True) is False


def test_present_build_evidence_short_circuits_the_predicate(tmp_path: Path) -> None:
    assert gate_scan._build_gate_would_block(_seed_run(tmp_path, "coding"), missing_build=False) is False


# ---------------------------------------------------------------------------
# The rendered summary
# ---------------------------------------------------------------------------


def test_summary_blocks_when_the_gate_would_block() -> None:
    summary = gate_scan._summarize_deliver_gate(False, True, False, build_would_block=True)

    assert summary.startswith("BLOCKED")
    assert "trw_build_check" in summary


def test_summary_downgrades_to_advisory_when_the_gate_would_not_block() -> None:
    summary = gate_scan._summarize_deliver_gate(False, True, False, build_would_block=False)

    assert not summary.startswith("BLOCKED")
    assert summary.startswith("READY")
    # The missing evidence is corrected, never hidden.
    assert "trw_build_check" in summary


def test_advisory_summary_names_both_missing_gates() -> None:
    summary = gate_scan._summarize_deliver_gate(False, False, False, build_would_block=False)

    assert summary.startswith("READY")
    assert "trw_build_check" in summary
    assert "trw_review" in summary


def test_review_hard_block_still_wins_over_an_advisory_build(tmp_path: Path) -> None:
    """A real review block must not be masked by the build downgrade."""
    summary = gate_scan._summarize_deliver_gate(False, False, True, build_would_block=False)

    assert summary.startswith("BLOCKED")
    assert "trw_review" in summary


# ---------------------------------------------------------------------------
# End to end through compute_deliver_gate_status
# ---------------------------------------------------------------------------


def _failing_build_event() -> dict[str, Any]:
    return {"event": "build_check_complete", "tests_passed": False, "ts": "2026-07-27T00:00:00+00:00"}


def test_docs_run_with_no_build_reports_ready_advisory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(monkeypatch, deliver_gate_mode="block_coding")
    run_dir = _seed_run(tmp_path, "docs")

    gate = gate_scan.compute_deliver_gate_status([_failing_build_event()], tmp_path / ".trw", run_dir)

    assert gate["build_gate_ready"] is False, "readiness still reports the missing evidence"
    assert gate["deliver_gate_summary"].startswith("READY")


def test_coding_run_with_no_build_still_reports_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity for the test above — the gate is not simply disabled."""
    _use_config(monkeypatch, deliver_gate_mode="block_coding")
    run_dir = _seed_run(tmp_path, "coding")

    gate = gate_scan.compute_deliver_gate_status([_failing_build_event()], tmp_path / ".trw", run_dir)

    assert gate["build_gate_ready"] is False
    assert gate["deliver_gate_summary"].startswith("BLOCKED")
