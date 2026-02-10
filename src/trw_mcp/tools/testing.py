"""TRW targeted testing tool — test dependency map, resolution, and strategy.

PRD-QUAL-006: Build test dependency maps, resolve changed files to test
targets, recommend phase-appropriate test strategies, and generate
parallel-safe test invocation commands.
"""

from __future__ import annotations

import ast
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.testing import (
    PHASE_TEST_STRATEGIES,
    TestDependencyMap,
    TestMapping,
    TestResolution,
    TestStrategy,
)
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
)

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()


def _test_map_path() -> Path:
    """Resolve path to .trw/test-map.yaml."""
    trw_dir = resolve_trw_dir()
    return trw_dir / _config.test_map_filename


def generate_test_map(
    src_dir: Path,
    tests_dir: Path,
    src_prefix: str = "trw_mcp",
) -> TestDependencyMap:
    """Generate a test dependency map by scanning source and test files.

    Naming convention:
    - trw_mcp/foo.py -> tests/test_foo.py
    - trw_mcp/tools/bar.py -> tests/test_tools_bar.py
    - trw_mcp/models/baz.py -> tests/test_models.py (if test_models_baz.py doesn't exist)

    Args:
        src_dir: Path to source directory (e.g. trw-mcp/src/trw_mcp/).
        tests_dir: Path to tests directory (e.g. trw-mcp/tests/).
        src_prefix: Source package name for relative path computation.

    Returns:
        TestDependencyMap with all source-to-test mappings.
    """
    mappings: dict[str, TestMapping] = {}

    if not src_dir.exists():
        return TestDependencyMap(
            generated_at=datetime.now(timezone.utc).isoformat(),
            mappings=mappings,
        )

    # Collect all test files for lookup
    test_files: set[str] = set()
    if tests_dir.exists():
        for tf in tests_dir.rglob("*.py"):
            test_files.add(tf.name)

    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue

        # Compute relative path within source package
        try:
            rel = py_file.relative_to(src_dir)
        except ValueError:
            continue

        # Source key: e.g. "trw_mcp/scoring.py"
        source_key = f"{src_prefix}/{rel}"

        # Determine test file name by convention
        test_candidates = _resolve_test_file_names(rel)
        matched_tests: list[str] = []
        for candidate in test_candidates:
            if candidate in test_files:
                matched_tests.append(f"tests/{candidate}")

        # Parse imports
        imports = _extract_imports(py_file, src_prefix)

        mappings[source_key] = TestMapping(
            tests=matched_tests,
            imports=imports,
        )

    return TestDependencyMap(
        generated_at=datetime.now(timezone.utc).isoformat(),
        mappings=mappings,
    )


def _resolve_test_file_names(rel_path: Path) -> list[str]:
    """Generate candidate test file names from a relative source path.

    Args:
        rel_path: Relative path within the source package.

    Returns:
        List of candidate test file names (most specific first).
    """
    parts = list(rel_path.parts)
    stem = rel_path.stem

    if len(parts) == 1:
        # Top-level: foo.py -> test_foo.py
        return [f"test_{stem}.py"]

    # Nested: tools/bar.py -> test_tools_bar.py, then test_tools.py
    subdir = "_".join(parts[:-1])
    candidates = [
        f"test_{subdir}_{stem}.py",
        f"test_{subdir}.py",
    ]
    return candidates


def _extract_imports(
    py_file: Path,
    src_prefix: str,
) -> list[str]:
    """Extract project-internal imports from a Python file.

    Args:
        py_file: Path to the Python source file.
        src_prefix: Package name to filter imports.

    Returns:
        List of imported module paths (e.g. "trw_mcp/models/config.py").
    """
    imports: list[str] = []
    try:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(src_prefix):
                # Convert dotted module to file path
                module_path = node.module.replace(".", "/") + ".py"
                if module_path not in imports:
                    imports.append(module_path)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(src_prefix):
                    module_path = alias.name.replace(".", "/") + ".py"
                    if module_path not in imports:
                        imports.append(module_path)

    return imports


