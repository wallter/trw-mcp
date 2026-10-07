"""SERIAL-RUN-LEAKS (C) census: every SQLite open of a pinned store goes through its owner, which holds it.

The pinned-read cache may evict an idle descriptor only when no connection in this process can hold a lock on its
inode, and it learns that from ``held_connect``. A third code path that opened ``operations.sqlite3`` or
``comms.sqlite3`` with a bare ``sqlite3.connect`` would be invisible to it: an eviction could then close the header
descriptor while that connection held a lock, dropping it (the C15 hazard). This scan fails such a path by name.

Owners: the delivery journal (``tools/_delivery_journal_store.py``) and the comms store (``comms/_store.py``, and
``comms/_upgrade.py`` for its exclusive upgrade connection). Every other mailbox reader uses
``comms._store.open_mailbox_ro``.

Residual (auditor, P2): a raw ``open()``/``read_bytes()`` of either store file, or an opener in another package that
runs in the server process, is not detected here; the C15 hazard of such a read exists with or without eviction.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests import _source_index as source_index

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"

#: A module that names either store, or the comms mailbox helpers, is in scope for the scan.
_MARKERS = ("operations.sqlite3", "comms.sqlite3", "DATABASE_FILENAME", "database_path(", "JournalStore")

#: The only modules that may call ``held_connect`` (the hold registration lives in the two owners only).
_OWNERS = frozenset({"tools/_delivery_journal_store.py", "comms/_store.py", "comms/_upgrade.py"})

#: In-scope ``sqlite3.connect`` calls that open something other than a pinned store, by (module, function).
_NOT_A_PINNED_STORE = frozenset(
    {
        ("comms/_schema.py", "_expected_ddl"),  # ":memory:"
        ("comms/_upgrade.py", "_verified_backup"),  # the .bak copy, never pinned
        ("comms/_upgrade.py", "rollback"),  # reads the .bak copy into the held exclusive connection
    }
)


def _is_partial_of_connect(call: ast.Call) -> bool:
    """``partial(sqlite3.connect, ...)`` / ``functools.partial(sqlite3.Connection, ...)``: an opener in disguise (auditor)."""
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name != "partial" or not call.args:
        return False
    target = call.args[0]
    return (
        isinstance(target, ast.Attribute)
        and target.attr in ("connect", "Connection")
        and getattr(target.value, "id", "") == "sqlite3"
    )


def _calls(tree: ast.AST) -> list[tuple[str, str, int]]:
    """``(called name, enclosing function, line)`` for every ``sqlite3.connect``/``held_connect`` call."""
    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            scope = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call):
                func = child.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in ("connect", "Connection")
                    and getattr(func.value, "id", "") == "sqlite3"
                ):
                    found.append(("sqlite3.connect", scope, child.lineno))
                elif _is_partial_of_connect(child):
                    found.append(("sqlite3.connect", scope, child.lineno))
                elif (isinstance(func, ast.Name) and func.id == "held_connect") or (
                    isinstance(func, ast.Attribute) and func.attr == "held_connect"
                ):
                    found.append(("held_connect", scope, child.lineno))
            visit(child, scope)

    visit(tree, "<module>")
    return found


def _modules() -> list[tuple[str, str, ast.AST]]:
    out = []
    for path in sorted(_SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        out.append((str(path.relative_to(_SRC)), text, source_index.parse(text)))
    return out


def test_no_bare_sqlite_open_of_a_pinned_store_outside_its_owner() -> None:
    offenders = [
        f"{rel}:{line} in {function}"
        for rel, text, tree in _modules()
        if any(marker in text for marker in _MARKERS)
        for name, function, line in _calls(tree)
        if name == "sqlite3.connect" and (rel, function) not in _NOT_A_PINNED_STORE
    ]
    assert offenders == [], offenders


def test_only_the_two_owners_register_holds() -> None:
    callers = sorted(
        {rel for rel, _text, tree in _modules() for name, _f, _l in _calls(tree) if name == "held_connect"}
        - {"_checkout_access.py"}
    )
    assert set(callers) <= _OWNERS, callers
    assert {"tools/_delivery_journal_store.py", "comms/_store.py", "comms/_upgrade.py"} <= set(callers)


def test_the_census_sees_the_shape_it_guards_against() -> None:
    tree = ast.parse("import sqlite3\ndef reader(p):\n    return sqlite3.connect(str(p))\n")
    assert _calls(tree) == [("sqlite3.connect", "reader", 3)]
    disguised = ast.parse(
        "import sqlite3, functools\n"
        "def a(p):\n    return sqlite3.Connection(str(p))\n"
        "def b(p):\n    return functools.partial(sqlite3.connect, str(p))()\n"
    )
    assert {(name, function) for name, function, _ in _calls(disguised)} == {
        ("sqlite3.connect", "a"),
        ("sqlite3.connect", "b"),
    }
