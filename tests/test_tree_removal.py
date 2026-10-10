"""PRD-FIX-156 FR01 (B71-10): trw_mcp cleanup removals go through trw-memory's one helper.

:func:`trw_memory._tree_removal.remove_tree` owns the contract (trw-memory's ``tests/test_tree_removal.py``);
this census keeps every ``shutil.rmtree`` in ``trw_mcp`` from carrying its own error handler again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import trw_mcp
from tests import _source_index as source_index

pytestmark = pytest.mark.repo_scan

_HANDLER_KEYWORDS = frozenset({"ignore_errors", "onerror", "onexc"})


def _handler_rmtree_calls() -> list[str]:
    """Every ``rmtree(...)`` passing an error handler, as ``path: call source`` (content, not line)."""
    root = Path(trw_mcp.__file__).parent
    found = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(source_index.tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name == "rmtree" and (len(node.args) > 1 or {k.arg for k in node.keywords} & _HANDLER_KEYWORDS):
                found.append(f"{path.relative_to(root)}: {ast.unparse(node)}")
    return found


def test_no_rmtree_in_trw_mcp_carries_its_own_error_handler() -> None:
    """B71-10 census: ``ignore_errors``/``onerror``/``onexc`` hide or reinvent failure handling; use remove_tree."""
    assert _handler_rmtree_calls() == []
