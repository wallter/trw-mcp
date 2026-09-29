"""How an install's context-local config meets FR04's live refresh and CLI override (PRD-CORE-305-FR04/FR07).

* The serve path's CLI override applies to a config an install builds, so a
  serving process keeps its flags across an in-process install.
* ``refresh_config_if_changed()`` inside an install leaves the process singleton
  alone: there "the project" is the install's target, so the freshness key would
  read as changed and rebuild the SERVER's config from the target's files.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _loader, get_config, reload_config
from trw_mcp.state._project_root_binding import installing_into


@pytest.fixture
def two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    server, target = tmp_path / "server", tmp_path / "target"
    for project, assess in ((server, "false"), (target, "true")):
        (project / ".trw").mkdir(parents=True)
        (project / ".trw" / "config.yaml").write_text(f"assess_enabled: {assess}\n", encoding="utf-8")
    for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(server))
    reload_config()
    yield server.resolve(), target.resolve()
    _loader.set_config_override(None)
    reload_config()


def test_the_cli_override_applies_to_an_install_built_config(two_projects: tuple[Path, Path]) -> None:
    _server, target = two_projects

    def _cli_flags(config: TRWConfig) -> TRWConfig:
        return config.model_copy(update={"debug": True})

    _loader.set_config_override(_cli_flags)
    with installing_into(target):
        inside = get_config()

    assert inside.assess_enabled is True, "the install must read the target's config"
    assert inside.debug is True, "the serving process's CLI override was dropped for the install's config"


def test_refresh_inside_an_install_leaves_the_server_config_alone(two_projects: tuple[Path, Path]) -> None:
    _server, target = two_projects
    served = get_config()  # a tracked, file-backed build of the server's project
    assert served.assess_enabled is False
    assert _loader._built_from is not None, "precondition: the singleton is tracked, so refresh would act on it"

    with installing_into(target):
        rebuilt = _loader.refresh_config_if_changed()

    assert rebuilt is False
    assert _loader._singleton is served, "an install's refresh replaced the process-wide config"
    assert get_config().assess_enabled is False