def resolve_targeted_tests(
    changed_files: list[str],
    test_map: TestDependencyMap,
    tests_dir: Path | None = None,
) -> TestResolution:
    """Resolve changed source files to targeted test files.

    Uses BFS on the import graph for transitive dependency resolution.

    Args:
        changed_files: List of changed source file paths (relative to src).
        test_map: Test dependency map.
        tests_dir: Path to tests directory for stale entry detection.

    Returns:
        TestResolution with targeted tests and warnings.
    """
    if not changed_files:
        return TestResolution(
            warnings=["No changed files provided — no tests to run."],
        )

    # If map is empty, fallback
    if not test_map.mappings:
        return TestResolution(
            changed_files=changed_files,
            fallback_used=True,
            warnings=["Test dependency map is empty — run full test suite."],
        )

    targeted_tests: set[str] = set()
    untested: list[str] = []
    stale: list[str] = []
    warnings: list[str] = []

    # Build reverse import graph: module -> list of modules that import it
    reverse_imports: dict[str, list[str]] = {}
    for src_key, mapping in test_map.mappings.items():
        for imp in mapping.imports:
            if imp not in reverse_imports:
                reverse_imports[imp] = []
            reverse_imports[imp].append(src_key)

    # BFS from changed files to find all affected modules
    affected: set[str] = set()
    queue: deque[str] = deque()

    for f in changed_files:
        # Normalize path
        normalized = f.replace("\\", "/")
        if normalized in test_map.mappings:
            queue.append(normalized)
            affected.add(normalized)
        else:
            # Try with src_prefix
            for key in test_map.mappings:
                if key.endswith(normalized) or normalized.endswith(key):
                    queue.append(key)
                    affected.add(key)
                    break
            else:
                untested.append(normalized)

    # BFS transitive dependents
    while queue:
        current = queue.popleft()
        dependents = reverse_imports.get(current, [])
        for dep in dependents:
            if dep not in affected:
                affected.add(dep)
                queue.append(dep)

    # Collect test files for all affected modules
    for module in affected:
        mod_mapping = test_map.mappings.get(module)
        if mod_mapping is not None:
            for test_file in mod_mapping.tests:
                targeted_tests.add(test_file)

    # Check for stale entries
    if tests_dir and tests_dir.exists():
        for test_path in targeted_tests:
            full_path = tests_dir.parent / test_path if not Path(test_path).is_absolute() else Path(test_path)
            if not full_path.exists():
                stale.append(test_path)
                warnings.append(f"Stale test entry: {test_path} does not exist")

    # Remove stale entries from results
    targeted_tests -= set(stale)

    return TestResolution(
        changed_files=changed_files,
        targeted_tests=sorted(targeted_tests),
        untested_files=untested,
        stale_entries=stale,
        fallback_used=False,
        warnings=warnings,
    )


def get_phase_strategy(phase: str) -> TestStrategy:
    """Get the recommended test strategy for a phase.

    Args:
        phase: Phase name (research, plan, implement, validate, review, deliver).

    Returns:
        TestStrategy for the phase. Returns implement strategy as default.
    """
    return PHASE_TEST_STRATEGIES.get(
        phase.lower(),
        PHASE_TEST_STRATEGIES["implement"],
    )


