"""Version-source tests for trw_mcp source checkouts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import trw_mcp

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
UV_LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
REQUIREMENTS_LOCK = Path(__file__).resolve().parents[1] / "requirements.lock"
PATCHED_FASTMCP_FLOOR = (3, 2, 0)

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


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[.+-]", version) if part.isdigit())


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _dependency_names(dependencies: list[str]) -> set[str]:
    """Return normalized package names from PEP 508 dependency strings."""
    return {re.split(r"[<>=!~;\\[]", dep, maxsplit=1)[0].strip().lower().replace("_", "-") for dep in dependencies}


def _requirements_lock_package_version(name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}==([^\s]+)$",
        REQUIREMENTS_LOCK.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None, f"{name!r} not found in requirements.lock"
    return match.group(1)


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
    assert memory_deps == [{"name": "trw-memory", "specifier": ">=0.9.0,<1.0.0"}]
    assert _lock_package("trw-memory")["version"] == "0.9.0"

    sqlite_deps = [dep for dep in requirements if isinstance(dep, dict) and dep.get("name") == "pysqlite3-binary"]
    assert sqlite_deps == [{"name": "pysqlite3-binary", "marker": "sys_platform == 'linux'", "specifier": ">=0.5.4"}]
    assert _lock_package("pysqlite3-binary")["version"] == "0.5.4.post2"


def test_pyproject_declares_core_runtime_direct_dependencies() -> None:
    """Core runtime imports must not rely on FastMCP's transitive dependency graph."""
    pyproject = _pyproject()
    project = pyproject["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)

    assert {
        "cryptography",
        "httpx",
        "mcp",
        "pyyaml",
        "starlette",
    }.issubset(_dependency_names(dependencies))


def test_pyproject_deptry_config_keeps_static_audit_signal_focused() -> None:
    """Deptry should scan the src-layout package without optional-import noise."""
    pyproject = _pyproject()
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    deptry = tool["deptry"]
    assert isinstance(deptry, dict)

    assert deptry["known_first_party"] == ["trw_mcp"]
    assert deptry["optional_dependencies_dev_groups"] == ["dev"]
    assert deptry["extend_exclude"] == ["scripts/install-trw.template.py"]
    per_rule = deptry["per_rule_ignores"]
    assert isinstance(per_rule, dict)
    assert per_rule["DEP001"] == ["tiktoken"]
    assert per_rule["DEP002"] == ["opentelemetry-distro", "opentelemetry-exporter-otlp", "starlette"]
    assert per_rule["DEP003"] == ["opentelemetry", "tiktoken"]
    assert per_rule["DEP004"] == ["rank_bm25"]


def test_fastmcp_pins_are_on_patched_floor() -> None:
    """Both lock surfaces must avoid vulnerable FastMCP releases."""
    fastmcp_package = _lock_package("fastmcp")
    version = fastmcp_package["version"]
    assert isinstance(version, str)

    assert _version_tuple(version) >= PATCHED_FASTMCP_FLOOR
    assert _version_tuple(_requirements_lock_package_version("fastmcp")) >= PATCHED_FASTMCP_FLOOR


def test_requirements_lock_security_pin_floors_are_patched() -> None:
    """Known-audited requirements.lock pins stay above patched floors."""
    floors = {
        "Authlib": (1, 6, 12),
        "urllib3": (2, 7, 0),
        "cryptography": (48, 0, 1),
        "ecdsa": (0, 19, 2),
        "idna": (3, 15),
        "lxml": (6, 1, 0),
        "Mako": (1, 3, 12),
        "pyasn1": (0, 6, 3),
        "Pygments": (2, 20, 0),
        "PyJWT": (2, 13, 0),
        "pydantic-settings": (2, 14, 2),
        "pytest": (9, 0, 3),
        "python-dotenv": (1, 2, 2),
        "python-multipart": (0, 0, 27),
        "requests": (2, 33, 0),
        "starlette": (1, 3, 1),
    }
    for package, floor in floors.items():
        assert _version_tuple(_requirements_lock_package_version(package)) >= floor


def test_requirements_lock_omits_stale_no_fix_vulnerable_pins() -> None:
    """requirements.lock must not carry unused no-fix vulnerable transitive pins."""
    text = REQUIREMENTS_LOCK.read_text(encoding="utf-8").lower()

    assert "lupa==" not in text
    assert "sentence-transformers==" not in text
    assert "torch==" not in text
    assert "transformers==" not in text


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
