"""Tests for trw_assess (trw-jev slice 1, opt-in decision seam).

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
from trw_memory.decisions._models import DecisionFailure

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.surface_packs import (
    KERNEL_TOOLS,
    PACK_TOOLS,
    REVIEWER_TOOLS,
    STANDARD_TASK_PACKS,
)
from trw_mcp.server._surface_manifest_registry import resolve_tool_surface
from trw_mcp.server._tools import raw_registered_tool_names
from trw_mcp.tools.assess import register_assess_tools

_NOUL_QUESTION = {
    "is_duplicate": {
        "type": "noul",
        "instructions": "Same root cause?",
        "criteria": {"true": "same failure and remedy", "false": "different failure"},
    }
}


@pytest.fixture(autouse=True)
def _no_operator_jev(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tests never see the developer's own Jev switch: an empty HOME and no Jev env."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    for name in ("TRW_JEV_ENABLED", "TRW_JEV_BASE_URL", "TRW_JEV_MODEL", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def decision_server() -> FastMCP:
    server = FastMCP("decision-test")
    register_assess_tools(server)
    return server


def _call(server: FastMCP, questions: dict[str, Any], state: Any) -> dict[str, Any]:
    result = asyncio.run(server.call_tool("trw_assess", {"questions": questions, "state": state}))
    payload = result.structured_content
    assert isinstance(payload, dict)
    return payload


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> TRWConfig:
    config = TRWConfig(assess_enabled=True, **overrides)
    monkeypatch.setattr("trw_mcp.tools.assess.get_config", lambda: config)
    return config


class _FakeJudge:
    """A DecisionJudge test double with a canned result (``None`` = a provider failure)."""

    def __init__(self, result: DecisionResult | None) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def decide(self, state: Any, questions: Any, *, timeout_s: float = 10.0, session_id: str | None = None):
        self.calls.append({"state": state, "questions": questions, "timeout_s": timeout_s})
        return self._result or DecisionFailure(kind="provider_error", detail="fake failure")


# -- Disabled path: no network, no judge construction ----------------------


def test_disabled_path_returns_disabled_with_no_network(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = TRWConfig()
    assert config.assess_enabled is False
    monkeypatch.setattr("trw_mcp.tools.assess.get_config", lambda: config)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("judge_from_env must not be called when assess_enabled is false")

    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", _boom)

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
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: fake)

    payload = _call(decision_server, _NOUL_QUESTION, "learning summary text")

    assert payload["status"] == "complete"
    assert payload["backend"] == "jev"
    assert payload["model"] == "typesafe/jev-1.13-20260917"
    assert payload["outcomes"]["is_duplicate"]["noul"] == pytest.approx(0.87)
    assert payload["usage"]["cost"] == pytest.approx(0.000034)
    assert payload["latency_ms"] == pytest.approx(250.0)
    assert len(fake.calls) == 1


def test_enabled_abstains_when_judge_returns_none(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    fake = _FakeJudge(None)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: fake)

    payload = _call(decision_server, _NOUL_QUESTION, "state")

    assert payload["status"] == "failed"
    assert payload["outcomes"]["is_duplicate"]["failure"]["kind"] == "provider_error"
    assert payload["backend"] == "null"


def test_failed_jev_call_is_attributed_to_jev_not_null(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Jev transport failure must be logged as backend=jev, so the error-rate evidence counts it."""
    import httpx
    from trw_memory.decisions._jev_http import JevHttpJudge

    _enable(monkeypatch)
    failing = JevHttpJudge(api_key="k", transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: failing)

    payload = _call(decision_server, _NOUL_QUESTION, "state")

    assert payload["status"] == "failed"
    assert payload["backend"] == "jev"
    assert payload["outcomes"]["is_duplicate"]["failure"]["kind"] == "auth"
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

    def _spy(*args: Any, **kwargs: Any):
        judge = original(*args, **kwargs)
        resolved.append(judge)
        return judge

    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", _spy)

    payload = _call(decision_server, _NOUL_QUESTION, "state")

    assert isinstance(resolved[0], NullJudge)
    assert payload["status"] == "failed"
    assert payload["outcomes"]["is_duplicate"]["failure"]["kind"] == "disabled"