def register_testing_tools(server: FastMCP) -> None:
    """Register testing tools on the MCP server.

    Args:
        server: FastMCP server instance.
    """

    @server.tool()
    def trw_test_target(
        changed_files: list[str] | None = None,
        phase: str | None = None,
        generate_map: bool = False,
        run_id: str | None = None,
    ) -> dict[str, object]:
        """Analyze changes and recommend targeted test subset for efficient validation.

        Args:
            changed_files: List of changed source files. If None, uses git diff.
            phase: Current phase for strategy recommendation. Optional.
            generate_map: If True, regenerate the test dependency map.
            run_id: Optional run ID for parallel-safe test invocation paths.
        """
        project_root = resolve_project_root()
        src_dir = project_root / _config.source_package_path / _config.source_package_name
        tests_dir = project_root / _config.tests_relative_path

        result: dict[str, object] = {}

        # Generate or load test map
        if generate_map or not _reader.exists(_test_map_path()):
            test_map = generate_test_map(src_dir, tests_dir)
            # Persist map
            map_data: dict[str, object] = {
                "version": test_map.version,
                "generated_at": test_map.generated_at,
                "mappings": {
                    k: {"tests": v.tests, "imports": v.imports}
                    for k, v in test_map.mappings.items()
                },
            }
            map_path = _test_map_path()
            map_path.parent.mkdir(parents=True, exist_ok=True)
            _writer.write_yaml(map_path, map_data)
            result["map_generated"] = True
            result["map_entries"] = len(test_map.mappings)
        else:
            # Load existing map
            map_data = _reader.read_yaml(_test_map_path())
            raw_mappings = map_data.get("mappings", {})
            parsed_mappings: dict[str, TestMapping] = {}
            if isinstance(raw_mappings, dict):
                for k, v in raw_mappings.items():
                    if isinstance(v, dict):
                        parsed_mappings[str(k)] = TestMapping(
                            tests=list(v.get("tests", [])),
                            imports=list(v.get("imports", [])),
                        )
            test_map = TestDependencyMap(
                version=int(str(map_data.get("version", 1))),
                generated_at=str(map_data.get("generated_at", "")),
                mappings=parsed_mappings,
            )
            result["map_generated"] = False
            result["map_entries"] = len(test_map.mappings)

        # Resolve targeted tests
        files = changed_files or []
        resolution = resolve_targeted_tests(files, test_map, tests_dir)
        result["resolution"] = {
            "changed_files": resolution.changed_files,
            "targeted_tests": resolution.targeted_tests,
            "untested_files": resolution.untested_files,
            "stale_entries": resolution.stale_entries,
            "fallback_used": resolution.fallback_used,
            "warnings": resolution.warnings,
        }

        # Generate pytest command
        if resolution.targeted_tests:
            test_paths = " ".join(resolution.targeted_tests)
            cmd = f"pytest {test_paths} -v"
            if run_id:
                cmd += f" --basetemp=/tmp/pytest-{run_id}"
                cmd += f" --override-ini=cache_dir=/tmp/pytest-cache-{run_id}"
            result["pytest_command"] = cmd
        elif resolution.fallback_used:
            cmd = "pytest tests/ -v"
            if run_id:
                cmd += f" --basetemp=/tmp/pytest-{run_id}"
            result["pytest_command"] = cmd
        else:
            result["pytest_command"] = None
            result["note"] = "No tests to run for the given changes."

        # Phase strategy
        if phase:
            strategy = get_phase_strategy(phase)
            result["strategy"] = {
                "phase": strategy.phase,
                "recommended_markers": strategy.recommended_markers,
                "run_full_suite": strategy.run_full_suite,
                "run_coverage": strategy.run_coverage,
                "run_mypy": strategy.run_mypy,
                "description": strategy.description,
            }

        # Targeted mypy command
        if files and not resolution.fallback_used:
            mypy_files: set[str] = set()
            for f in files:
                # Add the changed file
                mypy_files.add(f"src/{f}" if not f.startswith("src/") else f)
                # Add direct imports
                mapping = test_map.mappings.get(f)
                if mapping:
                    for imp in mapping.imports:
                        mypy_files.add(f"src/{imp}" if not imp.startswith("src/") else imp)
            if mypy_files:
                result["mypy_command"] = f"mypy {' '.join(sorted(mypy_files))} --strict"

        logger.info(
            "trw_test_target",
            changed_count=len(files),
            targeted_count=len(resolution.targeted_tests),
        )

        return result
