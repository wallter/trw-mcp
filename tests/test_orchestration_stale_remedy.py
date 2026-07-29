"""The stale-run advisory may only promise a remedy the config actually runs.

``_ceremony_helpers`` calls ``auto_close_stale_runs`` only when
``run_auto_close_enabled`` is set. The advisory used to state flatly that
``trw_session_start`` would "auto-close them", which with auto-close disabled
directs the agent to a call that will not do the promised thing — a consequence
the config does not enforce (CONSTITUTION HB-1).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trw_mcp.tools import _orchestration_status_assembly as assembly
from trw_mcp.tools._orchestration_status_assembly import _stale_close_remedy


def test_remedy_offered_when_auto_close_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped default (True) keeps the actionable remedy."""
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: SimpleNamespace(run_auto_close_enabled=True),
    )
    assert "trw_session_start" in _stale_close_remedy()


def test_no_remedy_promised_when_auto_close_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With auto-close off, session_start will not close them — so do not say it will."""
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: SimpleNamespace(run_auto_close_enabled=False),
    )
    assert _stale_close_remedy() == ""


def test_unreadable_config_asserts_no_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-open weakens the claim: an unprovable remedy is not asserted."""

    def _boom() -> object:
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("trw_mcp.models.config.get_config", _boom)
    assert _stale_close_remedy() == ""


def test_stale_count_is_surfaced_regardless_of_the_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning auto-close off makes TRW quieter about the fix, never blind to staleness."""
    monkeypatch.setattr(assembly, "_stale_close_remedy", lambda: "")
    advisory = f"{3} stale run(s) detected.{assembly._stale_close_remedy()}"
    assert "3 stale run(s) detected." in advisory
