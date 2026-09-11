"""Version-source tests for trw_mcp source checkouts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

import trw_mcp

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
UV_LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
REQUIREMENTS_LOCK = Path(__file__).resolve().parents[1] / "requirements.lock"
PATCHED_FASTMCP_FLOOR = (3, 2, 0)
# Optional, ignored developer freeze: neither shipped nor consumed by public CI.
# uv.lock checks below remain unconditional and fail if the shipped lock is absent.
requires_developer_freeze = pytest.mark.skipif(
    not REQUIREMENTS_LOCK.is_file(), reason="optional unshipped developer requirements.lock is absent"
)

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


def _pyproject_specifier(package: str) -> str:
    """Return the version specifier pyproject.toml declares for *package*.

    Derived, never hardcoded: a literal expectation goes stale on every bump and
    then fails for a reason unrelated to the invariant it is supposed to guard.
    """
    for raw in _pyproject()["project"]["dependencies"]:
        requirement = str(raw)
        name, _, specifier = requirement.partition(">=")
        if name.strip().split(";")[0].strip() == package:
            return f">={specifier.strip()}"
    raise AssertionError(f"{package} is not a declared runtime dependency")


def test_uv_lock_dependency_specifiers_match_pyproject() -> None:
    """uv.lock must record the SAME trw-memory floor pyproject declares.

    This is the invariant that protects users, not a cosmetic sync: the lock is
    what an install resolves from, so a lock whose floor lags pyproject silently
    hands every user a package older than the code requires — a symbol added in
    the newer version then degrades or raises at runtime with nothing failing at
    install time. (Observed 2026-07-24: `verification_status` shipped in
    trw-memory while consumers still resolved a release without it.)

    Expectations are DERIVED from pyproject, so this test stays correct across
    version bumps and fails only when the lock genuinely drifts. When it fails:
    publish the new trw-memory, then re-run `uv lock` in trw-mcp — in that order,
    since the lock resolves trw-memory from the PyPI registry and cannot pin a
    version that is not published yet.
    """
    metadata = _lock_package("trw-mcp")["metadata"]
    assert isinstance(metadata, dict)
    requirements = metadata["requires-dist"]
    assert isinstance(requirements, list)

    declared = _pyproject_specifier("trw-memory")
    locked = [dep for dep in requirements if isinstance(dep, dict) and dep.get("name") == "trw-memory"]
    assert len(locked) == 1, f"expected exactly one trw-memory requirement, got {locked}"
    assert locked[0].get("specifier") == declared, (
        f"uv.lock records trw-memory{locked[0].get('specifier')} but pyproject declares "
        f"trw-memory{declared}. Publish trw-memory first, then re-run `uv lock`."
    )

    # The resolved version must actually satisfy the declared floor — a matching
    # specifier string with a stale resolved version is the same user-facing bug.
    resolved = str(_lock_package("trw-memory")["version"])
    assert SpecifierSet(declared).contains(resolved), (
        f"uv.lock resolved trw-memory=={resolved}, which does not satisfy {declared}"
    )

    sqlite_deps = [dep for dep in requirements if isinstance(dep, dict) and dep.get("name") == "pysqlite3-binary"]
    assert sqlite_deps == [{"name": "pysqlite3-binary", "marker": "sys_platform == 'linux'", "specifier": ">=0.5.4"}]
    assert SpecifierSet(">=0.5.4").contains(str(_lock_package("pysqlite3-binary")["version"]))


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
    """The shipped registry lock must avoid vulnerable FastMCP releases."""
    fastmcp_package = _lock_package("fastmcp")
    version = fastmcp_package["version"]
    assert isinstance(version, str)

    assert _version_tuple(version) >= PATCHED_FASTMCP_FLOOR


@requires_developer_freeze
def test_developer_freeze_fastmcp_pin_is_on_patched_floor() -> None:
    assert _version_tuple(_requirements_lock_package_version("fastmcp")) >= PATCHED_FASTMCP_FLOOR


@requires_developer_freeze
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


@requires_developer_freeze
def test_requirements_lock_omits_stale_no_fix_vulnerable_pins() -> None:
    """requirements.lock must not carry unused no-fix vulnerable transitive pins."""
    text = REQUIREMENTS_LOCK.read_text(encoding="utf-8").lower()

    assert "lupa==" not in text
    assert "sentence-transformers==" not in text
    assert "torch==" not in text
    assert "transformers==" not in text


@requires_developer_freeze
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
