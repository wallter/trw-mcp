"""DEDICATED test enforcing P0-12: applyTo must use directory minimatch globs.

This is a separate, focused test file per the PRD deliverables requirement.
Tests that compute_apply_to_glob NEVER emits literal file paths.

PRD-DIST-2406 FR08, P0-12.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def compute_glob() -> type:
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    return compute_apply_to_glob  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# P0-12 enforcement: no literal filenames in applyTo
# ---------------------------------------------------------------------------


def test_no_literal_py_filenames_in_glob(compute_glob: object) -> None:
    """No literal .py file names appear in the glob output."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    hotspot_files = [
        "trw-mcp/src/trw_mcp/state/ceremony.py",
        "backend/routers/admin.py",
        "trw-memory/src/trw_memory/models/memory.py",
    ]
    result = compute_apply_to_glob(hotspot_files)

    # These literal filenames must NOT appear
    assert "ceremony.py" not in result
    assert "admin.py" not in result
    assert "memory.py" not in result


def test_glob_contains_directory_wildcard() -> None:
    """Each glob component contains a directory-level wildcard pattern."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    hotspot_files = [
        "backend/routers/admin.py",
        "trw-mcp/src/trw_mcp/tools/build.py",
    ]
    result = compute_apply_to_glob(hotspot_files)

    for part in result.split(","):
        part = part.strip()
        # Must contain **/ or ** pattern (minimatch directory glob)
        assert "**" in part, f"Expected ** in glob part: {part!r}"


def test_glob_preserves_directory_structure() -> None:
    """Directory part of path is preserved in glob output."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    result = compute_apply_to_glob(["backend/routers/admin.py"])

    # The directory 'backend/routers' should appear
    assert "backend/routers" in result


def test_single_file_produces_directory_glob() -> None:
    """Single hotspot file produces directory-level glob."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    result = compute_apply_to_glob(["trw-mcp/src/trw_mcp/state/ceremony.py"])

    assert "ceremony.py" not in result
    assert "trw-mcp/src/trw_mcp/state" in result
    assert "**" in result


def test_top_level_file_produces_wildcard_glob() -> None:
    """Top-level file (no directory) produces wildcard glob."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    result = compute_apply_to_glob(["main.py"])

    assert "main.py" not in result
    assert "**" in result


def test_prds_acceptance_example() -> None:
    """Verify the exact acceptance criteria example from the PRD (FR08)."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    # PRD says:
    # 'trw-mcp/src/trw_mcp/state/ceremony.py' -> 'trw-mcp/src/trw_mcp/state/**/*.py'
    # 'backend/routers/admin.py' -> 'backend/routers/**/*.py'
    hotspot_files = [
        "trw-mcp/src/trw_mcp/state/ceremony.py",
        "backend/routers/admin.py",
        "trw-memory/src/trw_memory/models/memory.py",
    ]
    result = compute_apply_to_glob(hotspot_files)

    assert "trw-mcp/src/trw_mcp/state" in result
    assert "backend/routers" in result
    assert "trw-memory/src/trw_memory/models" in result

    # None of the filenames should be literal
    assert "ceremony.py" not in result
    assert "admin.py" not in result
    assert "memory.py" not in result


def test_deduplication_of_same_directory() -> None:
    """Multiple files in same directory produce single directory glob."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    hotspot_files = [
        "backend/routers/admin.py",
        "backend/routers/users.py",
    ]
    result = compute_apply_to_glob(hotspot_files)
    parts = result.split(",")

    # Should deduplicate to a single glob for backend/routers
    assert len(parts) == 1
    assert "backend/routers" in parts[0]
