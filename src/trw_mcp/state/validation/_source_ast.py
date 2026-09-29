"""Deterministic AST source helpers shared by the delivery call-chain verifier.

:mod:`trw_mcp.state.validation.call_chain` needs three primitives: whether a path
is a test, how to parse a module without guessing, and where a dotted name lives
inside one package. They used to sit in ``trw_mcp.wiring._source``; PRD-CORE-313
FR05 moved the wiring detector out of the published wheel (it is a monorepo
development gate, now ``scripts/_wiring``), and these three stay here because a
user's ``trw_deliver`` reaches them through the call-chain check.
``scripts/_wiring/_source.py`` re-exports them, so the detector and the verifier
still agree on one definition of "test path" and "unparseable".
"""

from __future__ import annotations

import ast
from pathlib import Path


def is_test_path(path: Path) -> bool:
    """True for test files and test packages.

    Test-only reachability is not production wiring. The generic dead-code scan
    returned 49 of 51 findings as ``test_only_reference=True``; this predicate
    is why neither the wiring detector nor the call-chain verifier counts a test
    as a producer or a consumer.
    """
    parts = path.parts
    if any(part in {"tests", "test", "fixtures", "conftest"} for part in parts):
        return True
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def parse_module(path: Path) -> ast.Module | None:
    """Parse ``path`` into an AST, or return ``None`` when it cannot be parsed.

    Under-claiming is deliberate: an unparseable file yields no findings rather
    than a guessed one.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


def module_path_for(package_root: Path, dotted: str, package_name: str) -> Path | None:
    """Map ``a.b.c`` to ``<package_root>/a/b/c.py`` (or its ``__init__.py``).

    Returns ``None`` for anything outside ``package_name`` — the scan never
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
