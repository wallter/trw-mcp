"""Tests for the server tool-exposure filter (PRD-CORE-125-FR02).

Focus: the fail-CLOSED contract. Under a restrictive exposure mode a config
or list_tools failure must NOT leave every tool registered (which would widen
exposure and leak privileged tools the operator meant to hide).
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMcp:
    """Records remove_tool calls; list_tools can be made to raise."""

    def __init__(self, names: list[str], *, raise_on_list: bool = False) -> None:
        self._names = list(names)
        self._raise_on_list = raise_on_list
        self.removed: list[str] = []

    async def list_tools(self) -> list[_FakeTool]:
        if self._raise_on_list:
            raise RuntimeError("list_tools boom")
        return [_FakeTool(n) for n in self._names if n not in self.removed]

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)


def _make_config(mode: str, *, exposure_list: list[str] | None = None) -> Any:
    class _Cfg:
        effective_tool_exposure_mode = mode
        tool_exposure_list = exposure_list or []

    return _Cfg()


class TestExposureFilterFailClosed:
    def test_minimal_mode_removes_admin_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: a restrictive mode removes tools outside its preset."""
        import trw_mcp.server._tools as tools_mod

        fake = _FakeMcp(["trw_session_start", "trw_learn", "trw_meta_tune_rollback"])
        monkeypatch.setattr(tools_mod, "mcp", fake)
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: _make_config("minimal"),
            raising=False,
        )

        tools_mod._apply_tool_exposure_filter()

        # Admin tool is outside the minimal preset -> removed.
        assert "trw_meta_tune_rollback" in fake.removed
        # Core tool stays.
        assert "trw_session_start" not in fake.removed

    def test_load_failure_under_minimal_does_not_serve_full_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: a list_tools failure AFTER a restrictive mode is known
        must fail to the SAFE subset, never leave privileged tools registered."""
        import trw_mcp.server._tools as tools_mod

        # First list_tools (in the main try) raises; the fallback pass then
        # enumerates and prunes anything outside the safe core subset.
        calls = {"n": 0}
        registered = ["trw_session_start", "trw_learn", "trw_meta_tune_rollback", "trw_query_events"]

        class _FlakyMcp(_FakeMcp):
            async def list_tools(self) -> list[_FakeTool]:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("boom")
                return [_FakeTool(n) for n in registered if n not in self.removed]

        fake = _FlakyMcp(registered)
        monkeypatch.setattr(tools_mod, "mcp", fake)
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: _make_config("minimal"),
            raising=False,
        )

        tools_mod._apply_tool_exposure_filter()

        # The privileged admin + observability tools must have been pruned by
        # the fail-safe fallback (they are outside the core preset).
        assert "trw_meta_tune_rollback" in fake.removed
        assert "trw_query_events" in fake.removed
        # A core tool must NOT be removed.
        assert "trw_session_start" not in fake.removed

    def test_all_mode_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mode 'all' (or unset) keeps every tool registered."""
        import trw_mcp.server._tools as tools_mod

        fake = _FakeMcp(["trw_session_start", "trw_meta_tune_rollback"])
        monkeypatch.setattr(tools_mod, "mcp", fake)
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: _make_config("all"),
            raising=False,
        )

        tools_mod._apply_tool_exposure_filter()
        assert fake.removed == []
