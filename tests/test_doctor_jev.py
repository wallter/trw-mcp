"""The ``jev`` doctor row: one place, under every client, to see whether trw_assess is usable."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.server._doctor_jev import jev_row

_KEY = "sk-or-doctor-test-00000000000000000"


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("TRW_JEV_ENABLED", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return home


def _machine_switch(home: Path) -> None:
    (home / ".trw").mkdir()
    (home / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")


def test_off_is_a_skip_that_names_the_switch(tmp_path: Path) -> None:
    status, message = jev_row(tmp_path, TRWConfig())
    assert status == "SKIP" and "~/.trw/config.yaml" in message


def test_shown_without_backend_or_key_warns_with_each_gap(tmp_path: Path) -> None:
    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=True))
    assert status == "WARN"
    assert "backend off" in message and "no OPENROUTER_API_KEY" in message


def test_machine_switch_and_dotenv_key_pass_and_never_print_the_key(tmp_path: Path, _clean: Path) -> None:
    _machine_switch(_clean)
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={_KEY}\n", encoding="utf-8")

    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=True))

    assert status == "PASS"
    assert "~/.trw/config.yaml" in message and "project .env" in message
    assert _KEY not in message


def test_explicit_env_off_beats_the_machine_switch(
    tmp_path: Path, _clean: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _machine_switch(_clean)
    monkeypatch.setenv("TRW_JEV_ENABLED", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)

    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=True))

    assert status == "WARN" and "backend off" in message


def test_backend_on_but_tool_hidden_in_this_project_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_JEV_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)

    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=False))

    assert status == "WARN" and "tool hidden" in message


def test_a_project_switch_off_is_named_as_the_reason(
    tmp_path: Path, _clean: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _machine_switch(_clean)
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    (tmp_path / ".env").write_text("TRW_JEV_ENABLED=false\n", encoding="utf-8")

    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=True))

    assert status == "WARN" and "backend off: switched off by project .env" in message


def test_project_dotenv_now_enables_the_backend(tmp_path: Path, _clean: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-09-23 operator decision: relaxes the prior rule that a project could only disable."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    (tmp_path / ".env").write_text("TRW_JEV_ENABLED=true\n", encoding="utf-8")

    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=True))

    assert status == "PASS" and "backend enabled by project .env" in message


def test_project_config_yaml_now_enables_the_backend(
    tmp_path: Path, _clean: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")

    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=True))

    assert status == "PASS" and "backend enabled by project .trw/config.yaml" in message


def test_explicit_env_off_beats_a_project_that_enables_itself(
    tmp_path: Path, _clean: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_JEV_ENABLED", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    (tmp_path / ".env").write_text("TRW_JEV_ENABLED=true\n", encoding="utf-8")

    status, message = jev_row(tmp_path, TRWConfig(assess_enabled=True))

    assert status == "WARN" and "backend off: switched off by TRW_JEV_ENABLED" in message
