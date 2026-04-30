"""Unit tests for cursor-cli bootstrap reminder, typing, and detection paths."""

from __future__ import annotations

from pathlib import Path

from tests._structlog_capture import captured_structlog  # noqa: F401


class TestTtyReminder:
    """test_tty_reminder_emitted: result["info"] contains TTY and tmux."""

    def test_tty_reminder_on_fresh_init(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_text = " ".join(info)
        assert "TTY" in all_text
        assert "tmux" in all_text

    def test_github_actions_mentioned(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_text = " ".join(info)
        assert "GitHub Actions" in all_text

    def test_emit_helper_idempotent(self, tmp_path: Path) -> None:
        """Calling emit twice does not duplicate lines."""
        from trw_mcp.bootstrap._cursor_cli import _emit_cli_safety_reminder
        from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

        result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
        _emit_cli_safety_reminder(result)
        count_after_first = len(result.get("info", []))
        _emit_cli_safety_reminder(result)
        count_after_second = len(result.get("info", []))
        assert count_after_second == count_after_first, (
            "Calling _emit_cli_safety_reminder twice should not grow the info list"
        )


class TestTtyReminderStructlog:
    """Verify _emit_cli_safety_reminder emits cursor_cli_tty_reminder via structlog."""

    def test_structlog_event_emitted(self, tmp_path: Path, captured_structlog: list[dict]) -> None:
        """cursor_cli_tty_reminder event captured by structlog.testing.capture_logs."""
        from trw_mcp.bootstrap._cursor_cli import _emit_cli_safety_reminder
        from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

        result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
        _emit_cli_safety_reminder(result)

        events = [e["event"] for e in captured_structlog]
        assert "cursor_cli_tty_reminder" in events, (
            f"Expected cursor_cli_tty_reminder in structlog output; got: {events}"
        )

    def test_structlog_event_has_tty_required(self, tmp_path: Path, captured_structlog: list[dict]) -> None:
        """cursor_cli_tty_reminder log entry includes tty_required=True."""
        from trw_mcp.bootstrap._cursor_cli import _emit_cli_safety_reminder
        from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

        result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
        _emit_cli_safety_reminder(result)

        reminder_events = [e for e in captured_structlog if e.get("event") == "cursor_cli_tty_reminder"]
        assert len(reminder_events) >= 1
        assert reminder_events[0].get("tty_required") is True

    def test_generate_cli_config_emits_tty_reminder_via_structlog(
        self, tmp_path: Path, captured_structlog: list[dict]
    ) -> None:
        """generate_cursor_cli_config call emits cursor_cli_tty_reminder via structlog."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)

        events = [e["event"] for e in captured_structlog]
        assert "cursor_cli_tty_reminder" in events


class TestTypeAnnotations:
    """Verify that typed constants and TypedDicts are importable and correctly shaped."""

    def test_default_allow_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_ALLOW

        assert isinstance(_DEFAULT_ALLOW, tuple)

    def test_default_deny_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_DENY

        assert isinstance(_DEFAULT_DENY, tuple)

    def test_cli_hook_scripts_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _CLI_HOOK_SCRIPTS

        assert isinstance(_CLI_HOOK_SCRIPTS, tuple)

    def test_tty_reminder_lines_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _TTY_REMINDER_LINES

        assert isinstance(_TTY_REMINDER_LINES, tuple)

    def test_cli_hook_events_has_five_keys(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _CLI_HOOK_EVENTS

        assert len(_CLI_HOOK_EVENTS) == 5

    def test_cursor_cli_permissions_typeddict_importable(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import CursorCliPermissions  # noqa: F401

    def test_cursor_cli_config_typeddict_importable(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import CursorCliConfig  # noqa: F401


class TestCursorCliDetectionNegative:
    """cursor-cli detection must not trigger from IDE-only signals."""

    def test_cursor_rules_dir_alone_not_cursor_cli(self, tmp_path: Path) -> None:
        """Presence of .cursor/rules/ (IDE artifact) must NOT trigger cursor-cli."""
        from unittest.mock import patch

        from trw_mcp.bootstrap._utils import detect_ide

        cursor_dir = tmp_path / ".cursor"
        rules_dir = cursor_dir / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "trw-ceremony.mdc").write_text("# rules")

        with patch("shutil.which", return_value=None), patch.dict("os.environ", {}, clear=True):
            result = detect_ide(tmp_path)

        assert "cursor-cli" not in result, ".cursor/rules/ alone should not trigger cursor-cli detection"
