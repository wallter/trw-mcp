"""N1 grep gate: every write of a run's meta/run.yaml goes through state/_run_yaml_update.py.

Before N1 about ten writers read, edited and rewrote run.yaml with no lock, so two
concurrent writers could each drop the other's key. The locked primitive fixes
that only if nobody bypasses it, so this gate fails on any ``write_yaml(...)``
elsewhere whose target is a run.yaml path. The target counts as a run.yaml path
if it is a literal ``"run.yaml"`` expression, a ``run_yaml_path(...)`` call, a
local name assigned from one of those, or a parameter whose name contains
``run_yaml``. A path reaching a writer under another parameter name is not seen.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trw_mcp

_PRIMITIVE = "state/_run_yaml_update.py"


def _mentions_run_yaml(node: ast.AST) -> bool:
    return any(
        (isinstance(sub, ast.Constant) and sub.value == "run.yaml")
        or (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "run_yaml_path")
        for sub in ast.walk(node)
    )


def _run_yaml_writes(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        tainted = {
            target.id
            for assign in ast.walk(func)
            if isinstance(assign, ast.Assign) and _mentions_run_yaml(assign.value)
            for target in assign.targets
            if isinstance(target, ast.Name)
        }
        # A path handed in as an argument is tainted by its name (C review SF2).
        args = func.args
        tainted |= {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs] if "run_yaml" in a.arg.lower()}
        for call in ast.walk(func):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "write_yaml"
                and call.args
            ):
                target = call.args[0]
                if _mentions_run_yaml(target) or (isinstance(target, ast.Name) and target.id in tainted):
                    lines.append(call.lineno)
    return sorted(set(lines))


def test_run_yaml_is_written_only_through_the_locked_primitive() -> None:
    root = Path(trw_mcp.__file__).parent
    offenders = {
        str(path.relative_to(root)): found
        for path in sorted(root.rglob("*.py"))
        if str(path.relative_to(root)) != _PRIMITIVE
        and (found := _run_yaml_writes(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert not offenders, (
        f"run.yaml written outside {_PRIMITIVE}: {offenders}. "
        "Use update_run_yaml (edit in place under the lock) or create_run_yaml (exclusive create)."
    )


def test_the_gate_catches_a_direct_writer() -> None:
    """Negative control: the detector must flag the shapes that were removed."""
    literal = 'def f(w, run_dir):\n    w.write_yaml(run_dir / "meta" / "run.yaml", {})\n'
    via_name = 'def g(w, run_dir):\n    p = run_dir / "meta" / "run.yaml"\n    w.write_yaml(p, {})\n'
    other_file = 'def h(w, d):\n    w.write_yaml(d / "installer-meta.yaml", {})\n'
    via_parameter = "def k(w, run_yaml, data):\n    w.write_yaml(run_yaml, data)\n"
    assert _run_yaml_writes(ast.parse(literal)) == [2]
    assert _run_yaml_writes(ast.parse(via_name)) == [3]
    assert _run_yaml_writes(ast.parse(other_file)) == []
    assert _run_yaml_writes(ast.parse(via_parameter)) == [2]
