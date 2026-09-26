"""F5 follow-up (P1, 2026-09-25 sol re-review): local_only fails closed in trw-mcp too.

trw-memory's F5 fix only covers MemoryConfig; a trw-mcp-only process (e.g. a
CLI subcommand) never constructs MemoryConfig, so it never hit that gate.
This suite covers the three surfaces where trw-mcp could otherwise silently
drop a leftover local_only: a direct TRWConfig(...) call, the YAML cascade
(project + machine config.yaml), and the environment.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.exceptions import ConfigError
from trw_mcp.models.config import TRWConfig, _loader, reload_config


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    (tmp_path / "proj" / ".trw").mkdir(parents=True)
    return tmp_path / "proj"


@pytest.fixture(autouse=True)
def _isolate(project_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project_dir)
    monkeypatch.delenv("TRW_CONFIG_STRICT", raising=False)
    monkeypatch.delenv("TRW_LOCAL_ONLY", raising=False)
    reload_config()
    yield
    reload_config()


@pytest.mark.parametrize("key", ["local_only", "memory_local_only"])
def test_direct_constructor_refuses(key: str) -> None:
    with pytest.raises(ConfigError, match="local_only"):
        TRWConfig(**{key: True})


@pytest.mark.parametrize("key", ["local_only", "memory_local_only"])
def test_yaml_cascade_refuses_and_does_not_fall_back_to_defaults(project_dir: Path, key: str) -> None:
    (project_dir / ".trw" / "config.yaml").write_text(f"{key}: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="local_only"):
        _loader._build_config_unguarded()


def test_yaml_cascade_refuses_even_under_strict_mode_default(project_dir: Path) -> None:
    """The refusal is unconditional -- it does not depend on TRW_CONFIG_STRICT."""
    (project_dir / ".trw" / "config.yaml").write_text("local_only: false\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="local_only"):
        _loader._build_config_unguarded()


def test_environment_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_LOCAL_ONLY", "true")
    with pytest.raises(ConfigError, match="local_only"):
        _loader._build_config_unguarded()


def test_refusal_names_the_key_and_gives_the_remedy() -> None:
    with pytest.raises(ConfigError) as exc_info:
        TRWConfig(local_only=True)
    message = str(exc_info.value)
    assert "local_only" in message
    assert "sync_enabled: false" in message


def test_absence_of_local_only_still_constructs(project_dir: Path) -> None:
    """Negative case: no local_only anywhere -- construction is unaffected."""
    cfg = _loader._build_config_unguarded()
    assert cfg is not None
    assert "local_only" not in TRWConfig.model_fields


def test_get_config_propagates_the_refusal_rather_than_falling_back(project_dir: Path) -> None:
    """get_config() -- the real production entrypoint -- must also refuse, not silently default."""
    (project_dir / ".trw" / "config.yaml").write_text("local_only: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="local_only"):
        _loader.get_config()
