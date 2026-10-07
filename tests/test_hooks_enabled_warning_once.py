"""``hooks_enabled: false`` is warned about once per run, with the exact command that fixes it (feedback #142, #161)."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from trw_mcp.bootstrap._file_ops import write_hook_env_for_clients

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("no_memory_daemon")]

_CLIENTS = ["claude-code", "copilot", "cursor-ide"]


def _project(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text("hooks_enabled: false\n", encoding="utf-8")
    return trw_dir


def test_three_clients_yield_one_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_HOOKS_ENABLED", raising=False)
    warnings: list[str] = []
    write_hook_env_for_clients(_project(tmp_path), _CLIENTS, warnings=warnings)
    assert len([w for w in warnings if "hooks_enabled" in w]) == 1


def test_the_project_layer_warning_names_an_exact_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_HOOKS_ENABLED", raising=False)
    trw_dir = _project(tmp_path)
    warnings: list[str] = []
    write_hook_env_for_clients(trw_dir, _CLIENTS, warnings=warnings)
    (message,) = [w for w in warnings if "hooks_enabled" in w]
    command = message.split("Fix: ", 1)[1]
    assert shlex.split(command)[-1] == str(trw_dir / "config.yaml")
    assert "hooks_enabled: true" in command


def test_the_env_layer_warning_names_the_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_HOOKS_ENABLED", "false")
    warnings: list[str] = []
    write_hook_env_for_clients(tmp_path / ".trw", ["claude-code"], warnings=warnings)
    (message,) = [w for w in warnings if "hooks_enabled" in w]
    assert "unset TRW_HOOKS_ENABLED" in message


def test_the_single_warning_names_every_affected_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Naming the clients is information the operator needs: one message, all names, no duplicates."""
    monkeypatch.delenv("TRW_HOOKS_ENABLED", raising=False)
    warnings: list[str] = []
    write_hook_env_for_clients(_project(tmp_path), ["claude-code", "copilot", "claude-code"], warnings=warnings)

    (message,) = [w for w in warnings if "hooks_enabled" in w]
    assert "Claude Code" in message and "GitHub Copilot CLI" in message
    assert message.count("Claude Code") == 1
    assert "Fix: " in message
