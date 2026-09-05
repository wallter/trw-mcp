"""WD-02 / WD-09 — an unparseable timestamp must not read as "fresh".

External audit + independent skeptic (2026-09-04), P2 defense-in-depth. Two
staleness checks silently dropped a timestamp they could not parse and then
answered in the permissive direction:

- ``state/validation/phase_gates_build.py`` logged at DEBUG and fell through
  with ``is_stale=False``, so a cached build status whose age nobody could read
  was accepted as covering the current tree.
- ``tools/_delivery_build_gates._latest_ts_for`` filtered unparseable stamps out
  of its list, so a damaged ``ts`` on either side of the build-vs-edit
  comparison looked like "no such event" and the pass-then-edit detector
  answered "not stale".

Neither is the primary detector — the content-hash binding in
``build_receipt_content_stale_warning`` is — so these tests also pin the
NARROWNESS of the fix: an ABSENT ``ts`` is still ordinary (legacy and
hook-sourced records omit it routinely) and the phase gate deliberately does not
claim staleness for an unknown age, because that flag RELAXES its own severity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation._phase_gates_build_staleness import evaluate_build_staleness
from trw_mcp.state.validation.phase_gates_build import _check_build_status
from trw_mcp.tools._delivery_build_gates import (
    _build_evidence_is_stale,
    _build_evidence_staleness_reason,
    _check_build_and_work_events,
)

pytestmark = pytest.mark.integration

_PASS = {
    "event": "build_check_complete",
    "tests_passed": True,
    "static_checks_clean": True,
    "test_count": 9,
    "scope": "pytest tests",
}


def _pass(ts: str) -> dict[str, object]:
    return {**_PASS, "ts": ts}


def _edit(ts: str) -> dict[str, object]:
    return {"event": "file_modified", "file": "src/x.py", "ts": ts}


# ── WD-09: run event timestamps ────────────────────────────────────────────


def test_unparseable_build_timestamp_is_stale_unknown() -> None:
    events = [_pass("not-a-timestamp"), _edit("2026-09-04T00:00:05Z")]
    reason = _build_evidence_staleness_reason(events)
    assert reason is not None and "Unverifiable build evidence" in reason
    assert _build_evidence_is_stale(events) is True


def test_unparseable_edit_timestamp_is_stale_unknown() -> None:
    events = [_pass("2026-09-04T00:00:05Z"), _edit("garbage")]
    reason = _build_evidence_staleness_reason(events)
    assert reason is not None and "Unverifiable build evidence" in reason


def test_absent_timestamps_are_still_ordinary() -> None:
    """Non-vacuity: omitting ``ts`` is normal history, not corruption."""
    no_ts_pass = dict(_PASS)
    assert _build_evidence_staleness_reason([no_ts_pass, {"event": "file_modified", "file": "src/x.py"}]) is None
    assert _build_evidence_is_stale([no_ts_pass]) is False


def test_ordinary_stale_and_fresh_orderings_are_unchanged() -> None:
    stale = _build_evidence_staleness_reason([_pass("2026-09-04T00:00:00Z"), _edit("2026-09-04T00:00:05Z")])
    fresh = _build_evidence_staleness_reason([_edit("2026-09-04T00:00:00Z"), _pass("2026-09-04T00:00:05Z")])
    assert stale is not None and "Stale build evidence" in stale
    assert fresh is None


def test_the_gate_surfaces_the_unknown_reason_not_the_stale_one() -> None:
    warning, _ = _check_build_and_work_events([_pass("2026-09-04T00:00:00Z"), _edit("nonsense")])
    assert warning is not None and "Unverifiable build evidence" in warning


def test_status_preview_shares_the_predicate() -> None:
    """The preview must not report READY for evidence the gate calls unverifiable."""
    from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

    assert _build_gate_ready([_pass("2026-09-04T00:00:00Z"), _edit("nonsense")]) is False
    assert _build_gate_ready([_edit("2026-09-04T00:00:00Z"), _pass("2026-09-04T00:00:05Z")]) is True


# ── WD-02: the cached build-status timestamp ───────────────────────────────


def test_unparseable_cached_timestamp_reports_unknown_age() -> None:
    is_stale, failures = evaluate_build_staleness("not-a-timestamp", 1800)

    assert [f.rule for f in failures] == ["build_timestamp_unparseable"]
    assert "age is unknown" in failures[0].message
    # It must NOT claim staleness: that flag relaxes the caller's strict gate.
    assert is_stale is False


def test_measurably_old_and_fresh_timestamps_are_unchanged() -> None:
    old_stale, old_failures = evaluate_build_staleness("2020-01-01T00:00:00Z", 1800)
    fresh_stale, fresh_failures = evaluate_build_staleness("2999-01-01T00:00:00Z", 1800)
    absent_stale, absent_failures = evaluate_build_staleness("", 1800)

    assert old_stale is True and [f.rule for f in old_failures] == ["build_staleness"]
    assert fresh_stale is False and fresh_failures == []
    assert absent_stale is False and absent_failures == []


def test_phase_gate_surfaces_the_unparseable_stamp(tmp_path: Path) -> None:
    """End to end through ``_check_build_status``: the failure reaches the caller."""
    context = tmp_path / "context"
    context.mkdir(parents=True)
    (context / "build-status.yaml").write_text(
        "timestamp: not-a-timestamp\ntests_passed: true\nstatic_checks_clean: true\nscope: full\n",
        encoding="utf-8",
    )

    config = TRWConfig().model_copy(update={"build_check_enabled": True, "build_gate_enforcement": "strict"})
    failures = _check_build_status(tmp_path, config, "deliver")

    assert any(f.rule == "build_timestamp_unparseable" for f in failures)
