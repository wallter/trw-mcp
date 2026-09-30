"""CODEX-P0-A S2: a codex dispatch records what its own rollout says it ran (client-reported)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TID = "01a0f03e-df17-7592-8d16-98ffd97ef1e9"


def _stdout(tid: str = TID) -> str:
    return "\n".join([json.dumps({"type": "thread.started", "thread_id": tid}), json.dumps({"type": "turn.started"})])


def _rollout(root: Path, tid: str = TID, *, model: str = "gpt-6.1-sol", effort: str | None = "medium") -> Path:
    day = root / "sessions" / "2026" / "09" / "29"
    day.mkdir(parents=True, exist_ok=True)
    turn: dict[str, object] = {"model": model, "cwd": "/x", "approval_policy": "never"}
    if effort is not None:
        turn["effort"] = effort
    lines = [
        {"type": "session_meta", "payload": {"cli_version": "0.99.1", "cwd": "/x", "instructions": "SECRET PROMPT"}},
        {"type": "response_item", "payload": {"content": "SECRET CONTENT"}},
        {"type": "turn_context", "payload": turn},
    ]
    path = day / f"rollout-2026-09-29T20-57-27-{tid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


def test_observes_model_effort_and_cli_version_from_the_rollout(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import observe_codex

    _rollout(tmp_path)
    seen = observe_codex(_stdout(), tmp_path)
    assert seen == {"model": "gpt-6.1-sol", "effort": "medium", "cli_version": "0.99.1", "by": "client-rollout"}
    assert "SECRET" not in json.dumps(seen)


@pytest.mark.parametrize(
    ("stdout", "make_rollout", "reason"),
    [
        ("not json\n", False, "no_thread_id"),
        (None, False, "rollout_missing"),
    ],
)
def test_missing_evidence_is_unknown_with_a_reason(
    tmp_path: Path, stdout: str | None, make_rollout: bool, reason: str
) -> None:
    from trw_mcp.dispatch._codex_observed import observe_codex

    seen = observe_codex(_stdout() if stdout is None else stdout, tmp_path)
    assert seen["model"] == seen["effort"] == "unknown"
    assert seen["reason"] == reason


def test_an_absent_effort_key_stays_unknown_not_copied(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import with_observed

    _rollout(tmp_path, effort=None)
    policy = {"effort": {"applied": "high"}, "model": {"applied": "gpt-6.1-sol"}}
    out = with_observed(policy, "codex", _stdout(), tmp_path)
    assert out["effort"]["observed"] == "unknown"
    assert "mismatch" not in out["effort"]
    assert out["observation"]["reason"] == "key_absent"


def test_a_model_the_cli_ran_differently_is_flagged_as_a_mismatch(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import with_observed

    _rollout(tmp_path, model="gpt-5-other")
    policy = {"effort": {"applied": "medium"}, "model": {"applied": "gpt-6.1-sol"}}
    out = with_observed(policy, "codex", _stdout(), tmp_path)
    assert out["model"] == {"applied": "gpt-6.1-sol", "observed": "gpt-5-other", "mismatch": True}
    assert "mismatch" not in out["effort"]


def test_a_thread_id_cannot_escape_the_sessions_directory(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import observe_codex

    seen = observe_codex(_stdout("../../../etc"), tmp_path)
    assert seen["reason"] == "no_thread_id"


def test_non_codex_clients_are_untouched(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import with_observed

    policy = {"model": {"applied": "x"}}
    assert with_observed(policy, "claude", _stdout(), tmp_path) is policy


def test_the_cli_json_payload_carries_the_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring: the codex CLI dispatch payload (what review_runner reads) includes ``observed``."""
    from trw_mcp.dispatch import _cli

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(tmp_path)
    policy = {"model": {"applied": "gpt-6.1-sol"}, "effort": {"applied": "medium"}}
    out = _cli.policy_with_observation(policy, "codex", _stdout())
    assert out["model"]["observed"] == "gpt-6.1-sol"
    assert out["observation"]["cli_version"] == "0.99.1"
