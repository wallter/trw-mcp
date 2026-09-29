"""trw_assess is on the surface whenever any layer enables or configures it (operator directive, 2026-09-26).

The surface read only the ``assess_enabled`` field, while the backend's own cascade also takes
``TRW_JEV_ENABLED`` from the process env or the project ``.env``: a project that switched the judge on
that way got a working backend behind a hidden tool. Every visibility reader now asks
``assess_surfaced``: the surface resolver, the tool's own gate, the doctor row
and the optional skill. The reviewer posture stays bounded to ``REVIEWER_TOOLS`` by design (a reviewer
lane must never gain the judge's egress path).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.bootstrap._optional_skills import skill_enabled
from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware
from trw_mcp.models.config import _reset_config, get_config
from trw_mcp.models.surface_packs import REVIEWER_TOOLS
from trw_mcp.profile.explain import tool_surface_summary
from trw_mcp.server._always_load import always_load_names
from trw_mcp.server._doctor_jev import jev_row
from trw_mcp.tools._assess_enablement import assess_surfaced

#: Where assess gets switched on -> how to switch it on there. ``off`` sets nothing.
_SOURCES = ("off", "project config", "machine config", "TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED", "project .env")


def _enable(source: str, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw, home_trw = project / ".trw", Path.home() / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    home_trw.mkdir(parents=True, exist_ok=True)
    project_yaml = "tool_resolution_mode: {mode}\n"
    if source == "project config":
        project_yaml += "assess_enabled: true\n"
    elif source == "machine config":
        (home_trw / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    elif source == "TRW_ASSESS_ENABLED":
        monkeypatch.setenv("TRW_ASSESS_ENABLED", "true")
    elif source == "TRW_JEV_ENABLED":
        monkeypatch.setenv("TRW_JEV_ENABLED", "1")
    elif source == "project .env":
        (project / ".env").write_text("TRW_JEV_ENABLED=1\n", encoding="utf-8")
    (trw / "config.yaml").write_text(project_yaml, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED", "TRW_SURFACE_ROLE", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    _reset_config()


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["standard", "all"])
@pytest.mark.parametrize("source", _SOURCES)
def test_every_visibility_reader_shows_assess_whenever_a_layer_enables_it(
    project: Path, monkeypatch: pytest.MonkeyPatch, mode: str, source: str
) -> None:
    _enable(source, project, monkeypatch)
    config_yaml = project / ".trw" / "config.yaml"
    config_yaml.write_text(config_yaml.read_text(encoding="utf-8").format(mode=mode), encoding="utf-8")
    _reset_config()
    config = get_config()
    # ``all`` turns the assess pack on by itself (surface_packs.enabled_packs); the flag decides the rest.
    expected = source != "off"
    on_surface = expected or mode == "all"

    assert assess_surfaced(config) is expected
    assert ("trw_assess" in SurfaceAuthorityMiddleware()._resolve().tools) is on_surface
    assert ("trw_assess" in tool_surface_summary(config)["tools"]) is on_surface
    # PRD-CORE-305-FR04: the floor marks trw_assess whatever the flag; the surface mask hides it when off.
    assert "trw_assess" in always_load_names()
    assert skill_enabled("trw-assess") is expected
    status, message = jev_row(project, config)
    # Never "hidden while enabled": a backend switched on always has its tool shown.
    assert "tool hidden" not in message
    assert status == ("SKIP" if source == "off" else "WARN")  # WARN: no OPENROUTER_API_KEY here


@pytest.mark.unit
@pytest.mark.parametrize("source", _SOURCES)
def test_the_reviewer_posture_never_gains_assess(project: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    _enable(source, project, monkeypatch)
    config_yaml = project / ".trw" / "config.yaml"
    config_yaml.write_text(config_yaml.read_text(encoding="utf-8").format(mode="all"), encoding="utf-8")
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    _reset_config()

    assert SurfaceAuthorityMiddleware()._resolve().tools == REVIEWER_TOOLS
    assert "trw_assess" not in tool_surface_summary(get_config())["tools"]


@pytest.mark.unit
def test_an_explicit_project_off_beats_a_machine_on(project: Path) -> None:
    """Enabled or configured means the effective answer: a project that switched assess off stays off."""
    (Path.home() / ".trw").mkdir(parents=True, exist_ok=True)
    (Path.home() / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    (project / ".trw").mkdir(parents=True, exist_ok=True)
    (project / ".trw" / "config.yaml").write_text("assess_enabled: false\n", encoding="utf-8")
    _reset_config()

    assert assess_surfaced(get_config()) is False
    assert "trw_assess" not in SurfaceAuthorityMiddleware()._resolve().tools


#: Where each client's installer puts the optional skill, and whether the managed-artifact manifest
#: records it (the claude/codex/opencode skill surfaces have their own recorders, keyed differently).
_SKILL_DESTS = (".claude/skills", ".agents/skills", ".github/skills", ".cursor/skills", ".opencode/skills")
_MANIFESTED = (".github/skills/trw-assess/SKILL.md", ".cursor/skills/trw-assess/SKILL.md")


@pytest.mark.integration
@pytest.mark.parametrize(("caller", "target", "installed"), [(False, True, True), (True, False, False)])
def test_init_from_another_directory_installs_and_records_by_the_targets_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: bool, target: bool, installed: bool
) -> None:
    """B71-117 (sol rounds 1-3 on B71-112): ``trw-mcp init <target>`` run from elsewhere decides the
    optional skill by the TARGET's switch, and the managed-artifact manifest agrees with what landed, so
    a later update never reads an untouched copy as user-edited."""
    from ruamel.yaml import YAML

    from trw_mcp.bootstrap._init_project import init_project

    for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED", "TRW_PROJECT_ROOT"):
        monkeypatch.delenv(var, raising=False)
    elsewhere, project = tmp_path / "elsewhere", tmp_path / "target"
    (elsewhere / ".trw").mkdir(parents=True)
    (elsewhere / ".trw" / "config.yaml").write_text(f"assess_enabled: {str(caller).lower()}\n", encoding="utf-8")
    project.mkdir()
    (project / ".env").write_text(f"TRW_JEV_ENABLED={int(target)}\n", encoding="utf-8")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(elsewhere))  # the caller's own project
    monkeypatch.chdir(elsewhere)
    _reset_config()

    result = init_project(project, ide="all")

    assert result["errors"] == []
    for dest in _SKILL_DESTS:
        assert (project / dest / "trw-assess").is_dir() is installed, dest
    hashes = YAML(typ="safe").load((project / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))
    for key in _MANIFESTED:
        assert (key in hashes["content_hashes"]) is installed, key
    # The target is named in the install's own context, never in os.environ or the cached config.
    assert os.environ["TRW_PROJECT_ROOT"] == str(elsewhere)
    assert get_config().assess_enabled is caller


@pytest.mark.integration
def test_each_skill_installer_called_directly_reads_the_target_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sol P2 on B71-117: an installer called outside init/update still decides by its own target."""
    from trw_mcp.bootstrap._codex import install_codex_skills
    from trw_mcp.bootstrap._copilot import install_copilot_skills
    from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills
    from trw_mcp.bootstrap._init_project_skills import _install_skills
    from trw_mcp.bootstrap._opencode import install_opencode_skills

    for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED", "TRW_PROJECT_ROOT"):
        monkeypatch.delenv(var, raising=False)
    elsewhere, target = tmp_path / "elsewhere", tmp_path / "target"
    elsewhere.mkdir()
    target.mkdir()
    (target / ".env").write_text("TRW_JEV_ENABLED=1\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    _reset_config()

    _install_skills(target, False, {"created": [], "updated": [], "preserved": [], "skipped": [], "errors": []})
    install_codex_skills(target)
    install_copilot_skills(target)
    generate_cursor_ide_skills(target)
    install_opencode_skills(target)

    for dest in _SKILL_DESTS:
        assert (target / dest / "trw-assess").is_dir(), dest


@pytest.mark.unit
def test_the_installers_skill_decision_follows_the_bound_project_not_the_cached_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cached config says on; the project being installed into says off: the project wins."""
    from trw_mcp.models.config import TRWConfig

    for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("assess_enabled: false\n", encoding="utf-8")
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: TRWConfig(assess_enabled=True))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    assert skill_enabled("trw-assess") is False
