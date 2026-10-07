"""The retired-key warning names ``trw-mcp config unset <key> --scope ...`` and prints once (per process, per install run)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _warnings(capsys: pytest.CaptureFixture[str]) -> int:
    err = capsys.readouterr().err
    return sum(line.startswith("TRW: WARNING") and "user_tier_enabled" in line for line in err.splitlines())


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    for key in tuple(os.environ):
        if key.startswith("TRW_") and key != "TRW_FRAMEWORK_PATH":
            monkeypatch.delenv(key)
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".trw").mkdir(parents=True)
    (project / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    return home, project


def test_the_warning_for_a_retired_key_names_the_exact_unset_command_once(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.models.config._loader import _build_config_unguarded
    from trw_mcp.models.config._retired_keys import _reset_warned_keys

    home, project = env
    (home / ".trw" / "config.yaml").write_text("user_tier_enabled: true\n", encoding="utf-8")
    _reset_warned_keys()
    for _ in range(3):  # config is rebuilt on reload: still one warning
        _build_config_unguarded(project / ".trw" / "config.yaml")
    err = capsys.readouterr().err
    assert sum(line.startswith("TRW: WARNING") and "user_tier_enabled" in line for line in err.splitlines()) == 1
    assert "trw-mcp config unset user_tier_enabled --scope machine" in err


def test_a_project_layer_key_names_the_project_scope(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.models.config._loader import _build_config_unguarded
    from trw_mcp.models.config._retired_keys import _reset_warned_keys

    _home, project = env
    (project / ".trw" / "config.yaml").write_text("user_tier_enabled: true\n", encoding="utf-8")
    _reset_warned_keys()
    _build_config_unguarded(project / ".trw" / "config.yaml")
    assert "trw-mcp config unset user_tier_enabled --scope project" in capsys.readouterr().err


def test_the_warning_is_skipped_when_retired_key_warning_is_off_and_printed_when_unset(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The installer shows the warning once from captured output and passes ``off`` to uncaptured steps."""
    from trw_mcp.models.config._loader import _build_config_unguarded
    from trw_mcp.models.config._retired_keys import _reset_warned_keys

    home, project = env
    (home / ".trw" / "config.yaml").write_text("user_tier_enabled: true\n", encoding="utf-8")
    monkeypatch.delenv("TRW_RETIRED_KEY_WARNING", raising=False)
    _reset_warned_keys()
    _build_config_unguarded(project / ".trw" / "config.yaml")
    assert _warnings(capsys) == 1  # unset: printed, once

    for value in ("off", "OFF", " off "):
        monkeypatch.setenv("TRW_RETIRED_KEY_WARNING", value)
        _reset_warned_keys()
        _build_config_unguarded(project / ".trw" / "config.yaml")
        assert _warnings(capsys) == 0, value

    monkeypatch.setenv("TRW_RETIRED_KEY_WARNING", "on")  # only "off" turns it off
    _reset_warned_keys()
    _build_config_unguarded(project / ".trw" / "config.yaml")
    assert _warnings(capsys) == 1


def test_a_retired_env_var_warning_is_skipped_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.models.config._retired_keys import _reset_warned_keys, warn_retired_env_vars

    monkeypatch.setenv("TRW_RETIRED_KEY_WARNING", "off")
    _reset_warned_keys()
    assert warn_retired_env_vars({"TRW_USER_TIER_ENABLED": "1"}) == [
        "TRW_USER_TIER_ENABLED"
    ]  # still reported to callers
    assert capsys.readouterr().err == ""
