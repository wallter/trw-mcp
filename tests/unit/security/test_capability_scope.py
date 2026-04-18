"""Unit tests for trw_mcp.security.capability_scope (PRD-INFRA-SEC-001 FR-2)."""

from __future__ import annotations

from typing import Any

import pytest

from trw_mcp.security.capability_scope import (
    CapabilityFilter,
    CapabilityScope,
    CapabilityScopeError,
    apply_scope,
    default_scopes_for_family,
)


def test_apply_scope_allows_known_args() -> None:
    scope = CapabilityScope(
        tool_name="read_file",
        allowed_args={"path": None},
    )
    call = {"name": "read_file", "args": {"path": "/etc/hosts"}}
    assert apply_scope(call, scope) is call


def test_apply_scope_rejects_extra_args() -> None:
    scope = CapabilityScope(
        tool_name="read_file",
        allowed_args={"path": None},
    )
    call = {"name": "read_file", "args": {"path": "/etc/hosts", "follow_symlinks": True}}
    with pytest.raises(CapabilityScopeError, match="disallowed arg"):
        apply_scope(call, scope)


def test_apply_scope_rate_limits_excess_calls() -> None:
    scope = CapabilityScope(
        tool_name="search",
        allowed_args=None,
        rate_limit_per_min=2,
    )
    now = [0.0]

    def clock() -> float:
        return now[0]

    def adapter(_name: str, _args: dict[str, Any]) -> str:
        return "ok"

    flt = CapabilityFilter(adapter, {"search": scope}, clock=clock)
    assert flt.call("search", {"q": "a"}) == "ok"
    now[0] = 1.0
    assert flt.call("search", {"q": "b"}) == "ok"
    now[0] = 2.0
    with pytest.raises(CapabilityScopeError, match="rate_limit_per_min"):
        flt.call("search", {"q": "c"})
    # After the 60s window rolls over, calls should succeed again.
    now[0] = 70.0
    assert flt.call("search", {"q": "d"}) == "ok"


def test_apply_scope_rejects_unknown_tool() -> None:
    def adapter(_name: str, _args: dict[str, Any]) -> str:
        return "ok"

    flt = CapabilityFilter(adapter, {})
    with pytest.raises(CapabilityScopeError, match="no registered scope"):
        flt.call("unlisted_tool", {})


def test_capability_filter_wraps_adapter_call() -> None:
    scope = CapabilityScope(tool_name="echo", allowed_args=None)
    seen: list[tuple[str, dict[str, Any]]] = []

    def adapter(name: str, args: dict[str, Any]) -> str:
        seen.append((name, args))
        return f"called {name}"

    flt = CapabilityFilter(adapter, {"echo": scope})
    result = flt.call("echo", {"msg": "hi"})
    assert result == "called echo"
    assert seen == [("echo", {"msg": "hi"})]


def test_apply_scope_name_mismatch_rejected() -> None:
    scope = CapabilityScope(tool_name="read_file", allowed_args=None)
    with pytest.raises(CapabilityScopeError, match="does not match scope"):
        apply_scope({"name": "write_file", "args": {}}, scope)


def test_apply_scope_non_dict_args_rejected() -> None:
    scope = CapabilityScope(tool_name="read_file", allowed_args=None)
    with pytest.raises(CapabilityScopeError, match="must be a dict"):
        apply_scope({"name": "read_file", "args": "not-a-dict"}, scope)


def test_default_scopes_for_family_builds_prefixed_scopes() -> None:
    scopes = default_scopes_for_family(
        "trw_",
        ["trw_learn", "trw_recall", "other_tool"],
        rate_limit_per_min=30,
    )
    assert set(scopes.keys()) == {"trw_learn", "trw_recall"}
    assert all(s.rate_limit_per_min == 30 for s in scopes.values())
    assert all(s.allowed_args is None for s in scopes.values())
