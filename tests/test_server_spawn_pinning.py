"""PRD-QUAL-146 FR06: every spawned ``python -m trw_mcp.server`` runs the code under test.

A worktree's venv is an editable install of the MAIN checkout, so a child that
inherits no ``PYTHONPATH`` imports the main checkout's trw_mcp, not this tree's.
``tests._stdio_harness.pinned_server_env`` puts both source roots of this tree
first; the census below fails when a spawn bypasses it.

Soundness scope: the census proves each ``subprocess.run/Popen/check_output/call``
whose argv literal holds ``"-m", "trw_mcp.server"`` passes ``env=pinned_server_env(...)``
directly, or carries a ``# spawn-pin: exempt`` comment (a test whose own PYTHONPATH
is the subject). It does not see argv built outside a list literal, and it does not
prove the base env handed to the helper is otherwise correct.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest

from tests import _source_index as source_index
from tests._stdio_harness import pinned_server_env

pytestmark = pytest.mark.repo_scan

_TESTS = Path(__file__).resolve().parent
_TREE = _TESTS.parents[1]
_EXEMPT = "# spawn-pin: exempt"  # a spawn whose own PYTHONPATH is the subject under test
_SPAWNERS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


def _spawns_server(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name not in _SPAWNERS or not call.args or not isinstance(call.args[0], ast.List | ast.Tuple):
        return False
    values = [e.value if isinstance(e, ast.Constant) else None for e in call.args[0].elts]
    return any(a == "-m" and b == "trw_mcp.server" for a, b in pairwise(values))


def unpinned_spawns(source: str, path: str) -> list[str]:
    tree = source_index.parse(source)
    lines = source.splitlines()
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _spawns_server(node)):
            continue
        env = next((kw.value for kw in node.keywords if kw.arg == "env"), None)
        pinned = isinstance(env, ast.Call) and getattr(env.func, "id", "") == "pinned_server_env"
        span = lines[node.lineno - 1 : node.end_lineno]
        if not pinned and not any(_EXEMPT in line for line in span):
            bad.append(f"{path}:{node.lineno}")
    return bad


def test_no_test_spawns_the_server_without_the_pinned_environment() -> None:
    bad = []
    for path in sorted(_TESTS.rglob("*.py")):
        bad += unpinned_spawns(path.read_text(encoding="utf-8"), str(path.relative_to(_TESTS)))
    assert bad == [], "spawn python -m trw_mcp.server with env=pinned_server_env(...):\n" + "\n".join(bad)


def test_the_census_flags_a_spawn_without_the_helper() -> None:
    src = 'import subprocess, sys\nsubprocess.run([sys.executable, "-m", "trw_mcp.server", "local"])\n'
    assert unpinned_spawns(src, "s.py") == ["s.py:2"]


def test_the_census_accepts_a_spawn_through_the_helper_and_ignores_config_text() -> None:
    src = (
        "import subprocess, sys\n"
        "from tests._stdio_harness import pinned_server_env\n"
        'subprocess.run([sys.executable, "-m", "trw_mcp.server"], env=pinned_server_env())\n'
        'ARGS = ["-m", "trw_mcp.server"]\n'
    )
    assert unpinned_spawns(src, "s.py") == []


@pytest.mark.integration
def test_a_child_with_the_pinned_environment_imports_this_tree() -> None:
    done = subprocess.run(
        [sys.executable, "-c", "import trw_mcp, trw_memory; print(trw_mcp.__file__); print(trw_memory.__file__)"],
        env=pinned_server_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    for line in done.stdout.split():
        assert Path(line).resolve().is_relative_to(_TREE), f"{line} is outside the tree under test {_TREE}"


def test_the_census_honours_an_explicit_exemption_and_rejects_a_bare_env() -> None:
    exempt = 'import subprocess\nsubprocess.run(["py", "-m", "trw_mcp.server"], env=e)  # spawn-pin: exempt\n'
    bare = 'import subprocess\nsubprocess.run(["py", "-m", "trw_mcp.server"], env=e)\n'
    assert unpinned_spawns(exempt, "s.py") == []
    assert unpinned_spawns(bare, "s.py") == ["s.py:2"]