def test_invalid_question_type_is_rejected(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: _FakeJudge(None))

    with pytest.raises(Exception):  # fastmcp wraps the tool's ValueError as a ToolError
        asyncio.run(
            decision_server.call_tool(
                "trw_assess",
                {"questions": {"q": {"type": "not_a_real_type", "instructions": "x"}}, "state": "s"},
            )
        )


def test_a_shape_error_names_each_field_by_path_and_shows_a_valid_payload(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first-call mistake agents make ({question, options}) must be fixable from the message alone."""
    import json

    from fastmcp.exceptions import ToolError

    _enable(monkeypatch)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: _FakeJudge(None))
    wrong = {"q": {"type": "choice", "question": "Which?", "options": ["a", "b"]}}

    with pytest.raises(ToolError) as caught:
        _call(decision_server, wrong, "s")
    message = str(caught.value)
    with pytest.raises(ToolError, match=r"questions\.q: Unable to extract tag using discriminator 'type'"):
        _call(decision_server, {"q": {"question": "Which?"}}, "s")
    with pytest.raises(ToolError, match=r"questions\.q\.instructions: Field required"):
        asyncio.run(
            decision_server.call_tool("trw_assess", {"questions": wrong, "state": "s", "items": {"a": "x", "b": "y"}})
        )

    assert "invalid questions (4 error(s)): questions.q.instructions: Field required; " in message
    assert "questions.q.criteria: Field required; questions.q.question: Extra inputs are not permitted." in message
    # The example is a payload the tool accepts, not prose that merely looks like one.
    example = json.loads(message.split("Valid example: ", 1)[1])
    assert _call(decision_server, example["questions"], example["state"])["status"] == "failed"


def test_state_is_redacted_before_reaching_the_judge(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    fake = _FakeJudge(None)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: fake)

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
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: _FakeJudge(result))
    monkeypatch.setattr("trw_mcp.tools.assess.find_active_run", lambda context: sample_run_dir)

    _call(decision_server, _NOUL_QUESTION, "state")

    events_path = sample_run_dir / "meta" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    decision_events = [json.loads(line) for line in lines if json.loads(line).get("event") == "decision"]
    assert len(decision_events) == 1
    event = decision_events[0]
    assert event["question_ids"] == ["is_duplicate"]
    assert event["backend"] == "jev"
    assert event["model"] == "m"  # the served model id, so a silent jev-latest change is auditable
    assert "state" not in event
    assert event["answers"]["is_duplicate"]["noul"] == pytest.approx(0.5)


def test_decision_event_skipped_without_active_run(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: _FakeJudge(None))
    monkeypatch.setattr("trw_mcp.tools.assess.find_active_run", lambda context: None)

    # Must not raise even with no active run.
    payload = _call(decision_server, _NOUL_QUESTION, "state")
    assert payload["status"] == "failed"


def test_items_asks_every_question_about_each_item_in_one_call(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch-first: items={} fans the same questions over many items without N round-trips."""
    _enable(monkeypatch)

    class _Echo:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def decide(self, state: Any, questions: Any, *, timeout_s: float = 10.0, session_id: str | None = None):
            self.calls.append(questions)
            answers = {
                qid: {"type": "noul", "noul": 0.9 if "jwt" in q["instructions"] else 0.1}
                for qid, q in questions.items()
            }
            return DecisionResult(model="m", answers=answers, usage={}, backend="jev", latency_ms=5.0)

    echo = _Echo()
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: echo)
    result = asyncio.run(
        decision_server.call_tool(
            "trw_assess",
            {
                "questions": _NOUL_QUESTION,
                "state": "review findings",
                "items": {"f1": {"finding": "jwt leaked"}, "f2": {"finding": "typo"}},
            },
        )
    )
    payload = result.structured_content
    assert isinstance(payload, dict)
    assert len(echo.calls) == 1 and len(echo.calls[0]) == 2
    assert payload["status"] == "complete" and payload["unanswered"] == []
    assert payload["items"]["f1"]["is_duplicate"]["noul"] == pytest.approx(0.9)
    assert payload["items"]["f2"]["is_duplicate"]["noul"] == pytest.approx(0.1)
    # provenance once per call, not per item
    assert payload["model"] == "m" and payload["backend"] == "jev"
    assert all(set(outcomes) == {"is_duplicate"} for outcomes in payload["items"].values())


def test_wrong_noul_criteria_keys_are_a_caller_error(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: _FakeJudge(None))
    with pytest.raises(Exception):
        asyncio.run(
            decision_server.call_tool(
                "trw_assess",
                {
                    "questions": {"q": {"type": "noul", "instructions": "x", "criteria": {"yes": "a", "no": "b"}}},
                    "state": "s",
                },
            )
        )


# -- Surface / pack membership ----------------------------------------------


def test_trw_assess_is_registered() -> None:
    assert "trw_assess" in raw_registered_tool_names()


def test_trw_assess_belongs_to_assess_support_pack_only() -> None:
    owning_packs = [pack for pack, tools in PACK_TOOLS.items() if "trw_assess" in tools]
    assert owning_packs == ["assess_support"]


def test_trw_assess_not_in_kernel() -> None:
    assert "trw_assess" not in KERNEL_TOOLS


def test_trw_assess_not_in_any_standard_task_pack() -> None:
    for task_type, packs in STANDARD_TASK_PACKS.items():
        assert "assess_support" not in packs, f"assess_support leaked into task pack '{task_type}'"


def test_trw_assess_not_in_reviewer_tools() -> None:
    """Negative test (spec-required): a reviewer lane must never reach a tool
    that can perform third-party network egress."""
    assert "trw_assess" not in REVIEWER_TOOLS


def test_assess_support_pack_not_admitted_without_opt_in() -> None:
    resolution = resolve_tool_surface("coding", "standard")
    assert "trw_assess" not in resolution.tools


def test_assess_support_pack_admitted_with_opt_in() -> None:
    resolution = resolve_tool_surface("coding", "standard", assess_enabled=True)
    assert "trw_assess" in resolution.tools
    assert "assess_support" in resolution.packs


_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I3PlFUP0THsR8U"


def test_jwt_nested_in_state_never_egresses() -> None:
    from trw_mcp.tools.assess import _redact_state

    out = _redact_state({"a": {"b": [_JWT]}})
    assert _JWT not in repr(out)
    assert "dozjgNryP4J3jVmNHl0w5N" not in repr(out)


def test_secret_named_key_value_never_egresses() -> None:
    from trw_mcp.tools.assess import _redact_state

    out = _redact_state({"password": "hunter2hunter2", "nested": {"api_key": "plainvalue123"}, "note": "ok"})
    assert "hunter2hunter2" not in repr(out)
    assert "plainvalue123" not in repr(out)
    assert out["note"] == "ok"


def test_bearer_values_and_pem_never_egress() -> None:
    from trw_mcp.tools.assess import _redact_state

    token = "abcdefghijklmnopqrstuvwxyz0123"
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"
    for text in (f"Authorization: Bearer {token}", f"Bearer {token}", f"use Token {token} here"):
        assert token not in _redact_state(text)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in _redact_state({"k": pem})["k"]


def test_ordinary_keys_that_contain_secret_words_are_kept() -> None:
    from trw_mcp.tools.assess import _redact_state

    out = _redact_state({"author": "ada", "tokens_used": 12, "db_password": "x1", "github_token": "y2"})
    assert out["author"] == "ada" and out["tokens_used"] == 12
    assert out["db_password"] == "<REDACTED:secret>" and out["github_token"] == "<REDACTED:secret>"


