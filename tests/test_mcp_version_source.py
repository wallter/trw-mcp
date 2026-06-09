"""Version-source tests for trw_mcp source checkouts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import trw_mcp

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
UV_LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
REQUIREMENTS_LOCK = Path(__file__).resolve().parents[1] / "requirements.lock"

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility path
    import tomli as tomllib


def _pyproject_version() -> str:
    match = re.search(r'^version = "([^"\n]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None
    return match.group(1)


def _lock_package(name: str) -> dict[str, object]:
    with UV_LOCK.open("rb") as handle:
        lock = tomllib.load(handle)
    packages = lock["package"]
    assert isinstance(packages, list)
    matches = [pkg for pkg in packages if isinstance(pkg, dict) and pkg.get("name") == name]
    assert len(matches) == 1, f"{name!r} appears {len(matches)} times in uv.lock"
    return matches[0]


def test_dunder_version_prefers_adjacent_pyproject() -> None:
    """A source checkout reports the source version, not stale installed metadata."""
    assert trw_mcp.__version__ == _pyproject_version()


def test_resolve_version_ignores_stale_installed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """PYTHONPATH imports should not inherit an older wheel's version string."""
    monkeypatch.setattr(trw_mcp._importlib_metadata, "version", lambda _name: "0.48.9")

    assert trw_mcp._resolve_version() == _pyproject_version()


def test_uv_lock_version_matches_pyproject() -> None:
    """The trw-mcp package version in uv.lock tracks pyproject.toml."""
    assert _lock_package("trw-mcp")["version"] == _pyproject_version()


def test_uv_lock_tracks_current_memory_and_sqlite_deps() -> None:
    """Lock must include the current trw-memory floor and Linux pysqlite wheel."""
    mcp_package = _lock_package("trw-mcp")
    metadata = mcp_package["metadata"]
    assert isinstance(metadata, dict)
    requirements = metadata["requires-dist"]
    assert isinstance(requirements, list)

    memory_deps = [dep for dep in requirements if isinstance(dep, dict) and dep.get("name") == "trw-memory"]
    assert memory_deps == [{"name": "trw-memory", "specifier": ">=0.8.3,<1.0.0"}]
    assert _lock_package("trw-memory")["version"] == "0.8.5"

    sqlite_deps = [dep for dep in requirements if isinstance(dep, dict) and dep.get("name") == "pysqlite3-binary"]
    assert sqlite_deps == [{"name": "pysqlite3-binary", "marker": "sys_platform == 'linux'", "specifier": ">=0.5.4"}]
    assert _lock_package("pysqlite3-binary")["version"] == "0.5.4.post2"


def test_requirements_lock_has_no_stale_git_self_pins() -> None:
    """requirements.lock must not pin local packages to a frozen git SHA."""
    text = REQUIREMENTS_LOCK.read_text(encoding="utf-8")

    stale_pin = re.compile(
        r"^-e\s+git\+.*trw-framework\.git@[0-9a-f]{7,40}.*egg=(trw_mcp|trw_memory)",
        re.MULTILINE,
    )
    assert not stale_pin.search(text)
    assert "-e ." in text
    assert "-e ../trw-memory" in text
