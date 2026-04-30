"""Misc coverage tests for prompt helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from trw_mcp.prompts.aaref import _DATA_DIR as _AAREF_DATA_DIR
except ImportError:
    _AAREF_DATA_DIR = Path("/nonexistent")

_AAREF_TEMPLATES = list(_AAREF_DATA_DIR.glob("*.md")) if _AAREF_DATA_DIR.is_dir() else []
_HAS_AAREF_TEMPLATES = len(_AAREF_TEMPLATES) > 0


class TestAarefPromptFallback:
    """Line 28: _load_prompt_template fallback when file not found."""

    def test_load_prompt_template_missing_file_returns_fallback(self) -> None:
        """Line 28: non-existent template returns fallback message."""
        from trw_mcp.prompts.aaref import _load_prompt_template

        result = _load_prompt_template("nonexistent_template_xyz.md")
        assert "nonexistent_template_xyz.md" in result
        assert "not found" in result

    @pytest.mark.skipif(not _HAS_AAREF_TEMPLATES, reason="No .md templates in aaref data dir")
    def test_load_prompt_template_existing_file_returns_content(self) -> None:
        """Line 26-27: existing template file returns its content."""
        from trw_mcp.prompts.aaref import _DATA_DIR, _load_prompt_template

        templates = list(_DATA_DIR.glob("*.md"))
        content = _load_prompt_template(templates[0].name)
        assert len(content) > 0
        assert "not found" not in content


class TestMessagingCoverage:
    """Lines 60, 98: kwargs formatting and list fallback."""

    def test_get_message_with_kwargs_formats_string(self) -> None:
        """Line 60: get_message with kwargs substitutes values."""
        from trw_mcp.prompts.messaging import get_message

        with patch("trw_mcp.prompts.messaging._load_messages") as mock_load:
            mock_load.return_value = {"test_key": "Hello {name}, you have {count} items"}
            result = get_message("test_key", name="Alice", count=5)

        assert result == "Hello Alice, you have 5 items"

    def test_get_message_without_kwargs_returns_raw(self) -> None:
        """Line 61 (no kwargs branch): returns raw string."""
        from trw_mcp.prompts import messaging

        with patch.object(messaging, "_load_messages") as mock_load:
            mock_load.return_value = {"simple_key": "Simple message"}
            result = messaging.get_message("simple_key")

        assert result == "Simple message"

    def test_get_message_or_default_with_kwargs_fallback(self) -> None:
        """Lines 78-79: get_message_or_default kwargs applied to default."""
        from trw_mcp.prompts.messaging import get_message_or_default

        result = get_message_or_default(
            "nonexistent_key_xyz",
            "Default {thing} message",
            thing="formatted",
        )
        assert result == "Default formatted message"

    def test_get_message_lines_returns_list_type(self) -> None:
        """Lines 97-98: get_message_lines with list value."""
        from trw_mcp.prompts import messaging

        with patch.object(messaging, "_load_messages") as mock_load:
            mock_load.return_value = {"list_key": ["item one", "item two", "item three"]}
            result = messaging.get_message_lines("list_key")

        assert result == ["item one", "item two", "item three"]

    def test_get_message_lines_non_list_wrapped(self) -> None:
        """Line 98 (else branch): non-list value wrapped in list."""
        from trw_mcp.prompts import messaging

        with patch.object(messaging, "_load_messages") as mock_load:
            mock_load.return_value = {"scalar_key": "single value"}
            result = messaging.get_message_lines("scalar_key")

        assert result == ["single value"]

    def test_get_message_with_kwargs_coerces_values_to_str(self) -> None:
        """Line 60: kwargs values are coerced to str before format."""
        from trw_mcp.prompts import messaging

        with patch.object(messaging, "_load_messages") as mock_load:
            mock_load.return_value = {"count_msg": "Count is {n}"}
            result = messaging.get_message("count_msg", n=42)

        assert result == "Count is 42"
