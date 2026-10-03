"""CLI-DEBUG-NO-LOG-SINK: the debug switches reach a CLI subcommand's logging.

``trw-mcp local recall`` forced a plain-output default level (``CRITICAL``/``WARNING``) as an EXPLICIT level, which beats
``TRW_LOG_LEVEL`` and ``debug: true`` / ``TRW_DEBUG``, so a field failure needed a code change to see its exception (the O6
host-B pull that failed with ``last_error: "pull failed"``). The default now applies only when nothing asked for logs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Run the real ``main()`` for ``trw-mcp local recall`` with only the logging call and the handler captured."""
    from trw_mcp.models.config import _reset_config as reset_config
    from trw_mcp.server import _cli

    seen: dict[str, Any] = {}
    monkeypatch.chdir(tmp_path)
    for name in ("TRW_LOG_LEVEL", "LOG_LEVEL", "TRW_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_cli, "configure_logging", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(_cli, "enforce_state_changing_guard", lambda *_a, **_k: None)
    monkeypatch.setitem(_cli.SUBCOMMAND_HANDLERS, "local", lambda _args: None)
    monkeypatch.setattr("sys.argv", ["trw-mcp", "local", "recall", "--query", "x"])
    reset_config()
    yield seen
    reset_config()


def _run() -> None:
    from trw_mcp.server import _cli

    _cli.main()


def test_a_plain_cli_command_keeps_its_quiet_default(captured: dict[str, Any]) -> None:
    _run()

    assert captured["log_level"] == "CRITICAL"  # `local` prints its own failures


def test_an_env_log_level_is_not_overridden_by_the_plain_default(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_LOG_LEVEL", "DEBUG")

    _run()

    assert captured["log_level"] is None  # configure_logging resolves TRW_LOG_LEVEL itself


def test_trw_debug_in_the_environment_turns_cli_logging_on(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import _reset_config as reset_config

    monkeypatch.setenv("TRW_DEBUG", "true")
    reset_config()

    _run()

    assert captured["log_level"] is None


def test_debug_true_in_the_project_config_turns_cli_logging_on(captured: dict[str, Any], tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("debug: true\n", encoding="utf-8")

    _run()

    assert captured["log_level"] is None


def test_an_explicit_log_level_flag_still_wins(captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["trw-mcp", "--log-level", "ERROR", "local", "recall", "--query", "x"])

    _run()

    assert captured["log_level"] == "ERROR"
