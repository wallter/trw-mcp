"""Version-source tests for trw_mcp source checkouts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

import trw_mcp
from tests._layout import MONOREPO_ROOT, requires_monorepo

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


@pytest.mark.requires_published_lock
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


@pytest.mark.requires_published_lock
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
    every = [dep for dep in requirements if isinstance(dep, dict) and dep.get("name") == "trw-memory"]
    # PRD-CORE-342 FR01: the `otel` extra adds `trw-memory[otel]` under an extra marker; the floor lives on the
    # one unconditional requirement, and the extra requirement must not declare a different floor.
    locked = [dep for dep in every if "marker" not in dep]
    assert len(locked) == 1, f"expected exactly one unconditional trw-memory requirement, got {every}"
    assert all(dep.get("specifier") in (None, declared) for dep in every), every
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

    # PRD-INFRA-185 FR01: pysqlite3-binary is an OPTIONAL extra now, not a runtime
    # requirement. As a hard Linux dependency it made aarch64 Linux installs fail
    # outright (it publishes one manylinux2014_x86_64 wheel and nothing else) while
    # delivering SQLite 3.51.1 -- below the 3.51.3 WAL-reset fix it existed for.
    # This is the check that would catch a lock still recording the old shape;
    # `make lockfile-parity` compares SELF-VERSIONS only and would not notice.
    sqlite_deps = [dep for dep in requirements if isinstance(dep, dict) and dep.get("name") == "pysqlite3-binary"]
    assert sqlite_deps == [
        {
            "name": "pysqlite3-binary",
            "marker": "platform_machine == 'x86_64' and sys_platform == 'linux' and extra == 'sqlite-fix'",
            "specifier": ">=0.5.4",
        }
    ]
    assert SpecifierSet(">=0.5.4").contains(str(_lock_package("pysqlite3-binary")["version"]))

    declared_runtime = _pyproject()["project"]
    assert isinstance(declared_runtime, dict)
    runtime_deps = declared_runtime["dependencies"]
    assert isinstance(runtime_deps, list)
    assert not [dep for dep in runtime_deps if "pysqlite3" in str(dep)]
    optional = declared_runtime["optional-dependencies"]
    assert isinstance(optional, dict)
    assert "sqlite-fix" in optional


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


def test_pyproject_declares_311_runtime_floor() -> None:
    """The manifest floor matches what code_index._require_runtime already enforces (PRD-INFRA-200 FR04).

    Python 3.10 reaches end of life 2026-10; trw_mcp.code_index.store._require_runtime already
    fails closed below 3.11 at runtime, so requires-python must catch up to that floor.
    """
    pyproject = _pyproject()
    project = pyproject["project"]
    assert isinstance(project, dict)
    assert project["requires-python"] == ">=3.11"
    classifiers = project["classifiers"]
    assert isinstance(classifiers, list)
    assert "Programming Language :: Python :: 3.10" not in classifiers
    assert "Programming Language :: Python :: 3.11" in classifiers

    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)
    assert not any("python_version < '3.11'" in dep for dep in dependencies), (
        "tomli's <3.11 marker is now always false and should be an unconditional dependency"
    )


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
    # No import is exempt from DEP001: tiktoken is no longer imported by trw-mcp, so `make dep-check` is blocking.
    assert "DEP001" not in per_rule
    # PRD-CORE-342 FR01: distro and the gRPC meta exporter are gone; opentelemetry-api is a declared base dep.
    assert per_rule["DEP002"] == ["starlette"]
    assert "DEP003" not in per_rule
    # rank-bm25 is a trw-memory base dependency (PRD-CORE-302 FR08); trw-mcp neither imports nor declares it.
    assert "DEP004" not in per_rule


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


def test_the_lock_tests_wait_for_the_published_dependency() -> None:
    """``uv lock`` can record a new trw-memory floor only once it is on PyPI, so the paired
    pre-cut check (``release_public.py check --with-local``) and the C1 release gate both
    deselect these two; ``release_public.py all``'s post-lock-refresh ``check trw-mcp``
    (no ``--with-local``) keeps them and fails the cut if they fail."""
    for test in (test_uv_lock_version_matches_pyproject, test_uv_lock_dependency_specifiers_match_pyproject):
        assert "requires_published_lock" in {mark.name for mark in getattr(test, "pytestmark", [])}


@requires_monorepo
def test_trw_memory_floor_is_never_behind_trw_memory() -> None:
    """The trw-memory pin names trw-memory's own major or the next one, never an older one.

    Before the cut the floor may lead (it names the unreleased major that exports what trw-mcp
    imports); after release_cut_prep.py bumps trw-memory, a floor left on the previous major
    would pin every install to a trw-memory that lacks those symbols.
    """
    assert MONOREPO_ROOT is not None
    pin = re.search(r'"trw-memory>=(\d+)\.\d+\.\d+,<(\d+)\.0\.0"', PYPROJECT.read_text(encoding="utf-8"))
    assert pin is not None, "no 'trw-memory>=X.Y.Z,<A.0.0' pin in trw-mcp/pyproject.toml"
    floor_major, upper_major = int(pin.group(1)), int(pin.group(2))
    memory = re.search(
        r'^version = "(\d+)\.',
        (MONOREPO_ROOT / "trw-memory" / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert memory is not None
    assert floor_major >= int(memory.group(1)), "the trw-memory floor is behind trw-memory's own major"
    assert upper_major == floor_major + 1, "the trw-memory pin must admit exactly one major"
