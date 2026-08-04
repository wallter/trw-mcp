"""Tests for trw_mcp._logging — structured logging configuration.

Verifies: verbosity levels, env var overrides, secret redaction,
component extraction, JSON/console output modes.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog

from trw_mcp._logging import (
    _add_component,
    _config_debug_requested,
    _redact_secrets,
    _resolve_log_level,
    _verbosity_to_level,
    configure_logging,
)


@pytest.mark.unit
class TestVerbosityToLevel:
    def test_negative_returns_warning(self) -> None:
        assert _verbosity_to_level(-1) == logging.WARNING

    def test_zero_returns_info(self) -> None:
        assert _verbosity_to_level(0) == logging.INFO

    def test_one_returns_debug(self) -> None:
        assert _verbosity_to_level(1) == logging.DEBUG

    def test_two_returns_debug(self) -> None:
        assert _verbosity_to_level(2) == logging.DEBUG


@pytest.mark.unit
class TestResolveLogLevel:
    def test_explicit_level_takes_precedence(self) -> None:
        assert _resolve_log_level(explicit_level="ERROR", debug=True, verbosity=2) == logging.ERROR

    def test_env_var_trw_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_LOG_LEVEL", "WARNING")
        assert _resolve_log_level() == logging.WARNING

    def test_env_var_log_level_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        assert _resolve_log_level() == logging.ERROR

    def test_trw_log_level_beats_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        assert _resolve_log_level() == logging.DEBUG

    def test_debug_flag(self) -> None:
        assert _resolve_log_level(debug=True) == logging.DEBUG

    def test_verbosity(self) -> None:
        assert _resolve_log_level(verbosity=1) == logging.DEBUG

    def test_default_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert _resolve_log_level() == logging.INFO


@pytest.mark.unit
class TestRedactSecrets:
    def _call(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        return _redact_secrets(MagicMock(), "info", event_dict)

    def test_redacts_password_key(self) -> None:
        result = self._call({"password": "s3cr3t", "user": "alice"})
        assert result["password"] == "***REDACTED***"
        assert result["user"] == "alice"

    def test_redacts_api_key(self) -> None:
        result = self._call({"api_key": "key-123"})
        assert result["api_key"] == "***REDACTED***"

    def test_redacts_authorization_header(self) -> None:
        result = self._call({"header": "Bearer abc123xyz"})
        assert "abc123xyz" not in result["header"]
        assert "***REDACTED***" in result["header"]

    @pytest.mark.parametrize("key", ["client_secret", "refresh_token", "jwt", "id_token"])
    def test_redacts_oauth_oidc_keys(self, key: str) -> None:
        result = self._call({key: "tok-abc"})
        assert result[key] == "***REDACTED***"

    def test_preserves_non_sensitive_values(self) -> None:
        result = self._call({"user": "alice", "action": "login"})
        assert result == {"user": "alice", "action": "login"}

    def test_case_insensitive_key_match(self) -> None:
        result = self._call({"API_KEY": "key-123"})
        assert result["API_KEY"] == "***REDACTED***"


@pytest.mark.unit
class TestAddComponent:
    def _call(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        return _add_component(MagicMock(), "info", event_dict)

    def test_extracts_component_from_trw_mcp_module(self) -> None:
        result = self._call({"_logger_name": "trw_mcp.tools.learning"})
        assert result["component"] == "tools.learning"

    def test_extracts_component_from_trw_memory_module(self) -> None:
        result = self._call({"_logger_name": "trw_memory.storage.sqlite_backend"})
        assert result["component"] == "storage.sqlite_backend"

    def test_preserves_existing_component(self) -> None:
        result = self._call({"_logger_name": "trw_mcp.tools.learning", "component": "custom"})
        assert result["component"] == "custom"

    def test_uses_full_name_for_unknown_package(self) -> None:
        result = self._call({"_logger_name": "external.module"})
        assert result["component"] == "external.module"

    def test_no_logger_name_skips(self) -> None:
        result = self._call({"message": "test"})
        assert "component" not in result


@pytest.mark.unit
class TestConfigureLogging:
    def test_version_bind_failure_logs_debug(self) -> None:
        mock_logger = MagicMock()
        with (
            patch("importlib.metadata.version", side_effect=RuntimeError("boom")),
            patch("structlog.get_logger", return_value=mock_logger),
        ):
            configure_logging(json_output=True)

        mock_logger.debug.assert_called_once_with(
            "logging_service_version_bind_failed",
            exc_info=True,
        )

    def test_json_output_forced(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        configure_logging(json_output=True)
        log = structlog.get_logger("test")
        # Should not raise
        log.info("test_event", key="value")

    def test_console_output_forced(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        configure_logging(json_output=False)
        log = structlog.get_logger("test")
        log.info("test_event", key="value")

    def test_file_logging(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        log_dir = tmp_path / "logs"
        configure_logging(log_dir=log_dir)
        assert log_dir.is_dir()
        # At least one .jsonl file created
        assert any(f.suffix == ".jsonl" for f in log_dir.iterdir())

    def test_explicit_log_file(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        log_file = tmp_path / "test.log"
        configure_logging(log_file=log_file)
        assert log_file.exists()


@pytest.mark.unit
class TestConfigDebugPrecedence:
    """``.trw/config.yaml::debug`` is THE portable verbosity toggle.

    No client bootstrap profile bakes ``--debug`` into its generated MCP server
    entry, so this key is the only thing that turns DEBUG on identically for
    every client. These tests pin the precedence contract.
    """

    def test_config_debug_enables_debug_level(self) -> None:
        assert _resolve_log_level(config_debug=True) == logging.DEBUG

    def test_config_debug_off_keeps_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert _resolve_log_level(config_debug=False) == logging.INFO

    def test_explicit_level_beats_config_debug(self) -> None:
        assert _resolve_log_level(explicit_level="WARNING", config_debug=True) == logging.WARNING

    def test_env_beats_config_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_LOG_LEVEL", "ERROR")
        assert _resolve_log_level(config_debug=True) == logging.ERROR

    def test_quiet_flag_beats_config_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--quiet`` is an explicit caller signal; an on-disk default must not undo it."""
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert _resolve_log_level(verbosity=-1, config_debug=True) == logging.WARNING


