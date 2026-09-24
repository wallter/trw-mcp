"""A passing build check is refused while a test-narrowing switch is set (RETRO-6.0.0 #9, L-0QSU).

Lanes reported READY from suites run with ``TRW_E1_ORACLE=1``, which skips the
BLOCKED tests; the release suite, run without it, then failed 111-128 tests.
``trw_build_check`` executes nothing, so it checks what it can see: its own
environment (inherited from the client session) and the text the caller reports
(scope, command labels, limitations). A failing run still records.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TRW_E1_ORACLE", "TRW_DISTILL_SKIP_LIVE_NETWORK"):
        monkeypatch.delenv(name, raising=False)


def _tests_result(label: str = "pytest tests -n 4", limitations: str = "") -> list[dict[str, object]]:
    return [
        {
            "command_id": "tests",
            "label": label,
            "command_class": "test",
            "exit_code": 0,
            "test_count": 12,
            "limitations": limitations,
        },
        {"command_id": "static_checks", "label": "ruff check", "command_class": "static", "exit_code": 0},
    ]


@pytest.mark.parametrize("switch", ["TRW_E1_ORACLE", "TRW_DISTILL_SKIP_LIVE_NETWORK"])
def test_a_pass_is_refused_while_a_switch_is_set_in_the_environment(
    build_check_invoke: Any, monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    monkeypatch.setenv(switch, "1")

    with pytest.raises(ValueError, match=switch):
        build_check_invoke(tests_passed=True, test_count=12)


def test_a_pass_is_refused_when_the_reported_command_names_a_switch(build_check_invoke: Any) -> None:
    with pytest.raises(ValueError, match="TRW_E1_ORACLE"):
        build_check_invoke(tests_passed=True, command_results=_tests_result("TRW_E1_ORACLE=1 pytest tests -n 4"))
    with pytest.raises(ValueError, match="TRW_DISTILL_SKIP_LIVE_NETWORK"):
        build_check_invoke(tests_passed=True, test_count=3, scope="full, TRW_DISTILL_SKIP_LIVE_NETWORK=true")
    assert build_check_invoke(tests_passed=True, test_count=3, scope="full, TRW_E1_ORACLE=0")["tests_passed"] is True


def test_a_failing_run_still_records_with_a_switch_set(
    build_check_invoke: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_E1_ORACLE", "1")

    result = build_check_invoke(tests_passed=False, test_count=12, failure_count=2)

    assert result["tests_passed"] is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TRW_E1_ORACLE", "0"),
        ("TRW_E1_ORACLE", "false"),
        ("TRW_E1_ORACLE", ""),
        ("TRW_SUITE_LOCK_SKIP", "1"),
        ("TRW_SKIP_INDEX_PREFLIGHT", "1"),
    ],
)
def test_an_off_switch_or_a_switch_that_narrows_no_test_does_not_block(
    build_check_invoke: Any, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """The suite-lock bypass only stops serializing; the installer preflight bypass is set by tests on purpose."""
    monkeypatch.setenv(name, value)

    assert build_check_invoke(tests_passed=True, test_count=12)["tests_passed"] is True
