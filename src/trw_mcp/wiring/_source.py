"""Shared, deterministic source-scanning primitives for the wiring detector.

Two rules encoded here, both measured on this codebase (PRD-CORE-232 §6):

1. **Resolve by fully-qualified import path, never by bare symbol.** Three
   unrelated features here are named ``meta_tune``; a bare-name match reports
   the dead one as live.
2. **Never treat a call-syntax match as the definition of "used".**
   ``server/_tools.py`` registers ~39 tool registrars as *bare function
   references* inside a tuple; a call-syntax matcher scores 47% false
   positives against it. Nothing in this module infers liveness from call
   syntax — the detector observes artifacts, not call graphs.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Directory names never scanned. Mirrors ``code_index.discovery`` defaults plus
# the vendored/worktree paths that would otherwise duplicate every finding.
EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".trw",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "worktrees",
    }
)


def is_test_path(path: Path) -> bool:
    """True for test files and test packages.

    Test-only reachability is not production wiring. The generic dead-code scan
    returned 49 of 51 findings as ``test_only_reference=True``; this predicate
    is why the detector never counts a test as a producer or a consumer.
    """
    parts = path.parts
    if any(part in {"tests", "test", "fixtures", "conftest"} for part in parts):
        return True
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def iter_python_files(root: Path, *, max_bytes: int, include_tests: bool = False) -> Iterator[Path]:
    """Yield ``*.py`` files under ``root`` in sorted (deterministic) order."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if not include_tests and is_test_path(path):
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        yield path


def parse_module(path: Path) -> ast.Module | None:
    """Parse ``path`` into an AST, or return ``None`` when it cannot be parsed.

    Under-claiming is deliberate: an unparseable file yields no findings rather
    than a guessed one.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


@dataclass(frozen=True)
class ImportedSymbol:
    """A name bound in a module, resolved to its fully-qualified origin."""

    local_name: str
    module: str
    qualified: str


def resolve_imports(tree: ast.Module) -> dict[str, ImportedSymbol]:
    """Map each locally-bound name to the fully-qualified symbol it came from.

    Only absolute ``from X import Y`` / ``import X`` forms are resolved.
    Relative imports are skipped: resolving them requires package context the
    detector does not need, and an unresolved callee simply produces no
    finding (``test_dynamic_dispatch_prefers_under_claiming``).
    """
    resolved: dict[str, ImportedSymbol] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                resolved[local] = ImportedSymbol(
                    local_name=local,
                    module=node.module,
                    qualified=f"{node.module}.{alias.name}",
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                resolved[local] = ImportedSymbol(local_name=local, module=alias.name, qualified=alias.name)
    return resolved


def module_path_for(package_root: Path, dotted: str, package_name: str) -> Path | None:
    """Map ``a.b.c`` to ``<package_root>/a/b/c.py`` (or its ``__init__.py``).

    Returns ``None`` for anything outside ``package_name`` — the detector never
    reaches into another distribution's source, which is also what keeps the
    IP boundary intact (NFR05: zero ``trw_distill`` imports).
    """
    if dotted != package_name and not dotted.startswith(f"{package_name}."):
        return None
    relative = Path(*dotted.split("."))
    candidate = package_root / relative.with_suffix(".py")
    if candidate.is_file():
        return candidate
    package_init = package_root / relative / "__init__.py"
    return package_init if package_init.is_file() else None


def function_string_constants(tree: ast.Module) -> dict[str, frozenset[str]]:
    """Map each top-level-or-nested function name to the string literals inside it.

    Used by the ``SCHEMA_DIVERGENCE`` check to test whether *one* function knows
    both the artifact it writes to and the record type it writes — the
    (writer location, record shape, reader location) triple. Comparing field
    names alone would have missed the stop-ceremony defect entirely: the field
    name agreed, the *file* the record landed in did not.
    """
    out: dict[str, frozenset[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        literals = {
            child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        out[node.name] = frozenset(literals)
    return out


def contains_text(path: Path, needle: str, *, max_bytes: int = 4_000_000) -> bool:
    """True when ``needle`` appears in ``path``. Fail-closed on unreadable files."""
    try:
        if path.stat().st_size > max_bytes:
            return False
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