@pytest.mark.unit
class TestConfigDebugRequested:
    """``_config_debug_requested`` gates the config read on precedence."""

    def _config(self, *, debug: bool) -> Any:
        cfg = MagicMock()
        cfg.debug = debug
        return cfg

    def test_reads_config_when_nothing_else_decides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        with patch("trw_mcp.models.config.get_config", return_value=self._config(debug=True)):
            assert _config_debug_requested(verbosity=0, debug=False, explicit_level=None) is True

    def test_returns_false_when_config_debug_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        with patch("trw_mcp.models.config.get_config", return_value=self._config(debug=False)):
            assert _config_debug_requested(verbosity=0, debug=False, explicit_level=None) is False

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            ({"verbosity": 0, "debug": False, "explicit_level": "CRITICAL"}, "explicit --log-level"),
            ({"verbosity": 0, "debug": True, "explicit_level": None}, "explicit --debug"),
            ({"verbosity": -1, "debug": False, "explicit_level": None}, "explicit --quiet"),
        ],
    )
    def test_skips_config_read_when_a_flag_decides(
        self,
        kwargs: dict[str, Any],
        reason: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Import safety: server/__init__ configures logging at import time.

        It passes an explicit level, so ``get_config`` must never be reached —
        a config load during package import is not safe.
        """
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        with patch("trw_mcp.models.config.get_config", side_effect=AssertionError("config read: " + reason)):
            assert _config_debug_requested(**kwargs) is False

    def test_env_level_skips_config_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_LOG_LEVEL", "ERROR")
        with patch("trw_mcp.models.config.get_config", side_effect=AssertionError("config read")):
            assert _config_debug_requested(verbosity=0, debug=False, explicit_level=None) is False

    def test_config_load_failure_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        with patch("trw_mcp.models.config.get_config", side_effect=RuntimeError("boom")):
            assert _config_debug_requested(verbosity=0, debug=False, explicit_level=None) is False


class TestConfigDebugEndToEnd:
    """Real path: ``.trw/config.yaml`` -> TRWConfig -> configure_logging -> file sink."""

    def _boot(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, *, debug_yaml: str) -> Any:
        from trw_mcp.models.config import reload_config

        monkeypatch.delenv("TRW_LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("TRW_DEBUG", raising=False)
        (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".trw" / "config.yaml").write_text(f"debug: {debug_yaml}\n", encoding="utf-8")
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda *a, **k: tmp_path)
        monkeypatch.chdir(tmp_path)
        reload_config()
        configure_logging(json_output=True, package_name="trw-mcp")
        structlog.get_logger("trw_mcp.probe").debug("config_debug_probe_event")
        for handler in logging.getLogger().handlers:
            handler.flush()
        return tmp_path / ".trw" / "logs"

    def test_debug_true_writes_debug_event_to_file_sink(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        log_dir = self._boot(tmp_path, monkeypatch, debug_yaml="true")
        assert log_dir.is_dir(), "config debug:true must open the .trw/logs file sink"
        written = "".join(f.read_text(encoding="utf-8") for f in log_dir.glob("*.jsonl"))
        assert "config_debug_probe_event" in written

    def test_debug_false_drops_debug_event_and_opens_no_sink(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_dir = self._boot(tmp_path, monkeypatch, debug_yaml="false")
        assert not log_dir.exists(), "config debug:false must not open a file sink"
