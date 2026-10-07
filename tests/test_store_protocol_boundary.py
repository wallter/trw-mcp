"""B71-00: trw-mcp reaches the store protocol only through ``trw_memory.store_access``.

A second opener of a store's lock file would drop that process's lock when it
closed, so trw-mcp never names the file or imports the module that owns it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import trw_mcp
from tests import _source_index as source_index

pytestmark = pytest.mark.unit


def test_trw_mcp_never_reaches_past_store_access() -> None:
    offenders: list[str] = []
    root = Path(trw_mcp.__file__).parent
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(source_index.tree(path)):
            if isinstance(node, ast.ImportFrom):
                modules = [f"{node.module}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                modules = []
            names_lock_file = isinstance(node, ast.Constant) and isinstance(node.value, str) and ".oplock" in node.value
            if names_lock_file or any(m.startswith("trw_memory._store_lock") for m in modules):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []
