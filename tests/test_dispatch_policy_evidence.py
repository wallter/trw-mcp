"""CODEX-P0-A S1: the policy record separates requested, resolved and argv-applied values.

``requested`` is the caller's literal ask (null unless the request itself chose the value),
``resolved`` is what precedence produced, and ``applied`` is read from the command line the
child is actually given -- so a caller's own ``extra_args`` flag, which the child honours
last, is what the record reports.
"""

from __future__ import annotations

from trw_mcp.dispatch._types import DispatchRequest


def test_requested_is_the_literal_ask_only_when_the_request_chose_it() -> None:
    from trw_mcp.dispatch._policy import policy_record

    req = DispatchRequest(client="grok", prompt="p", model="grok-4", model_source="config")
    model = policy_record(req)["model"]
    assert model["requested"] is None
    assert model["resolved"] == "grok-4"
    assert model["source"] == "config"


def test_an_explicit_request_is_both_requested_and_resolved() -> None:
    from trw_mcp.dispatch._policy import policy_record

    req = DispatchRequest(client="codex", prompt="p", effort="high", effort_source="request")
    effort = policy_record(req)["effort"]
    assert effort == {"requested": "high", "resolved": "high", "applied": "high", "source": "request"}


def test_an_extra_args_model_override_is_what_the_record_reports_as_applied() -> None:
    from trw_mcp.dispatch._policy import policy_record

    req = DispatchRequest(
        client="codex", prompt="p", model="gpt-a", model_source="request", extra_args=("--model", "gpt-b")
    )
    model = policy_record(req)["model"]
    assert model["resolved"] == "gpt-a"
    assert model["applied"] == "gpt-b"  # the child sees the later flag


def test_an_extra_args_effort_override_is_what_the_record_reports_as_applied() -> None:
    """(codex refuses ``-c`` in extra_args as a security flag, so the flag-carrier client is used here.)"""
    from trw_mcp.dispatch._policy import policy_record

    req = DispatchRequest(
        client="claude", prompt="p", effort="medium", effort_source="table", extra_args=("--effort", "high")
    )
    effort = policy_record(req)["effort"]
    assert effort["resolved"] == "medium"
    assert effort["applied"] == "high"


def test_a_prompt_that_mentions_a_flag_is_not_read_as_one() -> None:
    from trw_mcp.dispatch._policy import policy_record

    req = DispatchRequest(client="codex", prompt="--model gpt-evil", model="gpt-a", model_source="request")
    assert policy_record(req)["model"]["applied"] == "gpt-a"


def test_a_codex_config_carried_effort_is_read_from_its_c_override() -> None:
    from trw_mcp.dispatch._policy import policy_record

    req = DispatchRequest(client="codex", prompt="p", effort="xhigh", effort_source="request")
    assert policy_record(req)["effort"]["applied"] == "xhigh"
