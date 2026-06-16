"""PRD-CORE-185 FR04: machine-layer config cascade.

Precedence (highest wins): ``TRW_*`` env > project ``.trw/config.yaml`` >
machine ``~/.trw/config.yaml`` > built-in code defaults.

Backward compatibility (NFR02): with no ``~/.trw/config.yaml`` present, the
effective config is identical to today's project-only loader behavior.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.models.config import get_config, reload_config


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """A minimal project root with a ``.trw`` dir."""
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    (tmp_path / "proj" / ".trw").mkdir(parents=True)
    return tmp_path / "proj"


@pytest.fixture()
def home_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home directory with a ``.trw`` dir for the machine config layer."""
    home = tmp_path / "home"
    (home / ".trw").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(autouse=True)
def _point_project(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project_dir)
    monkeypatch.delenv("TRW_TASK_ROOT", raising=False)
    reload_config()
    yield
    reload_config()


def test_machine_layer_value_present(home_dir: Path) -> None:
    """A value set only in ``~/.trw/config.yaml`` reaches the effective config."""
    (home_dir / ".trw" / "config.yaml").write_text("task_root: from-machine\n", encoding="utf-8")
    assert get_config().task_root == "from-machine"


def test_project_overrides_machine(home_dir: Path, project_dir: Path) -> None:
    """The project file overrides the machine file per key (deep merge)."""
    (home_dir / ".trw" / "config.yaml").write_text("task_root: from-machine\n", encoding="utf-8")
    (project_dir / ".trw" / "config.yaml").write_text("task_root: from-project\n", encoding="utf-8")
    assert get_config().task_root == "from-project"


def test_env_overrides_both(home_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``TRW_*`` env overrides both the project and machine layers."""
    (home_dir / ".trw" / "config.yaml").write_text("task_root: from-machine\n", encoding="utf-8")
    (project_dir / ".trw" / "config.yaml").write_text("task_root: from-project\n", encoding="utf-8")
    monkeypatch.setenv("TRW_TASK_ROOT", "from-env")
    assert get_config().task_root == "from-env"


def test_machine_only_key_merges_with_project_only_key(home_dir: Path, project_dir: Path) -> None:
    """Keys unique to each layer both survive the deep merge."""
    (home_dir / ".trw" / "config.yaml").write_text("recall_user_tier_cap: 7\n", encoding="utf-8")
    (project_dir / ".trw" / "config.yaml").write_text("task_root: from-project\n", encoding="utf-8")
    cfg = get_config()
    assert cfg.recall_user_tier_cap == 7
    assert cfg.task_root == "from-project"


def test_no_machine_file_is_backward_compatible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With NO ``~/.trw/config.yaml`` present, behavior is today's loader.

    Home is pointed at an empty directory so the machine layer is absent; the
    project file (also absent here) does not apply either, leaving code defaults.
    """
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    reload_config()
    cfg = get_config()
    # Defaults preserved — user_tier fields carry their code defaults.
    assert cfg.user_tier_enabled is False
    assert cfg.recall_user_tier_cap == 5
    assert cfg.task_root == "docs"


def test_populated_project_no_machine_matches_project_values(
    tmp_path: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Populated project file + NO machine file == project-only loader result.

    This is the load-bearing NFR02 case the prior test under-proved: a NON-empty
    project ``.trw/config.yaml`` with the machine layer absent must yield exactly
    the project file's overrides (atop code defaults), i.e. the cascade adds
    nothing and removes nothing versus the historical project-only loader.
    """
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    (project_dir / ".trw" / "config.yaml").write_text(
        "task_root: from-project\nuser_tier_enabled: true\nrecall_user_tier_cap: 9\n",
        encoding="utf-8",
    )
    reload_config()
    cfg = get_config()
    # Every project-file key is reflected verbatim (no machine layer to dilute).
    assert cfg.task_root == "from-project"
    assert cfg.user_tier_enabled is True
    assert cfg.recall_user_tier_cap == 9


def test_user_tier_fields_exist_with_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04 introduces ``user_tier_enabled`` + ``recall_user_tier_cap``.

    Isolated so neither the machine layer (``~/.trw/config.yaml``) nor an
    ambient ``TRW_USER_TIER_ENABLED`` env can mask the code defaults: home is
    pointed at an empty dir and the user-tier env knobs are cleared before
    ``reload_config()`` (the autouse ``_point_project`` already pins a clean,
    config-less project root).
    """
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    monkeypatch.delenv("TRW_USER_TIER_ENABLED", raising=False)
    monkeypatch.delenv("TRW_RECALL_USER_TIER_CAP", raising=False)
    reload_config()
    cfg = get_config()
    assert hasattr(cfg, "user_tier_enabled")
    assert hasattr(cfg, "recall_user_tier_cap")
    assert cfg.user_tier_enabled is False
    assert cfg.recall_user_tier_cap == 5
