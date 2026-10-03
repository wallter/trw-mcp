"""assert_budget marks a missed budget UNTRUSTED when the host is saturated (load-aware verdict)."""

from __future__ import annotations

import pytest

from tests import _timing
from tests._timing import assert_budget as _budget


# The verdict of assert_budget is the unit under test, so these tests are unmarked and call it through a
# non-test helper (PRD-QUAL-141 rule 3 governs budgets on measured host values, not this helper).
def _call(*args: object, **kwargs: object) -> None:
    _budget(*args, **kwargs)  # type: ignore[arg-type]


def _load(monkeypatch: pytest.MonkeyPatch, load1: float) -> None:
    monkeypatch.setattr(_timing.os, "getloadavg", lambda: (load1, 0.0, 0.0))


def test_miss_above_default_limit_skips_with_a_visible_reason_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-102: a routine run on a busy host must not go red; the skip says what was not asserted."""
    monkeypatch.delenv("TRW_TIMING_TRUST_LOAD", raising=False)
    monkeypatch.delenv("TRW_TIMING_UNTRUSTED", raising=False)
    _load(monkeypatch, 15.0)
    with pytest.raises(pytest.skip.Exception) as exc:
        _call("op", 300.0, 250.0, "ms")
    assert str(exc.value) == "timing not asserted: load 15.0 > 12 (op: 300 ms above budget 250 ms)"
    assert _timing.RECORDS[-1]["ok"] is False  # the measurement is still recorded for the timing report


def test_the_gate_mode_fails_with_the_untrusted_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_TIMING_TRUST_LOAD", raising=False)
    monkeypatch.setenv("TRW_TIMING_UNTRUSTED", "fail")
    _load(monkeypatch, 15.0)
    with pytest.raises(AssertionError) as exc:
        _call("op", 300.0, 250.0, "ms")
    assert str(exc.value).startswith("UNTRUSTED (load 15.0 > 12)")
    assert "op: 300 ms above budget 250 ms" in str(exc.value)


def test_miss_at_or_below_limit_is_a_plain_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_TIMING_TRUST_LOAD", raising=False)
    _load(monkeypatch, 12.0)
    with pytest.raises(AssertionError) as exc:
        _call("op", 300.0, 250.0, "ms")
    assert str(exc.value) == "op: 300 ms above budget 250 ms"


def test_at_least_miss_above_limit_is_untrusted_in_gate_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_TIMING_UNTRUSTED", "fail")
    _load(monkeypatch, 30.0)
    with pytest.raises(AssertionError, match=r"^UNTRUSTED .*below budget"):
        _call("rate", 1.0, 10.0, "ops/s", at_least=True)


def test_met_budget_passes_at_any_load(monkeypatch: pytest.MonkeyPatch) -> None:
    _load(monkeypatch, 99.0)
    before = len(_timing.RECORDS)
    _call("op", 100.0, 250.0, "ms")
    _call("rate", 20.0, 10.0, "ops/s", at_least=True)
    # Both met measurements are recorded as ok, however loaded the host.
    assert [r["ok"] for r in _timing.RECORDS[before:]] == [True, True]


@pytest.mark.parametrize(("env", "load", "untrusted"), [("1000", 25.0, False), ("5", 6.0, True), ("junk", 15.0, True)])
def test_env_override_is_honoured(monkeypatch: pytest.MonkeyPatch, env: str, load: float, untrusted: bool) -> None:
    monkeypatch.setenv("TRW_TIMING_TRUST_LOAD", env)
    monkeypatch.setenv("TRW_TIMING_UNTRUSTED", "fail")
    _load(monkeypatch, load)
    with pytest.raises(AssertionError) as exc:
        _call("op", 300.0, 250.0, "ms")
    assert str(exc.value).startswith("UNTRUSTED") is untrusted
