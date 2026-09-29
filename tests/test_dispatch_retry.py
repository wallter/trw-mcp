"""PRD-CORE-304-FR03: a capacity or credential-refresh failure is retried once on the same client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._dispatch_host import install_stub
from trw_mcp.dispatch import dispatch
from trw_mcp.dispatch._fallback import dispatch_with_fallback
from trw_mcp.dispatch._types import DispatchRequest

_ANSWER = json.dumps({"result": "Verdict: PASS"})


def _stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failures: list[str]) -> Path:
    """A fake claude that fails with each of *failures* (on stderr, exit 1) in turn, then answers."""
    counter = tmp_path / "launches"
    lines = "\n".join(f'[ "$n" -eq {i} ] && {{ echo "{msg}" >&2; exit 1; }}' for i, msg in enumerate(failures, 1))
    count = f'n=$(( $(cat "{counter}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{counter}"\n'
    install_stub(tmp_path, monkeypatch, f"{count}{lines}\necho '{_ANSWER}'\n")
    monkeypatch.setattr("trw_mcp.dispatch._error_class.RETRY_DELAY_SECONDS", 0.0)
    return counter


def _launches(counter: Path) -> int:
    return int(counter.read_text().strip())


def test_a_capacity_failure_is_retried_once_and_the_first_try_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _stub(tmp_path, monkeypatch, ["Error: Selected model is at capacity. Please try again."])

    result = dispatch(DispatchRequest(client="claude", prompt="review"))

    assert (result.ok, _launches(counter)) == (True, 2)
    assert [(a.client, a.reason) for a in result.attempts] == [("claude", "provider_capacity")]


def test_a_second_refresh_conflict_is_reported_not_retried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conflict = "Error: refresh token has already been used to generate a new access token"
    counter = _stub(tmp_path, monkeypatch, [conflict, conflict, conflict])

    result = dispatch(DispatchRequest(client="claude", prompt="review"))

    assert (result.ok, result.error_class, _launches(counter)) == (False, "credential_refresh_conflict", 2)
    assert "refresh token has already been used" in (result.last_error or "")


@pytest.mark.parametrize(
    "failure", ["Error: You've hit your usage limit.", "Error: not logged in. Run the login command."]
)
def test_quota_and_auth_failures_are_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    counter = _stub(tmp_path, monkeypatch, [failure])

    result = dispatch(DispatchRequest(client="claude", prompt="review"))

    assert (result.ok, _launches(counter), result.attempts) == (False, 1, [])


def test_the_fallback_chain_keeps_a_clients_own_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _stub(tmp_path, monkeypatch, ["Error: server is overloaded", "Error: You've hit your usage limit."])
    first = DispatchRequest(client="claude", prompt="review")

    result = dispatch_with_fallback(first, ["codex"], lambda _client: first, run=dispatch)

    reasons = [a.reason for a in result.attempts]
    assert reasons[:2] == ["provider_capacity", "quota_exhausted"]  # claude's retry, then its failover
    assert _launches(counter) >= 3


def test_capacity_after_the_clients_own_retry_fails_over_to_the_next_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _stub(tmp_path, monkeypatch, ["Error: server is overloaded"] * 2)
    first = DispatchRequest(client="claude", prompt="review")

    result = dispatch_with_fallback(first, ["codex"], lambda _client: first, run=dispatch)

    assert [a.reason for a in result.attempts][:2] == ["provider_capacity", "provider_capacity"]
    assert result.ok and _launches(counter) == 3  # two tries on claude, then the fallback answered


def test_a_dispatch_without_budget_for_a_retry_does_not_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """sol r1: a retry never stretches a dispatch past its one budget (a background job is killed at 1.5x)."""
    counter = _stub(tmp_path, monkeypatch, ["Error: Selected model is at capacity."])

    result = dispatch(DispatchRequest(client="claude", prompt="review", timeout_s=20))

    assert (result.error_class, _launches(counter)) == ("provider_capacity", 1)


def test_each_attempt_runs_with_what_is_left_of_the_one_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._error_class import run_dispatch
    from trw_mcp.dispatch._types import DispatchResult

    monkeypatch.setattr("trw_mcp.dispatch._error_class.RETRY_DELAY_SECONDS", 0.0)
    budgets: list[int] = []

    def run_once(req: DispatchRequest) -> DispatchResult:
        budgets.append(req.timeout_s)
        reason = "provider_capacity" if len(budgets) == 1 else None
        return DispatchResult.model_construct(
            client="claude", silence_reason=reason, structured=None, raw_stderr="", attempts=[]
        )

    run_dispatch(
        DispatchRequest(client="claude", prompt="review", timeout_s=600), run_once, lambda why: pytest.fail(why)
    )

    assert len(budgets) == 2 and budgets[0] <= 600 and budgets[1] <= budgets[0]


def test_the_retry_log_carries_no_client_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """sol r1: an opaque token a CLI echoes in plain prose defeats pattern redaction, so the log has none of it."""
    canary = "plainopaquetoken0123456789abcdef"
    _stub(tmp_path, monkeypatch, [f"Error: server is overloaded while refreshing {canary}"])
    logged: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "trw_mcp.dispatch._error_class.logger.warning", lambda event, **fields: logged.append((event, fields))
    )

    dispatch(DispatchRequest(client="claude", prompt="review"))

    assert logged and logged[0][0] == "dispatch_retry"
    assert canary not in repr(logged)
