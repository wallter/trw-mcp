"""Package subjects remain testable without silently skipping broken canons."""

import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize("name", ["arbitrary-checkout", "trw-mcp/trw-mcp"])
def test_standalone_package_has_no_monorepo(tmp_path: Path, name: str) -> None:
    package = tmp_path / name
    state = _load_layout(package)
    assert state["PACKAGE_ROOT"] == package
    assert state["MONOREPO_ROOT"] is None
    assert state["requires_monorepo"].args == (True,)


def test_independent_identity_does_not_hide_missing_checked_files(tmp_path: Path) -> None:
    (tmp_path / "release-packages.yaml").write_text("packages: {}\n")
    state = _load_layout(tmp_path / "trw-mcp")
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".claude").exists()
    assert state["MONOREPO_ROOT"] == tmp_path
    assert state["requires_monorepo"].args == (False,)


def test_legacy_identity_does_not_hide_missing_release_manifest(tmp_path: Path) -> None:
    for package in ("trw-mcp", "trw-memory"):
        directory = tmp_path / package
        directory.mkdir()
        (directory / "pyproject.toml").write_text("")
    (tmp_path / "CLAUDE.md").write_text("")
    assert _load_layout(tmp_path / "trw-mcp")["MONOREPO_ROOT"] == tmp_path


def test_installed_package_is_not_a_monorepo(tmp_path: Path) -> None:
    parent = tmp_path / "site-packages"
    parent.mkdir()
    (parent / "release-packages.yaml").write_text("")
    assert _load_layout(parent / "trw-mcp")["MONOREPO_ROOT"] is None


def _load_layout(package: Path) -> dict:
    tests = package / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    target = tests / "_layout.py"
    target.write_text(Path(__file__).with_name("_layout.py").read_text())
    return runpy.run_path(str(target))