def test_camel_case_oauth_keys_never_egress() -> None:
    from trw_mcp.tools.assess import _redact_state

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
    from trw_mcp.tools.assess import _redact_state

    prose = "Token expiration handling failed; Bearer authentication required"
    assert _redact_state(prose) == prose
    assert "abc123def456" not in _redact_state("Bearer abc123def456")


# -- Ported from the 5.0.0 hardening of the pre-rename module (R2/N1/N2/F1) ------


def test_question_prose_and_ids_are_redacted_before_egress(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2+N1: question text and ids reach the POST body; both get state's redaction."""
    _enable(monkeypatch)
    fake = _FakeJudge(None)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: fake)
    _call(
        decision_server,
        {
            f"leak-{_JWT}": {
                "type": "noul",
                "instructions": f"Is {_JWT} valid?",
                "criteria": {"true": f"token {_JWT}", "false": "n"},
            },
            "q2": {"type": "score", "instructions": "rate it", "criteria": [f"low {_JWT}", "high"]},
            "q3": {"type": "choice", "instructions": "pick", "criteria": {"a": f"see {_JWT}", "b": "other"}},
        },
        "state",
    )
    rendered = json.dumps(fake.calls[0]["questions"])
    assert _JWT not in rendered and "dozjgNryP4J3jVmNHl0w5N" not in rendered
    assert "rate it" in rendered and "high" in rendered


def test_run_event_carries_redacted_question_ids(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, sample_run_dir: Path
) -> None:
    """N1: the id is persisted in the run event, so it is redacted there too."""
    _enable(monkeypatch)
    result = DecisionResult(model="m", answers={}, usage={}, backend="jev", latency_ms=1.0)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: _FakeJudge(result))
    monkeypatch.setattr("trw_mcp.tools.assess.find_active_run", lambda context: sample_run_dir)
    _call(decision_server, {f"leak-{_JWT}": _NOUL_QUESTION["is_duplicate"]}, "state")
    text = (sample_run_dir / "meta" / "events.jsonl").read_text(encoding="utf-8")
    assert _JWT not in text


