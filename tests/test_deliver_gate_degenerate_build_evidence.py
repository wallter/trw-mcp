"""WD-01 — a build check that ran ZERO tests is not passing evidence.

External audit + independent skeptic (2026-09-04). ``_build_passed`` accepted
any ``build_check_complete`` whose ``tests_passed`` was truthy, so
``trw_build_check(tests_passed=True, test_count=0, scope="")`` — a report that
no tests ran, against no named scope — satisfied the deliver-time build gate.

The audit scoped this correctly: under ``evidence_receipt_mode: enforce`` the
typed BuildReceipt requirement blocks it anyway. But ``observe`` is the shipped
default, and there ``build_receipt_content_stale_warning`` returns ``None`` for
``typed_absent`` — leaving this predicate as the only remaining check. The rule
is therefore applied regardless of evidence mode.

Two supporting changes are pinned here as well, because the rule is only
correct with them:

- ``_log_build_event`` must WRITE ``test_count``; without it the gate is
  measuring a field that never existed and every build check reads as zero.
- ``derive_test_count`` must roll a typed ``command_results`` count up into the
  recorded status, or an enforce-mode caller that reports the count in the
  typed form (and leaves the flat argument at its default) would be blocked for
  evidence it actually supplied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.tools._delivery_build_gates import _build_event_payload, _build_passed
from trw_mcp.tools._delivery_helpers import _check_build_and_work_events
from trw_mcp.tools.build._build_check_helpers import derive_test_count

pytestmark = pytest.mark.integration


def _event(**payload: object) -> dict[str, object]:
    base: dict[str, object] = {
        "ts": "2026-09-04T00:00:00Z",
        "event": "build_check_complete",
        "tests_passed": True,
        "static_checks_clean": True,
        "test_count": 12,
        "scope": "pytest tests",
    }
    base.update(payload)
    return base


def test_zero_test_count_is_not_a_pass() -> None:
    """The reported repro: tests_passed=True with test_count=0."""
    assert _build_passed(_event()) is True  # non-vacuity
    assert _build_passed(_event(test_count=0)) is False
    assert _build_passed(_event(test_count="0")) is False


def test_absent_test_count_is_not_a_pass() -> None:
    """Absence is not proof tests ran — ``_log_build_event`` always writes it."""
    degenerate = _event()
    del degenerate["test_count"]
    assert _build_passed(degenerate) is False


def test_empty_scope_is_not_a_pass() -> None:
    """A check that cannot name what it validated is not evidence either."""
    assert _build_passed(_event(scope="")) is False
    assert _build_passed(_event(scope="   ")) is False


def test_the_gate_names_the_actual_fault() -> None:
    """The warning must say WHY, not fall back to "no successful build check"."""
    zero, _ = _check_build_and_work_events([_event(test_count=0), {"event": "file_modified", "file": "src/a.py"}])
    empty, _ = _check_build_and_work_events([_event(scope=""), {"event": "file_modified", "file": "src/a.py"}])
    none_at_all, _ = _check_build_and_work_events([{"event": "file_modified", "file": "src/a.py"}])

    assert zero is not None and "test_count=0" in zero
    assert empty is not None and "empty scope" in empty
    assert none_at_all is not None and "No successful build check" in none_at_all


def test_a_self_reported_failing_build_is_still_the_ordinary_missing_case() -> None:
    """Only a tests_passed=True artifact can be *degenerate*; a reported failure is not."""
    failing, _ = _check_build_and_work_events(
        [_event(tests_passed=False, test_count=0), {"event": "file_modified", "file": "src/a.py"}]
    )
    assert failing is not None and "No successful build check" in failing


def test_nested_data_payloads_are_checked_too() -> None:
    """Flat and nested event shapes must not diverge."""
    nested_ok = {"event": "build_check_complete", "data": {"tests_passed": True, "test_count": 3, "scope": "pytest"}}
    nested_bad = {"event": "build_check_complete", "data": {"tests_passed": True, "test_count": 0, "scope": "pytest"}}
    assert _build_passed(nested_ok) is True
    assert _build_passed(nested_bad) is False


def test_build_check_writes_the_test_count_it_was_given(build_check_invoke: object, tmp_project: Path) -> None:
    """End to end: the recorded event carries test_count, so the gate can read it."""
    from trw_mcp.state.persistence import FileStateReader

    run_dir = tmp_project / ".trw" / "runs" / "wd01" / "20260904T000000Z-dddd4444"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "events.jsonl").touch()

    invoke = build_check_invoke  # fixture: calls the registered trw_build_check
    assert callable(invoke)
    invoke(run_path=str(run_dir), tests_passed=True, static_checks_clean=True, test_count=7, scope="pytest tests")

    events = FileStateReader().read_jsonl(run_dir / "meta" / "events.jsonl")
    complete = [e for e in events if str(e.get("event", "")) == "build_check_complete"]
    assert complete, events

    payload = _build_event_payload(complete[-1])
    assert int(str(payload["test_count"])) == 7
    assert _build_passed(complete[-1]) is True


def test_typed_command_results_supply_the_count_when_the_flat_arg_is_default() -> None:
    """An enforce-mode caller reporting the count in typed form is not blocked for it."""
    from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass

    typed = (
        BuildCommandResult(
            command_id="tests", label="pytest", command_class=CommandClass.TEST, exit_code=0, test_count=41
        ),
        BuildCommandResult(command_id="static_checks", label="mypy", command_class=CommandClass.STATIC, exit_code=0),
    )
    assert derive_test_count(typed, reported=0) == 41
    # The caller's own explicit number always wins.
    assert derive_test_count(typed, reported=5) == 5
    # Nothing to derive from stays zero rather than inventing a count.
    assert derive_test_count(None, reported=0) == 0
