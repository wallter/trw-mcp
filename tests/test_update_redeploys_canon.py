"""Upgrading a project initialised by an older TRW redeploys the framework canon (M1 dry run, release blocker).

A 7.0.1 project had v27.4_TRW deployed, a ``framework_version: v27.4_TRW`` pin that 7.0.1's init wrote into
``.trw/config.yaml``, and (in any repository that never committed ``.trw/``) canon files git reports as
untracked. ``update-project`` redeployed the new canon and then put the old bytes back as "uncommitted user
changes", so ``trw-mcp doctor`` failed framework_integrity after every upgrade.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_OLD_FRAMEWORK, _OLD_AAREF = "v27.4_TRW", "v3.2.1"


def _older_canon() -> tuple[str, str]:
    from trw_mcp.bootstrap._utils import _DATA_DIR
    from trw_mcp.models.config import TRWConfig

    defaults = TRWConfig.model_fields
    framework = (_DATA_DIR / "framework.md").read_text(encoding="utf-8")
    aaref = (_DATA_DIR / "aaref.md").read_text(encoding="utf-8")
    framework = framework.replace(str(defaults["framework_version"].default), _OLD_FRAMEWORK)
    aaref = aaref.replace(str(defaults["aaref_version"].default).lstrip("v"), _OLD_AAREF.lstrip("v"))
    return framework, aaref


def _project_initialised_by_an_older_release(tmp_path: Path) -> Path:
    """What 7.0.1 left: the older canon deployed through the receipt path, its version pinned, nothing committed."""
    from trw_mcp.bootstrap import init_project
    from trw_mcp.canons.registry import bundled_manifest_bytes, load_registry
    from trw_mcp.framework_integrity import repair_framework_runtime

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    assert not init_project(project, ide="claude-code")["errors"]
    config = project / ".trw" / "config.yaml"
    text = "".join(line for line in config.read_text(encoding="utf-8").splitlines(True) if "_version:" not in line)
    config.write_text(text + f"framework_version: {_OLD_FRAMEWORK}\n", encoding="utf-8")
    framework, aaref = _older_canon()
    repair_framework_runtime(
        project,
        framework_source=framework,
        aaref_source=aaref,
        framework_version=_OLD_FRAMEWORK,
        aaref_version=_OLD_AAREF,
        registry_digest=load_registry(bundled_manifest_bytes()).digest,
    )
    (project / "FRAMEWORK.md").write_text(framework, encoding="utf-8")
    (project / "AARE-F-FRAMEWORK.md").write_text(aaref, encoding="utf-8")
    _record_install(project, _OLD_FRAMEWORK, _OLD_AAREF)
    return project


def _record_install(project: Path, framework_version: str, aaref_version: str) -> None:
    """installer-meta as the older init wrote it: the ownership evidence for the pin it put in config.yaml."""
    meta = project / ".trw" / "installer-meta.yaml"
    kept = [line for line in meta.read_text(encoding="utf-8").splitlines(True) if "_version" not in line]
    meta.write_text(
        "".join(kept)
        + f"framework_version_at_install: {framework_version}\naaref_version_at_install: {aaref_version}\n"
        + f"framework_version: {framework_version}\ninstalled_by: trw-mcp init-project\n",
        encoding="utf-8",
    )


def test_update_redeploys_the_canon_and_the_integrity_check_goes_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import update_project
    from trw_mcp.bootstrap._utils import _DATA_DIR
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.server._subcommands_doctor import _check_framework_integrity

    monkeypatch.setenv("MEMORY_DAEMON_AUTOSTART", "false")
    project = _project_initialised_by_an_older_release(tmp_path)
    monkeypatch.chdir(project)
    _reset_config()

    result = update_project(project)

    assert not result["errors"], result["errors"]
    bundled = (_DATA_DIR / "framework.md").read_bytes()
    assert (project / ".trw" / "frameworks" / "FRAMEWORK.md").read_bytes() == bundled
    assert (project / "FRAMEWORK.md").read_bytes() == bundled
    assert (project / ".trw" / "frameworks" / "AARE-F-FRAMEWORK.md").read_bytes() == (
        _DATA_DIR / "aaref.md"
    ).read_bytes()
    assert not [p for p in result.get("preserved", []) if "FRAMEWORK" in p], result.get("preserved")
    assert "framework_version:" not in (project / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert any("framework_version" in line for line in result.get("info", [])), "the retired pin is reported"
    _reset_config()
    check = _check_framework_integrity(project, get_config())
    assert check.status == "PASS", check.message


def test_a_hand_edited_canon_body_is_still_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The receipt proves TRW's own bytes; a body the user edited after the deploy still survives the update."""
    from trw_mcp.bootstrap import update_project
    from trw_mcp.models.config import _reset_config

    monkeypatch.setenv("MEMORY_DAEMON_AUTOSTART", "false")
    project = _project_initialised_by_an_older_release(tmp_path)
    edited = project / "FRAMEWORK.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n<!-- my local note -->\n", encoding="utf-8")
    monkeypatch.chdir(project)
    _reset_config()

    result = update_project(project)

    assert "<!-- my local note -->" in edited.read_text(encoding="utf-8")
    assert any(p.startswith("FRAMEWORK.md") for p in result.get("preserved", []))


@pytest.mark.parametrize("pin", ["v99.0_TRW", "my-fork-framework", "v27.4_TRW"])
def test_a_pin_without_matching_install_evidence_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pin: str
) -> None:
    from trw_mcp.bootstrap._version_pins import retire_default_version_pins

    config = tmp_path / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(f"debug: false\nframework_version: {pin}\n", encoding="utf-8")
    # The install record says init wrote a DIFFERENT version, so this pin is the user's choice, not init's default.
    (tmp_path / ".trw" / "installer-meta.yaml").write_text(
        "framework_version_at_install: v27.3_TRW\n", encoding="utf-8"
    )
    result: dict[str, list[str]] = {}

    retire_default_version_pins(tmp_path, result)

    assert f"framework_version: {pin}" in config.read_text(encoding="utf-8")
    assert not result.get("info")


def test_init_writes_no_framework_version_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed package supplies the default; a pin written at init would freeze the next canon bump."""
    from trw_mcp.bootstrap import init_project

    monkeypatch.setenv("MEMORY_DAEMON_AUTOSTART", "false")
    project = tmp_path / "fresh"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    assert not init_project(project, ide="claude-code")["errors"]

    assert "framework_version:" not in (project / ".trw" / "config.yaml").read_text(encoding="utf-8")


def test_a_pin_recorded_only_by_an_earlier_update_is_kept_and_warned(tmp_path: Path) -> None:
    """update-project copies config.yaml's pin into installer-meta, so an update-written record proves nothing."""
    from trw_mcp.bootstrap._version_pins import retire_default_version_pins

    config = tmp_path / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("framework_version: v27.4_TRW\n", encoding="utf-8")
    (tmp_path / ".trw" / "installer-meta.yaml").write_text(
        "framework_version_at_install: v27.4_TRW\ninstalled_by: trw-mcp update-project\n", encoding="utf-8"
    )
    result: dict[str, list[str]] = {}

    assert retire_default_version_pins(tmp_path, result) == frozenset()

    assert "framework_version: v27.4_TRW" in config.read_text(encoding="utf-8")
    assert any("framework_version" in w and "freezes" in w for w in result.get("warnings", []))
