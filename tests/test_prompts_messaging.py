"""Tests for prompts/messaging.py — centralized AI-facing message registry.

PRD-INFRA-012: Validates message loading, value-oriented framing, fallbacks, and caching.
"""

from __future__ import annotations

import pytest

from trw_mcp.prompts.messaging import (
    _load_messages,
    get_message,
    get_message_lines,
    get_message_or_default,
)


class TestGetMessage:
    """Tests for get_message() — primary accessor."""

    def test_returns_server_instructions(self) -> None:
        msg = get_message("server_instructions")
        assert "trw_session_start" in msg
        assert len(msg) > 20

    def test_server_instructions_value_oriented(self) -> None:
        """Server instructions use value framing, not prescriptive commands."""
        msg = get_message("server_instructions")
        assert "MANDATORY" not in msg
        assert "MUST" not in msg.split()  # Not as a standalone word

    def test_returns_ceremony_warning(self) -> None:
        msg = get_message("ceremony_warning")
        assert "trw_session_start()" in msg

    def test_ceremony_warning_value_oriented(self) -> None:
        """Ceremony warning uses value framing, not threat language."""
        msg = get_message("ceremony_warning")
        assert "CRITICAL" not in msg
        assert "WILL repeat" not in msg
        assert "ACTION REQUIRED" not in msg

    def test_missing_key_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            get_message("nonexistent_key_xyz")

    def test_returns_string_type(self) -> None:
        msg = get_message("server_instructions")
        assert isinstance(msg, str)


class TestGetMessageOrDefault:
    """Tests for get_message_or_default() — fallback accessor."""

    def test_returns_message_when_key_exists(self) -> None:
        msg = get_message_or_default("server_instructions", "fallback")
        assert "trw_session_start" in msg
        assert msg != "fallback"

    def test_returns_default_on_missing_key(self) -> None:
        msg = get_message_or_default("nonexistent_key_xyz", "my fallback")
        assert msg == "my fallback"

    def test_default_with_format_substitution(self) -> None:
        msg = get_message_or_default(
            "nonexistent_key_xyz",
            "Hello {name}",
            name="world",
        )
        assert msg == "Hello world"


class TestGetMessageLines:
    """Tests for get_message_lines() — list accessor."""

    def test_string_message_returns_single_item_list(self) -> None:
        lines = get_message_lines("server_instructions")
        assert isinstance(lines, list)
        assert len(lines) == 1
        assert "trw_session_start" in lines[0]

    def test_missing_key_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            get_message_lines("nonexistent_key_xyz")


class TestLoadMessages:
    """Tests for the internal _load_messages() loader."""

    def test_returns_dict(self) -> None:
        messages = _load_messages()
        assert isinstance(messages, dict)

    def test_contains_expected_keys(self) -> None:
        messages = _load_messages()
        assert "server_instructions" in messages
        assert "ceremony_warning" in messages

    def test_caching_returns_same_object(self) -> None:
        """lru_cache ensures the same dict instance is returned."""
        m1 = _load_messages()
        m2 = _load_messages()
        assert m1 is m2
