"""Tests for installer subprocess timeout helpers."""

from __future__ import annotations

import inspect
import sys
import time
from unittest.mock import MagicMock

from tests._installer_process_support import (
    _detect_installed_extras,
    _run_quiet,
    _run_with_progress_testable,
)


class TestRunQuiet:
    """Tests for _run_quiet subprocess wrapper."""

    def test_successful_command_returns_true(self) -> None:
        assert _run_quiet([sys.executable, "-c", "pass"]) is True

    def test_failing_command_returns_false(self) -> None:
        assert _run_quiet([sys.executable, "-c", "raise SystemExit(1)"]) is False

    def test_missing_executable_returns_false(self) -> None:
        assert _run_quiet(["/nonexistent/binary"]) is False

    def test_timeout_returns_false(self) -> None:
        """Command exceeding timeout returns False without hanging."""
        result = _run_quiet(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=1,
        )
        assert result is False

    def test_default_timeout_is_120(self) -> None:
        """Verify the default timeout parameter value."""
        sig = inspect.signature(_run_quiet)
        assert sig.parameters["timeout"].default == 120


class TestDetectInstalledExtras:
    """Tests for _detect_installed_extras with short timeout."""

    def test_returns_dict_with_ai_and_sqlite_vec_keys(self) -> None:
        result = _detect_installed_extras(sys.executable)
        assert "ai" in result
        assert "sqlite_vec" in result
        assert all(isinstance(v, bool) for v in result.values())

    def test_uses_short_timeout(self) -> None:
        """Import checks use 10s timeout, not the default 120s."""
        start = time.monotonic()
        result = _detect_installed_extras("/nonexistent/python3", timeout=1)
        elapsed = time.monotonic() - start
        assert elapsed < 5
        assert result["ai"] is False
        assert result["sqlite_vec"] is False


class TestRunWithProgress:
    """Tests for run_with_progress watchdog timeout."""

    def test_successful_command(self) -> None:
        """Normal command completes and returns True."""
        ui = MagicMock()
        ui.interactive = False
        ui.quiet = True
        result = _run_with_progress_testable(
            ui,
            "Testing...",
            [sys.executable, "-c", "print('hello')"],
        )
        assert result is True

    def test_watchdog_kills_hanging_process(self) -> None:
        """Process exceeding timeout is killed by watchdog."""
        ui = MagicMock()
        ui.interactive = False
        ui.quiet = True
        result = _run_with_progress_testable(
            ui,
            "Testing...",
            [sys.executable, "-c", "import time; time.sleep(300)"],
            timeout=2,
        )
        assert result is False
        ui.step_warn.assert_called_once()
        warn_msg = ui.step_warn.call_args[0][0]
        assert "timed out" in warn_msg
        assert "2s" in warn_msg

    def test_missing_command_returns_false(self) -> None:
        """FileNotFoundError returns False immediately."""
        ui = MagicMock()
        ui.interactive = False
        ui.quiet = True
        result = _run_with_progress_testable(
            ui,
            "Testing...",
            ["/nonexistent/command"],
        )
        assert result is False
