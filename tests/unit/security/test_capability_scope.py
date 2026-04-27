"""Unit tests for trw_mcp.security.capability_scope (PRD-INFRA-SEC-001 FR-2).

The module was rewritten in commit 43caa7c4 (April 2026): the legacy
``CapabilityFilter`` / ``default_scopes_for_family`` rate-limiting API was
removed in favor of a smaller, declarative ``apply_scope`` validator that
just checks server/tool/phase/scope alignment.  These tests pin the
current behavior end-to-end.
"""

from __future__ import annotations

import pytest

from trw_mcp.security.capability_scope import (
    CapabilityScope,
    CapabilityScopeError,
    apply_scope,
    scope_from_allowed_tool,
)
from trw_mcp.security.mcp_registry import AllowedTool


def _make_scope(
    *,
    server_name: str = "trw",
    tool_name: str = "trw_recall",
    allowed_phases: tuple[str, ...] = ("research", "plan"),
    allowed_scopes: tuple[str, ...] = ("read",),
) -> CapabilityScope:
    return CapabilityScope(
        server_name=server_name,
        tool_name=tool_name,
        allowed_phases=allowed_phases,
        allowed_scopes=allowed_scopes,
    )


def test_apply_scope_happy_path_returns_none() -> None:
    """When server/tool/phase/scope all match, apply_scope returns silently."""
    scope = _make_scope()
    # Returns None on success — no exception.
    assert (
        apply_scope(
            server_name="trw",
            tool_name="trw_recall",
            scope=scope,
            current_phase="research",
            requested_scope="read",
        )
        is None
    )


def test_apply_scope_rejects_server_name_mismatch() -> None:
    """Calls routed to the wrong server raise CapabilityScopeError."""
    scope = _make_scope(server_name="trw")
    with pytest.raises(CapabilityScopeError, match="not authorized for server"):
        apply_scope(
            server_name="filesystem",  # mismatch
            tool_name="trw_recall",
            scope=scope,
            current_phase="research",
            requested_scope="read",
        )


def test_apply_scope_rejects_tool_name_mismatch() -> None:
    """A tool name not matching the registered scope is rejected."""
    scope = _make_scope(tool_name="trw_recall")
    with pytest.raises(CapabilityScopeError, match="does not match scope"):
        apply_scope(
            server_name="trw",
            tool_name="trw_learn",  # mismatch
            scope=scope,
            current_phase="research",
            requested_scope="read",
        )


def test_apply_scope_rejects_disallowed_phase() -> None:
    """A current_phase outside scope.allowed_phases triggers rejection."""
    scope = _make_scope(allowed_phases=("research", "plan"))
    with pytest.raises(CapabilityScopeError, match="not allowed during phase"):
        apply_scope(
            server_name="trw",
            tool_name="trw_recall",
            scope=scope,
            current_phase="deliver",  # not in allowed_phases
            requested_scope="read",
        )


def test_apply_scope_rejects_disallowed_requested_scope() -> None:
    """A requested_scope outside scope.allowed_scopes triggers rejection."""
    scope = _make_scope(allowed_scopes=("read",))
    with pytest.raises(CapabilityScopeError, match="not allowed for scope"):
        apply_scope(
            server_name="trw",
            tool_name="trw_recall",
            scope=scope,
            current_phase="research",
            requested_scope="write",  # not in allowed_scopes
        )


def test_apply_scope_skips_phase_check_when_current_phase_is_none() -> None:
    """current_phase=None bypasses the phase gate (legacy callers)."""
    scope = _make_scope(allowed_phases=("research",))
    apply_scope(
        server_name="trw",
        tool_name="trw_recall",
        scope=scope,
        current_phase=None,
        requested_scope="read",
    )


def test_apply_scope_skips_scope_check_when_requested_scope_is_none() -> None:
    """requested_scope=None bypasses the scope gate (legacy callers)."""
    scope = _make_scope(allowed_scopes=("read",))
    apply_scope(
        server_name="trw",
        tool_name="trw_recall",
        scope=scope,
        current_phase="research",
        requested_scope=None,
    )


def test_scope_from_allowed_tool_copies_phases_and_scopes() -> None:
    """scope_from_allowed_tool builds a CapabilityScope from an AllowedTool."""
    allowed = AllowedTool(
        name="trw_recall",
        allowed_phases=("research", "plan", "implement"),
        allowed_scopes=("read",),
    )
    scope = scope_from_allowed_tool("trw", allowed)
    assert scope.server_name == "trw"
    assert scope.tool_name == "trw_recall"
    assert scope.allowed_phases == ("research", "plan", "implement")
    assert scope.allowed_scopes == ("read",)
    # And the resulting scope is honored by apply_scope.
    apply_scope(
        server_name="trw",
        tool_name="trw_recall",
        scope=scope,
        current_phase="implement",
        requested_scope="read",
    )
