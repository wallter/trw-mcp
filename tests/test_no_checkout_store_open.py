"""PRD-CORE-280 e3: trw-mcp never opens a checkout's memory.db; every read and write goes through ``selected_store``.

A source scan, because the failure it guards against is a new call site: a probe,
a counter or a maintenance step that reaches for the file directly instead of the
daemon. Two modules may still touch the file, each for a stated reason:

* ``state/_store_migration.py`` moves an unmigrated store into the daemon and
  checks it is empty afterwards (``memory migrate``).
* ``server/_doctor_memory_store.py`` reads it read-only to report stray rows a
  pinned checkout's file still holds (``trw-mcp doctor``).

The scan finds an opener by the ``"memory.db"`` literal in the same module. The
WAL maintenance (``maybe_checkpoint_wal``, ``_wal_idle_sweep``) opened the file
through a path another module built, so the literal missed it; it is deleted,
and importing it, or any e3 accessor or module, fails here too.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"

#: The in-process accessors e3 deleted: none may be defined or called again.
_ACCESSORS = frozenset({"get_backend", "get_user_backend", "peek_backend", "peek_user_backend"})

#: Modules e3 deleted: none may be imported again.
_DELETED_MODULES = frozenset(
    {"trw_mcp.state._memory_recovery", "trw_mcp.state._user_tier", "trw_mcp.state._wal_idle_sweep"}
)

#: Other names e3 deleted: none may be imported again.
_DELETED_NAMES = _ACCESSORS | {"maybe_checkpoint_wal", "start_wal_checkpoint_sweeper", "checkpoint_after_commit"}

#: Modules that may open a file they locate as ``memory.db`` (see the module docstring).
_MAY_OPEN_THE_CHECKOUT_STORE = frozenset({"state/_store_migration.py", "server/_doctor_memory_store.py"})


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr == "connect" and isinstance(func.value, ast.Name) and func.value.id == "sqlite3":
            return "sqlite3.connect"
        return func.attr
    return func.id if isinstance(func, ast.Name) else None


def _imported(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {f"import {a.name}" for a in node.names if a.name in _DELETED_MODULES}
    base = node.module or ""
    found = {f"import {base}"} if base in _DELETED_MODULES else set()
    for alias in node.names:
        if alias.name in _DELETED_NAMES or f"{base}.{alias.name}" in _DELETED_MODULES:
            found.add(f"import {base}.{alias.name}")
    return found


def _scan(root: Path = _SRC) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``({module: e3 names it defines, calls or imports}, {module: how it opens a store it names memory.db})``."""
    accessors: dict[str, list[str]] = {}
    openers: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names_the_store = False
        opens: set[str] = set()
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "memory.db":
                names_the_store = True
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                found |= _imported(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _ACCESSORS:
                found.add(f"def {node.name}")
            elif isinstance(node, ast.Call):
                name = _call_name(node)
                if name in _ACCESSORS:
                    found.add(name)
                elif name in ("SQLiteBackend", "sqlite3.connect"):
                    opens.add(name)
        if found:
            accessors[module] = sorted(found)
        if names_the_store and opens:
            openers[module] = sorted(opens)
    return accessors, openers


def test_no_module_defines_or_calls_an_in_process_store_accessor() -> None:
    accessors, _openers = _scan()

    assert accessors == {}, f"in-process store accessors are back: {accessors}"


def test_only_migration_and_the_doctor_open_a_checkouts_memory_db() -> None:
    _accessors, openers = _scan()

    assert set(openers) <= _MAY_OPEN_THE_CHECKOUT_STORE, (
        f"these modules open a store they locate as memory.db; route them through selected_store: "
        f"{ {module: how for module, how in openers.items() if module not in _MAY_OPEN_THE_CHECKOUT_STORE} }"
    )


def test_the_scan_sees_the_two_modules_it_allows() -> None:
    """A scan that finds nothing proves nothing: the allowed openers must still register as openers."""
    _accessors, openers = _scan()

    assert set(openers) >= _MAY_OPEN_THE_CHECKOUT_STORE


@pytest.mark.parametrize(
    "planted",
    [
        "from trw_mcp.state.memory_adapter import get_backend\n",
        "from trw_mcp.state import _memory_recovery\n",
        "import trw_mcp.state._user_tier\n",
        "from trw_mcp.state._wal_idle_sweep import start_wal_checkpoint_sweeper\n",
        "from trw_mcp.state.memory_adapter import maybe_checkpoint_wal\n",
    ],
)
def test_the_scan_fails_on_a_planted_import(tmp_path: Path, planted: str) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "planted.py").write_text(planted, encoding="utf-8")

    accessors, _openers = _scan(tmp_path)

    assert list(accessors) == ["state/planted.py"]
