"""Focused regression guards for PRD-FIX-062 exception policy."""

from __future__ import annotations

import ast
from pathlib import Path


_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
_POLICY_SCOPE = (
    _SRC_ROOT / "sync" / "pull.py",
    _SRC_ROOT / "sync" / "push.py",
    _SRC_ROOT / "bootstrap" / "_update_project.py",
)


def test_scope_except_exception_handlers_are_annotated() -> None:
    """Guard against reintroducing unjustified broad catches in reviewed scope."""
    violations: list[str] = []

    for path in _POLICY_SCOPE:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "except Exception" not in line:
                continue
            if "# justified:" in line or "# per-item" in line:
                continue
            violations.append(f"{path.relative_to(_SRC_ROOT.parent)}:{lineno}: {line.strip()}")

    assert not violations, "Unjustified except Exception handlers found:\n" + "\n".join(violations)


def test_scope_exception_handlers_do_not_use_bare_pass() -> None:
    """Guard against bare ``except Exception: pass`` in the reviewed scope."""
    violations: list[str] = []

    for path in _POLICY_SCOPE:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not isinstance(node.type, ast.Name) or node.type.id != "Exception":
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                violations.append(f"{path.relative_to(_SRC_ROOT.parent)}:{node.lineno}")

    assert not violations, "Bare except Exception: pass handlers found:\n" + "\n".join(violations)
