"""Integration validation — tool registration, test coverage, and orphan detection.

Scans tool modules for registration functions, compares against server.py
imports, checks for corresponding test files, and detects orphan modules
not imported by any other production source file (PRD-QUAL-011).
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

logger = structlog.get_logger()

# RC-004: Sprint exit criteria parser
_EXIT_CRITERIA_RE = re.compile(r"^##\s*Exit\s+Criteria", re.IGNORECASE | re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+)$", re.MULTILINE)


def check_integration(source_dir: Path) -> dict[str, object]:
    """Detect unregistered tool modules and missing test files.

    PRD-QUAL-011-FR01/FR02: Scan ``tools/*.py`` for ``register_*_tools``
    definitions, compare against ``server.py`` imports/calls, and check
    for corresponding test files.

    Args:
        source_dir: Root source directory (e.g., ``src/trw_mcp``).

    Returns:
        Dict with keys ``unregistered``, ``missing_tests``, ``conventions``,
        and ``all_registered`` boolean.
    """
    tools_dir = source_dir / "tools"
    server_path = source_dir / "server.py"
    tests_dir = source_dir.parent.parent / "tests"

    unregistered: list[str] = []
    missing_tests: list[str] = []
    registered_funcs: set[str] = set()
    tool_modules: dict[str, str] = {}  # module_name -> register function name

    # Step 1: Scan tool modules for register_*_tools definitions
    if tools_dir.is_dir():
        for tool_file in sorted(tools_dir.glob("*.py")):
            name = tool_file.stem
            if name.startswith("_") or name == "__init__":
                continue
            try:
                content = tool_file.read_text(encoding="utf-8")
            except OSError:
                continue
            match = re.search(r"def (register_\w+_tools)\s*\(", content)
            if match:
                tool_modules[name] = match.group(1)
            # Also check for test file
            test_candidates = [
                tests_dir / f"test_tools_{name}.py",
                tests_dir / f"test_{name}.py",
            ]
            if not any(t.exists() for t in test_candidates):
                missing_tests.append(f"test_tools_{name}.py")

    # Step 2: Parse server.py for imports and registration calls
    if server_path.is_file():
        try:
            server_content = server_path.read_text(encoding="utf-8")
        except OSError:
            server_content = ""

        # Find all import statements: from trw_mcp.tools.X import register_X_tools
        for match in re.finditer(
            r"from\s+trw_mcp\.tools\.(\w+)\s+import\s+(register_\w+_tools)",
            server_content,
        ):
            registered_funcs.add(match.group(2))

        # Also find call sites: register_X_tools(
        for match in re.finditer(
            r"(register_\w+_tools)\s*\(",
            server_content,
        ):
            registered_funcs.add(match.group(1))

    # Step 3: Diff — tool modules with registration functions but not in server.py
    for module_name, func_name in tool_modules.items():
        if func_name not in registered_funcs:
            unregistered.append(module_name)

    return {
        "unregistered": unregistered,
        "missing_tests": missing_tests,
        "all_registered": len(unregistered) == 0,
        "tool_modules_scanned": len(tool_modules),
        "conventions": {
            "tool_pattern": "tools/X.py -> register_X_tools(server) -> import in server.py",
            "test_pattern": "tools/X.py -> tests/test_tools_X.py",
        },
    }


def check_orphan_modules(source_dir: Path) -> dict[str, object]:
    """Detect source modules not imported by any other production module.

    Scans all ``.py`` files under *source_dir* (excluding ``__init__.py``
    and known entry points).  For each, checks whether any other ``.py``
    file in the same source tree contains an import statement referencing
    it.  Modules with zero incoming imports are reported as orphans.

    This catches the "extraction without wiring" anti-pattern where a
    module is created but never connected to the production call graph.
    Tests in ``tests/`` are excluded from the importer set — only
    production source files count.

    Args:
        source_dir: Root source directory (e.g., ``src/trw_mcp``).

    Returns:
        Dict with ``orphans`` list, ``all_reachable`` bool, and
        ``modules_scanned`` count.
    """
    # Entry points that legitimately have no importers
    _ENTRY_POINTS = {"server", "__main__"}

    # Read all source file contents once (excludes __pycache__)
    all_source: list[tuple[Path, str]] = []
    for py_file in sorted(source_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            all_source.append((py_file, py_file.read_text(encoding="utf-8")))
        except OSError:
            continue

    orphans: list[str] = []
    modules_scanned = 0

    # Collect candidate modules: all .py files in subdirectories + top-level
    candidates: list[tuple[Path, str]] = []  # (file_path, relative_display_name)

    # Subdirectory modules (state/, tools/, models/, etc.)
    for subdir in sorted(source_dir.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith("__"):
            continue
        for py_file in sorted(subdir.rglob("*.py")):
            if py_file.name == "__init__.py" or "__pycache__" in str(py_file):
                continue
            rel = py_file.relative_to(source_dir)
            candidates.append((py_file, str(rel)))

    # Top-level modules (scoring.py, etc. — not entry points)
    for py_file in sorted(source_dir.glob("*.py")):
        if py_file.name == "__init__.py" or py_file.stem in _ENTRY_POINTS:
            continue
        candidates.append((py_file, py_file.name))

    for py_file, display_name in candidates:
        modules_scanned += 1
        name = py_file.stem

        # Build regex for import detection.  Matches:
        #   from trw_mcp.state.foo import ...     (absolute)
        #   from .foo import ...                   (relative)
        #   import trw_mcp.state.foo               (full import)
        #   from . import foo                      (package import)
        escaped = re.escape(name)
        import_re = re.compile(
            rf"(?:from\s+\S*\.{escaped}\s+import"
            rf"|import\s+\S*\.{escaped}(?:\s|$|,)"
            rf"|from\s+\.\.?\s+import\s+[^)]*\b{escaped}\b)",
        )

        is_imported = False
        for other_path, content in all_source:
            if other_path == py_file:
                continue
            if import_re.search(content):
                is_imported = True
                break

        if not is_imported:
            orphans.append(display_name)

    if orphans:
        logger.warning(
            "orphan_modules_detected",
            count=len(orphans),
            orphans=orphans,
        )

    return {
        "orphans": orphans,
        "all_reachable": len(orphans) == 0,
        "modules_scanned": modules_scanned,
    }


def parse_exit_criteria(sprint_md: str) -> list[dict[str, object]]:
    """Parse exit criteria checkboxes from a sprint markdown document.

    Extracts ``- [ ]`` (unchecked) and ``- [x]`` (checked) lines from
    the "Exit Criteria" section. Stops at the next ``##`` heading or EOF.

    Args:
        sprint_md: Full sprint markdown content.

    Returns:
        List of dicts with ``text`` (str) and ``checked`` (bool) keys.
    """
    # Find the Exit Criteria section
    match = _EXIT_CRITERIA_RE.search(sprint_md)
    if match is None:
        return []

    # Extract section content until next ## heading or EOF
    start = match.end()
    next_heading = re.search(r"^##\s", sprint_md[start:], re.MULTILINE)
    section = sprint_md[start:start + next_heading.start()] if next_heading else sprint_md[start:]

    criteria: list[dict[str, object]] = []
    for cb_match in _CHECKBOX_RE.finditer(section):
        checked = cb_match.group(1).strip().lower() == "x"
        text = cb_match.group(2).strip()
        criteria.append({"text": text, "checked": checked})

    return criteria
