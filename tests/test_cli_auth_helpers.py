"""Tests for CLI auth helper behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trw_mcp.cli.auth import _format_countdown, select_organization


class TestFormatCountdown:
    def test_minutes_and_seconds(self) -> None:
        assert _format_countdown(863) == "14:23"

    def test_zero(self) -> None:
        assert _format_countdown(0) == "0:00"

    def test_negative_clamps(self) -> None:
        assert _format_countdown(-5) == "0:00"

    def test_exact_minute(self) -> None:
        assert _format_countdown(60) == "1:00"

    def test_seconds_only(self) -> None:
        assert _format_countdown(45) == "0:45"


class TestSelectOrganization:
    def test_empty_list(self) -> None:
        result = select_organization([], interactive=True)
        assert result is None

    def test_single_org_auto_selects(self) -> None:
        org = {"id": "org-1", "name": "acme-corp", "slug": "acme-corp"}
        result = select_organization([org], interactive=True)
        assert result == org

    def test_multiple_non_interactive(self) -> None:
        """Non-interactive mode picks first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        result = select_organization(orgs, interactive=False)
        assert result == orgs[0]

    def test_multiple_interactive_default(self) -> None:
        """Interactive mode with empty input picks first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        mock_tty = MagicMock()
        mock_tty.readline.return_value = "\n"

        with patch("trw_mcp.cli.auth._open_tty", return_value=mock_tty):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[0]

    def test_multiple_interactive_choice(self) -> None:
        """Interactive mode with specific choice."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        mock_tty = MagicMock()
        mock_tty.readline.return_value = "2\n"

        with patch("trw_mcp.cli.auth._open_tty", return_value=mock_tty):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[1]

    def test_multiple_interactive_invalid_choice(self) -> None:
        """Invalid choice defaults to first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        mock_tty = MagicMock()
        mock_tty.readline.return_value = "99\n"

        with patch("trw_mcp.cli.auth._open_tty", return_value=mock_tty):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[0]

    def test_no_tty_defaults_to_first(self) -> None:
        """No TTY available defaults to first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]

        with patch("trw_mcp.cli.auth._open_tty", return_value=None):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[0]