def test_colliding_question_ids_do_not_drop_a_question(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N1+N2: two ids that redact to the same placeholder must stay two questions."""
    _enable(monkeypatch)
    fake = _FakeJudge(None)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: fake)
    payload = _call(
        decision_server,
        {"alice@example.com": _NOUL_QUESTION["is_duplicate"], "bob@example.com": _NOUL_QUESTION["is_duplicate"]},
        "state",
    )
    assert len(fake.calls[0]["questions"]) == 2
    assert "example.com" not in json.dumps(fake.calls[0]["questions"])
    assert set(payload["outcomes"]) == {"alice@example.com", "bob@example.com"}


def test_validation_error_text_is_redacted(decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    """F1: a question that fails validation must not echo its raw prose in the error."""
    _enable(monkeypatch)
    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", lambda *args, **kwargs: _FakeJudge(None))
    with pytest.raises(Exception) as excinfo:
        asyncio.run(
            decision_server.call_tool(
                "trw_assess",
                {
                    "questions": {
                        "q": {"type": "choice", "instructions": f"use {_JWT}", "criteria": {"only": "x"}, "note": 1}
                    },
                    "state": "s",
                },
            )
        )
    assert _JWT not in str(excinfo.value)


def test_state_keys_that_redact_alike_are_kept_apart() -> None:
    """N2: the same collapse applied to ordinary state, not just criteria."""
    from trw_mcp.tools.assess import _redact_state

    out = _redact_state({"alice@example.com": "approve", "bob@example.com": "reject"})
    assert len(out) == 2 and sorted(out.values()) == ["approve", "reject"]


def test_tuple_under_an_ordinary_key_is_still_redacted() -> None:
    """F3: a tuple is a container too; it egresses as a list, redacted."""
    from trw_mcp.tools.assess import _redact_state

    token = "abcdefghijklmnopqrstuvwxyz0123"
    out = _redact_state({"notes": ("plain note", f"Bearer {token}", 7)})
    assert isinstance(out["notes"], list) and token not in repr(out)
    assert out["notes"][0] == "plain note" and out["notes"][2] == 7


def test_secret_bearing_dict_keys_are_redacted() -> None:
    """F4: a key can carry the credential as easily as a value."""
    from trw_mcp.tools.assess import _redact_state

    out = _redact_state({"sk-or-v1-abcdef0123456789abcdef": "some value", "plain": 1})
    assert "sk-or-v1-abcdef0123456789abcdef" not in repr(out) and out["plain"] == 1


def test_container_under_a_secret_key_is_redacted_whole() -> None:
    """release-verify R2: a list/tuple/nested dict under a secret key escaped the whole-value rule."""
    from trw_mcp.tools.assess import _redact_state

    out = _redact_state(
        {
            "password": ["plainlist1", {"deep": "plainlist2"}],
            "credentials": {"user": "ada", "value": "plaindict1"},
            "api_key": ("plaintuple1",),
            "retries": 3,
        }
    )
    assert out["password"] == out["credentials"] == out["api_key"] == "<REDACTED:secret>" and out["retries"] == 3
    for leaked in ("plainlist1", "plainlist2", "plaindict1", "plaintuple1"):
        assert leaked not in repr(out)


# -- enablement precedence (lane J P1/D1; relaxed 2026-09-23: a project may now enable, not only disable) --


def _resolve(monkeypatch: pytest.MonkeyPatch, project: Path) -> list[Any]:
    """Route the tool at ``project``; record the judge the REAL factory builds, answer with a fake."""
    from trw_memory.decisions import judge_from_env as real

    monkeypatch.setattr("trw_mcp.tools.assess.resolve_project_root", lambda: project)
    seen: list[Any] = []

    def _spy(env: Any = None, **kwargs: Any) -> Any:
        seen.append((dict(env or {}), real(env, **kwargs)))
        return _FakeJudge(None)

    monkeypatch.setattr("trw_memory.decisions._env.judge_from_env", _spy)
    return seen


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_machine_config_switch_enables_the_backend(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    _enable(monkeypatch)
    _write(_no_operator_jev / ".trw" / "config.yaml", "assess_enabled: true\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-000000000000000000")
    seen = _resolve(monkeypatch, tmp_path)

    _call(decision_server, _NOUL_QUESTION, "state")

    _env, judge = seen[0]
    assert type(judge).__name__ == "JevHttpJudge"


def test_explicit_env_off_beats_the_machine_switch(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    _enable(monkeypatch)
    _write(_no_operator_jev / ".trw" / "config.yaml", "assess_enabled: true\n")
    monkeypatch.setenv("TRW_JEV_ENABLED", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-000000000000000000")
    seen = _resolve(monkeypatch, tmp_path)

    _call(decision_server, _NOUL_QUESTION, "state")

    assert isinstance(seen[0][1], NullJudge)


def test_explicit_env_off_beats_a_project_that_enables_itself(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    """2026-09-23: a project may now enable, but the process env still outranks project scope."""
    _enable(monkeypatch)
    _write(tmp_path / ".trw" / "config.yaml", "assess_enabled: true\n")
    monkeypatch.setenv("TRW_JEV_ENABLED", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-000000000000000000")
    seen = _resolve(monkeypatch, tmp_path)

    _call(decision_server, _NOUL_QUESTION, "state")

    assert isinstance(seen[0][1], NullJudge)


def test_project_config_now_enables_the_backend(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    """2026-09-23 operator decision: relaxes the prior 'project can only disable' rule."""
    _enable(monkeypatch)
    _write(tmp_path / ".trw" / "config.yaml", "assess_enabled: true\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-000000000000000000")
    seen = _resolve(monkeypatch, tmp_path)

    _call(decision_server, _NOUL_QUESTION, "state")

    _env, judge = seen[0]
    assert type(judge).__name__ == "JevHttpJudge"


def test_project_config_explicit_false_still_disables(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    _enable(monkeypatch)
    _write(_no_operator_jev / ".trw" / "config.yaml", "assess_enabled: true\n")
    _write(tmp_path / ".trw" / "config.yaml", "assess_enabled: false\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-000000000000000000")
    seen = _resolve(monkeypatch, tmp_path)

    _call(decision_server, _NOUL_QUESTION, "state")

    assert isinstance(seen[0][1], NullJudge)


_HOSTILE_DOTENV = """\
TRW_JEV_ENABLED=true
TRW_JEV_BASE_URL=https://attacker.example/decisions
TRW_JEV_MODEL=attacker/model
HOME=/tmp/attacker-home
TRW_HOME=/tmp/attacker-home
OPENROUTER_API_KEY=sk-or-from-dotenv-0000000000000000
"""


def test_repo_dotenv_enables_via_project_scope_but_only_the_key_crosses(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    """2026-09-23: the project ``.env``'s ``TRW_JEV_ENABLED=true`` is a legitimate project-scope
    enable (it's checked, not a repo file that can only disable) -- but the base URL, model and
    HOME/TRW_HOME it also carries are NOT read from a dotenv (release-verify R1 trust split is
    unchanged): only ``OPENROUTER_API_KEY`` crosses.
    """
    import os

    _enable(monkeypatch)
    _write(tmp_path / ".env", _HOSTILE_DOTENV)
    before = dict(os.environ)
    seen = _resolve(monkeypatch, tmp_path)

    _call(decision_server, _NOUL_QUESTION, "state")

    env, judge = seen[0]
    assert dict(os.environ) == before, "loading the repo .env must never write the process env"
    for name in ("TRW_JEV_ENABLED", "TRW_JEV_BASE_URL", "TRW_JEV_MODEL", "TRW_HOME"):
        assert name not in env, f"{name} must never leak from the project .env into the process env"
    assert env["HOME"] == str(_no_operator_jev)
    assert type(judge).__name__ == "JevHttpJudge", "the .env's TRW_JEV_ENABLED=true is now a legitimate project enable"
    assert judge._base_url == "https://openrouter.ai/api/alpha/decisions", "the .env's base URL never applies"
    assert judge._model == "~typesafe/jev-latest", "the .env's model never applies"
    assert judge._api_key == "sk-or-from-dotenv-0000000000000000"


def test_explicit_env_off_beats_a_hostile_project_dotenv(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    _enable(monkeypatch)
    _write(tmp_path / ".env", _HOSTILE_DOTENV)
    monkeypatch.setenv("TRW_JEV_ENABLED", "false")
    seen = _resolve(monkeypatch, tmp_path)

    _call(decision_server, _NOUL_QUESTION, "state")

    assert isinstance(seen[0][1], NullJudge)


def test_project_config_cannot_redirect_the_machine_layer(
    decision_server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_operator_jev: Path
) -> None:
    """A project's ``home``/``trw_home`` TRWConfig fields (which redirect where TRWConfig itself
    looks for the machine layer) must not redirect enablement's OWN, independent user-scope
    lookup -- it always reads the real ``Path.home()``, isolated here to an empty ``_no_operator_jev``.
    """
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / ".trw" / "config.yaml", "assess_enabled: true\n")
    _write(
        tmp_path / "project" / ".trw" / "config.yaml",
        f"home: {elsewhere}\ntrw_home: {elsewhere}\nuser_config_path: {elsewhere / '.trw' / 'config.yaml'}\n",
    )
    _enable(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-000000000000000000")
    seen = _resolve(monkeypatch, tmp_path / "project")

    _call(decision_server, _NOUL_QUESTION, "state")

    assert isinstance(seen[0][1], NullJudge)
