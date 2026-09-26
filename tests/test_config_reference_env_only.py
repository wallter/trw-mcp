"""W35 item 4: config-reference must list env-only ``TRW_`` variables too.

``config-reference`` built its table from ``_TRWConfigFields.model_fields``
alone, so a variable read straight from ``os.environ``/``os.getenv`` --
declared nowhere as a ``TRWConfig`` field -- was invisible (``TRW_PROBE_ENABLED``
was named in the bug report; its sibling ``TRW_OFFLINE`` was retired by
PRD-CORE-302 W40). This AST-scans
``src/`` for that category of read and fails when one is missing from
``ENV_ONLY_VARS`` (drift undetected) or when a registry entry is no longer
read anywhere (a stale, misleading row).

The scan covers three shapes actually used in this codebase, not just the
literal ``os.environ.get("TRW_...")`` textual pattern:

1. A direct call -- ``os.environ.get("TRW_X")``, ``os.getenv("TRW_X")``, or
   ``os.environ["TRW_X"]`` -- with a string-literal argument/key.
2. A module-level (or any-scope) tuple/list/set assigned to a name containing
   "ENV", whose elements are all ``TRW_``-prefixed string literals (the
   ``_X_ENV_VARS = ("TRW_X", "HF_X")`` shape).
3. A call to a local "flag wrapper" function -- one whose body forwards its
   first parameter verbatim into ``os.environ.get``/``os.getenv`` (the
   ``_flag(name)`` shape in ``tools/_experiment_cli.py``) -- with a
   ``TRW_``-prefixed string-literal argument at any call site.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"
_TRW_NAME_RE = re.compile(r"^TRW_[A-Z0-9_]+$")


def _iter_py_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if "/data/" not in str(p))


def _is_environ_get_or_getenv(func: ast.expr) -> bool:
    """True for ``os.environ.get`` / ``environ.get`` / ``os.getenv`` call targets."""
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "getenv":
        return True
    return func.attr == "get" and isinstance(func.value, ast.Attribute) and func.value.attr == "environ"


def _is_environ_subscript_target(value: ast.expr) -> bool:
    return isinstance(value, ast.Attribute) and value.attr == "environ"


def _string_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _FileScan:
    """One file's direct TRW_ literal reads and its "flag wrapper" function names."""

    def __init__(self) -> None:
        self.direct_names: set[str] = set()
        self.wrapper_func_names: set[str] = set()
        self.registry_names: set[str] = set()
        self.call_sites: list[tuple[str, str | None]] = []  # (func-name-called, literal-arg-or-None)


def _scan_file(tree: ast.AST) -> _FileScan:
    scan = _FileScan()

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.args.args:
            first_param = node.args.args[0].arg
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and _is_environ_get_or_getenv(sub.func) and sub.args:
                    arg0 = sub.args[0]
                    if isinstance(arg0, ast.Name) and arg0.id == first_param:
                        scan.wrapper_func_names.add(node.name)

        if isinstance(node, ast.Call):
            if _is_environ_get_or_getenv(node.func) and node.args:
                literal = _string_literal(node.args[0])
                if literal and literal.startswith("TRW_"):
                    scan.direct_names.add(literal)
            func_name = node.func.id if isinstance(node.func, ast.Name) else None
            if func_name and node.args:
                literal = _string_literal(node.args[0])
                scan.call_sites.append((func_name, literal))

        if isinstance(node, ast.Subscript) and _is_environ_subscript_target(node.value):
            literal = _string_literal(node.slice)
            if literal and literal.startswith("TRW_"):
                scan.direct_names.add(literal)

        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any("ENV" in n.upper() for n in names) and isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                # A mixed registry (e.g. `("TRW_X", "HF_X")`) only
                # contributes its TRW_-prefixed elements -- the non-TRW_ sibling
                # names (upstream conventions like HF_HUB_OFFLINE) are out of scope.
                for element in node.value.elts:
                    literal = _string_literal(element)
                    if literal and _TRW_NAME_RE.match(literal):
                        scan.registry_names.add(literal)

    return scan


def _all_trw_env_only_reads() -> set[str]:
    """Every ``TRW_`` name this scan can prove is read from the environment."""
    from trw_mcp.models.config._main_fields import _TRWConfigFields

    config_field_env_vars = {f"TRW_{name.upper()}" for name in _TRWConfigFields.model_fields}

    per_file: dict[Path, _FileScan] = {}
    found: set[str] = set()
    all_wrapper_names: set[str] = set()

    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scan = _scan_file(tree)
        per_file[path] = scan
        found |= scan.direct_names
        found |= scan.registry_names
        all_wrapper_names |= scan.wrapper_func_names

    # Second pass: calls to a wrapper function (found anywhere) with a literal arg.
    for scan in per_file.values():
        for func_name, literal in scan.call_sites:
            if func_name in all_wrapper_names and literal and literal.startswith("TRW_"):
                found.add(literal)

    return found - config_field_env_vars


@pytest.mark.unit
def test_every_env_only_read_is_registered() -> None:
    from trw_mcp.models.config._env_only_vars import ENV_ONLY_VARS

    registered = {v.name for v in ENV_ONLY_VARS}
    found = _all_trw_env_only_reads()
    missing = found - registered
    assert not missing, (
        f"env-only TRW_ vars read from os.environ but missing from ENV_ONLY_VARS: {sorted(missing)} "
        "(add them to trw_mcp/models/config/_env_only_vars.py)"
    )


@pytest.mark.unit
def test_no_stale_registry_entries() -> None:
    """A registry entry nobody reads any more is a row that misleads an operator."""
    from trw_mcp.models.config._env_only_vars import ENV_ONLY_VARS

    found = _all_trw_env_only_reads()
    registered = {v.name for v in ENV_ONLY_VARS}
    stale = registered - found
    assert not stale, f"ENV_ONLY_VARS entries no longer read anywhere in src/: {sorted(stale)}"


@pytest.mark.unit
def test_config_reference_lists_env_only_vars(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    from trw_mcp.server._subcommands_misc import _run_config_reference

    _run_config_reference(argparse.Namespace())
    output = capsys.readouterr().out

    assert "TRW_PROBE_ENABLED" in output
    assert "TRW_OFFLINE" not in output, "a retired switch is still advertised"


@pytest.mark.unit
def test_the_two_reported_vars_are_registered() -> None:
    """Regression for the exact defect named in the bug report."""
    from trw_mcp.models.config._env_only_vars import ENV_ONLY_VARS

    names = {v.name for v in ENV_ONLY_VARS}
    assert "TRW_PROBE_ENABLED" in names
    assert "TRW_OFFLINE" not in names
