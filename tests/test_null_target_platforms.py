"""A null ``target_platforms`` in .trw/config.yaml reads as an empty list.

Emptying the list by hand leaves ``target_platforms:`` null; a bare
``update-project`` used to die with ``TypeError: 'NoneType' object is not iterable``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests._ide_detection_isolation import isolate_ide_detection
from trw_mcp.bootstrap._ide_targets_finalize import (
    _remove_config_target_platform,
    _update_config_target_platforms,
)
from trw_mcp.bootstrap._init_project import init_project
from trw_mcp.bootstrap._update_project import update_project

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


@pytest.fixture(autouse=True)
def _isolate_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_ide_detection(monkeypatch)


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _null_out(cfg: Path, *, drop_key: bool) -> None:
    data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    if drop_key:
        data.pop("target_platforms", None)
        cfg.write_text(yaml.safe_dump(data, sort_keys=False) + "target_platforms:\n", encoding="utf-8")
    else:
        data["target_platforms"] = None
        cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["target_platforms"] is None


@pytest.mark.integration
@pytest.mark.parametrize("bare_key", [True, False], ids=["bare-key", "explicit-null"])
def test_bare_update_project_survives_null_target_platforms(fake_git_repo: Path, bare_key: bool) -> None:
    init_project(fake_git_repo, ide="opencode")
    cfg = fake_git_repo / ".trw" / "config.yaml"
    _null_out(cfg, drop_key=bare_key)

    result = update_project(fake_git_repo)

    assert not any("NoneType" in w for w in result.get("warnings", []))
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["target_platforms"] is not None


@pytest.mark.unit
def test_update_config_treats_null_as_empty_and_records_write_targets(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir()
    cfg.write_text("target_platforms:\n", encoding="utf-8")
    result: dict[str, list[str]] = {}

    _update_config_target_platforms(tmp_path, ["opencode"], result)

    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["target_platforms"] == ["opencode"]


@pytest.mark.unit
def test_remove_platform_from_null_is_a_noop(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir()
    cfg.write_text("target_platforms:\n", encoding="utf-8")

    _remove_config_target_platform(tmp_path, "opencode", {})

    assert cfg.read_text(encoding="utf-8") == "target_platforms:\n"


@pytest.mark.unit
def test_removing_last_client_writes_empty_list_not_null(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir()
    cfg.write_text("target_platforms:\n- opencode\n", encoding="utf-8")

    _remove_config_target_platform(tmp_path, "opencode", {})

    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["target_platforms"] == []
