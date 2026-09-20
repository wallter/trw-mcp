"""Tests for trw_decision (trw-jev slice 1, opt-in decision seam).

Follows the comms test convention: drives the REAL tool dispatch
(``server.call_tool``), not the underlying function, so argument coercion and
the response shape are exercised exactly as an MCP client sees them.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP
from trw_memory.decisions import DecisionResult, NullJudge

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.surface_packs import (
    KERNEL_TOOLS,
    PACK_TOOLS,
    REVIEWER_TOOLS,
    STANDARD_TASK_PACKS,
)
from trw_mcp.server._surface_manifest_registry import resolve_tool_surface
from trw_mcp.server._tools import raw_registered_tool_names
from trw_mcp.tools.decision import register_decision_tools

_NOUL_QUESTION = {"is_duplicate": {"type": "noul", "instructions": "Same root cause?"}}


@pytest.fixture
def decision_server() -> FastMCP:
    server = FastMCP("decision-test")
    register_decision_tools(server)
    return server


def _call(server: FastMCP, questions: dict[str, Any], state: Any) -> dict[str, Any]:
    result = asyncio.run(server.call_tool("trw_decision", {"questions": questions, "state": state}))
    payload = result.structured_content
    assert isinstance(payload, dict)
    return payload


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> TRWConfig:
    config = TRWConfig(decision_enabled=True, **overrides)
    monkeypatch.setattr("trw_mcp.tools.decision.get_config", lambda: config)
    return config


class _FakeJudge:
    """A DecisionJudge test double with a canned result (or None)."""

    def __init__(self, result: DecisionResult | None) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def decide(self, state: Any, questions: Any, *, timeout_s: float = 10.0, session_id: str | None = None):
        self.calls.append({"state": state, "questions": questions, "timeout_s": timeout_s})
        return self._result


# -- Disabled path: no network, no judge construction ----------------------


def test_disabled_path_returns_disabled_with_no_network(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = TRWConfig()
    assert config.decision_enabled is False
    monkeypatch.setattr("trw_mcp.tools.decision.get_config", lambda: config)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("judge_from_env must not be called when decision_enabled is false")

    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", _boom)

    payload = _call(decision_server, _NOUL_QUESTION, "some state")

    assert payload == {"status": "disabled"}


# -- Enabled, with a fake judge ---------------------------------------------


def test_enabled_with_fake_judge_returns_answered(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    result = DecisionResult(
        model="typesafe/jev-1.13-20260917",
        answers={"is_duplicate": {"type": "noul", "noul": 0.87}},
        usage={"cost": 0.000034},
        backend="jev",
        latency_ms=250.0,
    )
    fake = _FakeJudge(result)
    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", lambda **kwargs: fake)

    payload = _call(decision_server, _NOUL_QUESTION, "learning summary text")

    assert payload["status"] == "answered"
    assert payload["backend"] == "jev"
    assert payload["model"] == "typesafe/jev-1.13-20260917"
    assert payload["answers"]["is_duplicate"]["noul"] == pytest.approx(0.87)
    assert payload["usage"]["cost"] == pytest.approx(0.000034)
    assert payload["latency_ms"] == pytest.approx(250.0)
    assert len(fake.calls) == 1


def test_enabled_abstains_when_judge_returns_none(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    fake = _FakeJudge(None)
    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", lambda **kwargs: fake)

    payload = _call(decision_server, _NOUL_QUESTION, "state")

    assert payload["status"] == "abstained"
    assert payload["answers"] == {}
    assert payload["backend"] == "null"


def test_failed_jev_call_is_attributed_to_jev_not_null(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Jev transport failure must be logged as backend=jev, so the error-rate evidence counts it."""
    import httpx
    from trw_memory.decisions import JevHttpJudge

    _enable(monkeypatch)
    failing = JevHttpJudge(api_key="k", transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", lambda **kwargs: failing)

    payload = _call(decision_server, _NOUL_QUESTION, "state")

    assert payload["status"] == "abstained"
    assert payload["backend"] == "jev"
    assert payload["latency_ms"] >= 0.0


def test_enabled_uses_null_judge_by_default_when_env_unconfigured(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No mocking of judge_from_env: the real factory must resolve to NullJudge
    when TRW_JEV_ENABLED/OPENROUTER_API_KEY are absent, so enabling the pack
    alone can never create network egress."""
    _enable(monkeypatch)
    monkeypatch.delenv("TRW_JEV_ENABLED", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    real_judge_from_env = pytest.importorskip("trw_memory.decisions").judge_from_env
    resolved: list[Any] = []
    original = real_judge_from_env

    def _spy(**kwargs: Any):
        judge = original(**kwargs)
        resolved.append(judge)
        return judge

    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", _spy)

    payload = _call(decision_server, _NOUL_QUESTION, "state")

    assert isinstance(resolved[0], NullJudge)
    assert payload["status"] == "abstained"


def test_invalid_question_type_is_rejected(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", lambda **kwargs: _FakeJudge(None))

    with pytest.raises(Exception):  # fastmcp wraps the tool's ValueError as a ToolError
        asyncio.run(
            decision_server.call_tool(
                "trw_decision",
                {"questions": {"q": {"type": "not_a_real_type", "instructions": "x"}}, "state": "s"},
            )
        )


def test_state_is_redacted_before_reaching_the_judge(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    fake = _FakeJudge(None)
    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", lambda **kwargs: fake)

    _call(decision_server, _NOUL_QUESTION, "contact me at someone@example.com")

    assert fake.calls[0]["state"] == "contact me at <email>"


# -- Decision event logging (fail-open) -------------------------------------


def test_decision_event_appended_to_active_run(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, sample_run_dir: Path
) -> None:
    _enable(monkeypatch)
    result = DecisionResult(
        model="m", answers={"is_duplicate": {"type": "noul", "noul": 0.5}}, usage={}, backend="jev", latency_ms=10.0
    )
    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", lambda **kwargs: _FakeJudge(result))
    monkeypatch.setattr("trw_mcp.tools.decision.find_active_run", lambda context: sample_run_dir)

    _call(decision_server, _NOUL_QUESTION, "state")

    events_path = sample_run_dir / "meta" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    decision_events = [json.loads(line) for line in lines if json.loads(line).get("event") == "decision"]
    assert len(decision_events) == 1
    event = decision_events[0]
    assert event["question_ids"] == ["is_duplicate"]
    assert event["backend"] == "jev"
    assert "state" not in event
    assert event["answers"]["is_duplicate"]["noul"] == pytest.approx(0.5)


def test_decision_event_skipped_without_active_run(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr("trw_mcp.tools.decision.judge_from_env", lambda **kwargs: _FakeJudge(None))
    monkeypatch.setattr("trw_mcp.tools.decision.find_active_run", lambda context: None)

    # Must not raise even with no active run.
    payload = _call(decision_server, _NOUL_QUESTION, "state")
    assert payload["status"] == "abstained"


# -- Surface / pack membership ----------------------------------------------


def test_trw_decision_is_registered() -> None:
    assert "trw_decision" in raw_registered_tool_names()


def test_trw_decision_belongs_to_decision_support_pack_only() -> None:
    owning_packs = [pack for pack, tools in PACK_TOOLS.items() if "trw_decision" in tools]
    assert owning_packs == ["decision_support"]


def test_trw_decision_not_in_kernel() -> None:
    assert "trw_decision" not in KERNEL_TOOLS


def test_trw_decision_not_in_any_standard_task_pack() -> None:
    for task_type, packs in STANDARD_TASK_PACKS.items():
        assert "decision_support" not in packs, f"decision_support leaked into task pack '{task_type}'"


def test_trw_decision_not_in_reviewer_tools() -> None:
    """Negative test (spec-required): a reviewer lane must never reach a tool
    that can perform third-party network egress."""
    assert "trw_decision" not in REVIEWER_TOOLS


def test_decision_support_pack_not_admitted_without_opt_in() -> None:
    resolution = resolve_tool_surface("coding", "standard")
    assert "trw_decision" not in resolution.tools


def test_decision_support_pack_admitted_with_opt_in() -> None:
    resolution = resolve_tool_surface("coding", "standard", decision_enabled=True)
    assert "trw_decision" in resolution.tools
    assert "decision_support" in resolution.packs


_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I3PlFUP0THsR8U"


def test_jwt_nested_in_state_never_egresses() -> None:
    from trw_mcp.tools.decision import _redact_state

    out = _redact_state({"a": {"b": [_JWT]}})
    assert _JWT not in repr(out)
    assert "dozjgNryP4J3jVmNHl0w5N" not in repr(out)


def test_secret_named_key_value_never_egresses() -> None:
    from trw_mcp.tools.decision import _redact_state

    out = _redact_state({"password": "hunter2hunter2", "nested": {"api_key": "plainvalue123"}, "note": "ok"})
    assert "hunter2hunter2" not in repr(out)
    assert "plainvalue123" not in repr(out)
    assert out["note"] == "ok"


def test_question_instructions_and_criteria_are_redacted_before_egress() -> None:
    """F2: question prose reaches the POST body verbatim, so it gets state's redaction."""
    from trw_mcp.tools.decision import _parse_questions

    parsed = _parse_questions(
        {
            "q1": {"type": "noul", "instructions": f"Is {_JWT} valid?", "criteria": {"true": f"token {_JWT}"}},
            "q2": {"type": "score", "instructions": "rate it", "criteria": [f"low {_JWT}", "high"]},
            "q3": {"type": "choice", "instructions": "pick", "criteria": {"a": f"see {_JWT}", "b": "other"}},
        }
    )

    rendered = repr([question.model_dump() for question in parsed.values()])
    assert _JWT not in rendered
    assert "dozjgNryP4J3jVmNHl0w5N" not in rendered
    # The surrounding prose survives — only the credential shape is removed.
    assert "rate it" in rendered and "high" in rendered


def test_question_ids_are_redacted_before_payload_and_event() -> None:
    """N1: the question ID is a JSON key in the POST body and is persisted in the run event."""
    from trw_memory.decisions._wire import build_payload

    from trw_mcp.tools.decision import _parse_questions

    parsed = _parse_questions({f"leak-{_JWT}": {"type": "noul", "instructions": "Is it?"}})

    assert _JWT not in repr(list(parsed.keys()))
    payload = build_payload("~typesafe/jev-latest", "state", parsed, None)
    assert _JWT not in repr(payload)
    assert "dozjgNryP4J3jVmNHl0w5N" not in repr(payload)


def test_colliding_question_ids_do_not_drop_a_question() -> None:
    """N1+N2: two ids that redact to the same placeholder must stay two questions."""
    from trw_mcp.tools.decision import _parse_questions

    parsed = _parse_questions(
        {
            "alice@example.com": {"type": "noul", "instructions": "first"},
            "bob@example.com": {"type": "noul", "instructions": "second"},
        }
    )

    assert len(parsed) == 2
    assert "alice@example.com" not in repr(parsed) and "bob@example.com" not in repr(parsed)
    assert sorted(question.instructions for question in parsed.values()) == ["first", "second"]


def test_choice_criteria_keep_every_option_through_redaction() -> None:
    """N2: last-write-wins silently dropped an option AFTER min_length validation passed."""
    from trw_mcp.tools.decision import _parse_questions

    parsed = _parse_questions(
        {
            "q": {
                "type": "choice",
                "instructions": "who approves?",
                "criteria": {"alice@example.com": "approve", "bob@example.com": "reject"},
            }
        }
    )

    criteria = next(iter(parsed.values())).criteria
    assert criteria is not None
    assert len(criteria) == 2, "a choice question must not lose an option to redaction"
    assert sorted(criteria.values()) == ["approve", "reject"]
    assert "alice@example.com" not in repr(criteria) and "bob@example.com" not in repr(criteria)
    # The disambiguated labels are what the backend will score, so the answer maps back.
    assert len(set(criteria)) == 2


def test_state_keys_that_redact_alike_are_kept_apart() -> None:
    """N2: the same collapse applied to ordinary state, not just criteria."""
    from trw_mcp.tools.decision import _redact_state

    out = _redact_state({"alice@example.com": "approve", "bob@example.com": "reject"})

    assert len(out) == 2
    assert sorted(out.values()) == ["approve", "reject"]


def test_tuple_under_an_ordinary_key_is_still_redacted() -> None:
    """F3: a tuple is a container too; it egresses as a list, redacted."""
    from trw_mcp.tools.decision import _redact_state

    token = "abcdefghijklmnopqrstuvwxyz0123"
    out = _redact_state({"notes": ("plain note", f"Bearer {token}", 7)})
    assert isinstance(out["notes"], list)
    assert token not in repr(out)
    assert out["notes"][0] == "plain note" and out["notes"][2] == 7


def test_secret_bearing_dict_keys_are_redacted() -> None:
    """F4: a key can carry the credential as easily as a value."""
    from trw_mcp.tools.decision import _redact_state

    out = _redact_state({"sk-or-v1-abcdef0123456789abcdef": "some value", "plain": 1})
    assert "sk-or-v1-abcdef0123456789abcdef" not in repr(out)
    assert out["plain"] == 1


def test_container_under_a_secret_key_is_redacted_whole() -> None:
    """release-verify R2: a list/tuple/nested dict under a secret key escaped the whole-value rule."""
    from trw_mcp.tools.decision import _redact_state

    out = _redact_state(
        {
            "password": ["plainlist1", {"deep": "plainlist2"}],
            "credentials": {"user": "ada", "value": "plaindict1"},
            "api_key": ("plaintuple1",),
            "retries": 3,
        }
    )
    assert out["password"] == "<REDACTED:secret>"
    assert out["credentials"] == "<REDACTED:secret>"
    assert out["api_key"] == "<REDACTED:secret>"
    assert out["retries"] == 3
    for leaked in ("plainlist1", "plainlist2", "plaindict1", "plaintuple1"):
        assert leaked not in repr(out)


def test_bearer_values_and_pem_never_egress() -> None:
    from trw_mcp.tools.decision import _redact_state

    token = "abcdefghijklmnopqrstuvwxyz0123"
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"
    for text in (f"Authorization: Bearer {token}", f"Bearer {token}", f"use Token {token} here"):
        assert token not in _redact_state(text)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in _redact_state({"k": pem})["k"]


def test_ordinary_keys_that_contain_secret_words_are_kept() -> None:
    from trw_mcp.tools.decision import _redact_state

    out = _redact_state({"author": "ada", "tokens_used": 12, "db_password": "x1", "github_token": "y2"})
    assert out["author"] == "ada" and out["tokens_used"] == 12
    assert out["db_password"] == "<REDACTED:secret>" and out["github_token"] == "<REDACTED:secret>"


def test_camel_case_oauth_keys_never_egress() -> None:
    from trw_mcp.tools.decision import _redact_state

    state = {
        "accessToken": "opaqueAbc123opaqueAbc123",
        "refreshToken": "r" * 30,
        "idToken": "i" * 20,
        "sessionToken": "s1" * 12,
        "maxTokens": 256,
    }
    out = _redact_state(state)
    for key in ("accessToken", "refreshToken", "idToken", "sessionToken"):
        assert out[key] == "<REDACTED:secret>", key
    assert out["maxTokens"] == 256


def test_bearer_and_token_prose_is_not_redacted() -> None:
    from trw_mcp.tools.decision import _redact_state

    prose = "Token expiration handling failed; Bearer authentication required"
    assert _redact_state(prose) == prose
    assert "abc123def456" not in _redact_state("Bearer abc123def456")


def test_validation_error_text_is_redacted() -> None:
    """A question that fails validation must not echo its raw prose in the error."""
    from trw_mcp.tools.decision import _parse_questions

    bad = {"q": {"type": "choice", "instructions": f"use {_JWT}", "criteria": {"only": "x"}, "note": 1}}
    with pytest.raises(ValueError) as excinfo:
        _parse_questions(bad)
    assert "invalid decision questions" in str(excinfo.value)
    assert _JWT not in str(excinfo.value)
