"""State and constant tests for trw_mcp.middleware.ceremony."""

from __future__ import annotations

import pytest

from trw_mcp.middleware.ceremony import (
    CEREMONY_TOOLS,
    CEREMONY_WARNING,
    is_session_active,
    mark_session_active,
    reset_state,
)


class TestSessionState:
    """Tests for module-level session state management."""

    @pytest.mark.unit
    def test_new_session_is_not_active(self) -> None:
        assert not is_session_active("new-session")

    @pytest.mark.unit
    def test_mark_session_active(self) -> None:
        mark_session_active("sess-1")
        assert is_session_active("sess-1")

    @pytest.mark.unit
    def test_different_sessions_independent(self) -> None:
        mark_session_active("sess-1")
        assert is_session_active("sess-1")
        assert not is_session_active("sess-2")

    @pytest.mark.unit
    def test_reset_clears_all(self) -> None:
        mark_session_active("sess-1")
        mark_session_active("sess-2")
        reset_state()
        assert not is_session_active("sess-1")
        assert not is_session_active("sess-2")

    @pytest.mark.unit
    def test_mark_idempotent(self) -> None:
        mark_session_active("sess-1")
        mark_session_active("sess-1")
        assert is_session_active("sess-1")

    @pytest.mark.unit
    def test_reset_then_mark_works(self) -> None:
        """After reset, marking a new session should work normally."""
        mark_session_active("sess-before")
        reset_state()
        mark_session_active("sess-after")
        assert is_session_active("sess-after")
        assert not is_session_active("sess-before")

    @pytest.mark.unit
    def test_reset_idempotent(self) -> None:
        """Calling reset_state twice should not raise."""
        reset_state()
        reset_state()
        assert not is_session_active("any-session")


class TestCeremonyTools:
    """Tests for the CEREMONY_TOOLS constant."""

    @pytest.mark.unit
    def test_contains_session_start(self) -> None:
        assert "trw_session_start" in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_contains_init(self) -> None:
        assert "trw_init" not in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_contains_recall(self) -> None:
        assert "trw_recall" not in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_is_frozenset(self) -> None:
        assert isinstance(CEREMONY_TOOLS, frozenset)

    @pytest.mark.unit
    def test_nonempty(self) -> None:
        assert len(CEREMONY_TOOLS) > 0

    @pytest.mark.unit
    def test_non_ceremony_tools_excluded(self) -> None:
        """Delivery and checkpoint tools are NOT ceremony initializers."""
        assert "trw_deliver" not in CEREMONY_TOOLS
        assert "trw_checkpoint" not in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_immutable(self) -> None:
        """frozenset is immutable — add() must raise."""
        with pytest.raises((AttributeError, TypeError)):
            CEREMONY_TOOLS.add("trw_fake_tool")  # type: ignore[attr-defined]


class TestCeremonyWarningText:
    """Tests for the warning text content — value-oriented framing."""

    @pytest.mark.unit
    def test_warning_is_nonempty_string(self) -> None:
        assert isinstance(CEREMONY_WARNING, str)
        assert len(CEREMONY_WARNING.strip()) > 0

    @pytest.mark.unit
    def test_warning_mentions_session_start(self) -> None:
        assert "trw_session_start()" in CEREMONY_WARNING

    @pytest.mark.unit
    def test_warning_uses_value_framing(self) -> None:
        """Warning explains what the agent gains, not what it loses."""
        lower = CEREMONY_WARNING.lower()
        assert "learnings" in lower
        assert "run state" in lower

    @pytest.mark.unit
    def test_warning_avoids_threat_framing(self) -> None:
        """No CRITICAL/MUST/WILL threat language."""
        assert "CRITICAL" not in CEREMONY_WARNING
        assert "ACTION REQUIRED" not in CEREMONY_WARNING
        assert "WILL repeat" not in CEREMONY_WARNING
