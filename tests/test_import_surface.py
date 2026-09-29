"""PRD-CORE-313-FR07: ``trw_mcp.api`` is the ONLY public module.

"Public" is defined by path, PEP 8 style: an importable ``trw_mcp`` submodule
path none of whose segments after ``trw_mcp`` carries a leading underscore.
A module is internal when any containing namespace is internal, so only the
top-level children of ``trw_mcp`` need a classification.

The contract is declared once, in ``trw_mcp/__init__.py``:

- ``__public_modules__``: the modules (with their subtrees) that carry a
  compatibility promise. Exactly ``("trw_mcp.api",)``.
- ``__internal_modules__``: every other non-underscore top-level child. They
  stay importable but carry no compatibility promise; any release may move,
  rename or delete them.
- ``__entry_modules__``: import paths that packaging or the launcher names
  (console-script targets, ``python -m`` targets). Their path is kept; their
  contents are still internal.

The discovery walks the filesystem, not ``pkgutil.walk_packages``, so no
subpackage is imported and implicit namespace directories (``data/``) count.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import tomllib

import trw_mcp
import trw_mcp.api

pytestmark = pytest.mark.unit

_PACKAGE_DIR = Path(trw_mcp.__file__).resolve().parent
_PYPROJECT = _PACKAGE_DIR.parents[1] / "pyproject.toml"

#: The frozen public contract of ``trw_mcp.api``. Adding a name is a minor
#: release; removing or renaming one is a major release. Change this set only
#: together with a CHANGELOG entry.
_API_ALL = frozenset(
    {
        "CEREMONY_WEIGHTS",
        "CeremonyScoreResult",
        "CeremonyWeights",
        "ClientProfile",
        "Event",
        "LearningEntry",
        "LearningStatus",
        "ModelTier",
        "Phase",
        "RunState",
        "ScoringDimensionWeights",
        "StateError",
        "TRWConfig",
        "TRWError",
        "ValidationResult",
        "WriteTargets",
        "compute_ceremony_score",
        "get_config",
        "resolve_client_profile",
        "validate_prd_quality_v2",
    }
)


def _top_level_children(package_dir: Path) -> set[str]:
    """Every importable top-level child name of the package at ``package_dir``.

    A ``.py`` file is a module; a directory with an identifier name is a
    package (regular, or an implicit namespace package when it has no
    ``__init__.py``).
    """
    names: set[str] = set()
    for entry in package_dir.iterdir():
        if entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
            names.add(entry.stem)
        elif entry.is_dir() and entry.name.isidentifier() and entry.name != "__pycache__":
            names.add(entry.name)
    return names


def _classify(module: str, public: tuple[str, ...], internal: tuple[str, ...]) -> str:
    """Classify one dotted ``trw_mcp`` module path.

    Returns ``public``, ``private`` (an underscore segment), ``internal``
    (under a declared internal top-level child) or ``undeclared``.
    """
    segments = module.split(".")
    if segments[0] != "trw_mcp" or len(segments) < 2:
        raise ValueError(f"not a trw_mcp submodule path: {module!r}")
    # An underscore segment is private even under a public module (``trw_mcp.api._x``).
    if any(segment.startswith("_") for segment in segments[1:]):
        return "private"
    if any(module == p or module.startswith(p + ".") for p in public):
        return "public"
    if segments[1] in internal:
        return "internal"
    return "undeclared"


def _declared(name: str) -> tuple[str, ...]:
    value = getattr(trw_mcp, name, ())
    assert isinstance(value, tuple), f"trw_mcp.{name} must be a tuple of strings"
    return value


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("trw_mcp.api", "public"),
        ("trw_mcp.api.scoring", "public"),
        ("trw_mcp.apix", "undeclared"),
        ("trw_mcp._locking", "private"),
        ("trw_mcp.api._compat", "private"),
        ("trw_mcp.tools._review_helpers", "private"),
        ("trw_mcp.tools", "internal"),
        ("trw_mcp.tools.learning", "internal"),
        ("trw_mcp.server.__main__", "private"),
        ("trw_mcp.brand_new_module", "undeclared"),
    ],
)
def test_classify_applies_the_path_rule(module: str, expected: str) -> None:
    assert _classify(module, ("trw_mcp.api",), ("tools", "server")) == expected


@pytest.mark.parametrize("module", ["trw_mcp", "other.api", "trw_mcpx.api"])
def test_classify_rejects_a_non_submodule_path(module: str) -> None:
    with pytest.raises(ValueError, match="not a trw_mcp submodule path"):
        _classify(module, ("trw_mcp.api",), ())


def test_top_level_discovery_counts_namespace_dirs_and_skips_caches(tmp_path: Path) -> None:
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "mod.py").write_text("")
    (tmp_path / "_private.py").write_text("")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "nsdir").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "not-an-identifier").mkdir()
    (tmp_path / "notes.txt").write_text("")

    assert _top_level_children(tmp_path) == {"mod", "_private", "pkg", "nsdir"}


def test_api_is_the_only_public_module() -> None:
    assert _declared("__public_modules__") == ("trw_mcp.api",)


def test_every_public_top_level_module_is_declared_internal() -> None:
    public = _declared("__public_modules__")
    internal = _declared("__internal_modules__")
    undeclared = sorted(
        name
        for name in _top_level_children(_PACKAGE_DIR)
        if _classify(f"trw_mcp.{name}", public, internal) == "undeclared"
    )
    assert undeclared == [], (
        "public trw_mcp import paths outside trw_mcp.api: underscore-prefix them "
        f"or add them to trw_mcp.__internal_modules__: {undeclared}"
    )


def test_no_declared_internal_module_is_stale() -> None:
    internal = _declared("__internal_modules__")
    children = _top_level_children(_PACKAGE_DIR)
    stale = sorted(name for name in internal if name not in children or name.startswith("_") or name == "api")
    assert stale == [], f"trw_mcp.__internal_modules__ names no public top-level child: {stale}"
    assert len(internal) == len(set(internal)), "trw_mcp.__internal_modules__ has duplicates"


def test_console_script_targets_are_declared_entry_modules() -> None:
    scripts = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["scripts"]
    targets = {target.split(":", 1)[0] for target in scripts.values()}
    entry = set(_declared("__entry_modules__"))
    assert targets, "pyproject declares no console scripts"
    assert targets <= entry, f"console-script modules missing from trw_mcp.__entry_modules__: {sorted(targets - entry)}"


def test_entry_modules_are_runnable_and_internal() -> None:
    entry = _declared("__entry_modules__")
    internal = _declared("__internal_modules__")
    assert entry, "trw_mcp.__entry_modules__ is empty"
    for module in entry:
        assert _classify(module, (), internal) == "internal", module
        # ``python -m <module>`` needs a __main__ for a package entry path.
        spec = importlib.util.find_spec(module)
        assert spec is not None, module
        if spec.submodule_search_locations is not None:
            assert importlib.util.find_spec(f"{module}.__main__") is not None, module


def test_api_all_is_the_frozen_contract() -> None:
    assert set(trw_mcp.api.__all__) == _API_ALL
    assert len(trw_mcp.api.__all__) == len(_API_ALL), "trw_mcp.api.__all__ has duplicates"
    missing = sorted(name for name in _API_ALL if not hasattr(trw_mcp.api, name))
    assert missing == []
