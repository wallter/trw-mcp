"""CI-enforced layer boundary tests (PRD-FIX-061-FR07).

These tests verify that the documented architecture invariants hold:
  - state/ modules NEVER import from tools/
  - scoring/_utils.py __all__ excludes I/O primitives
  - scoring/ modules don't re-export state-layer I/O in their public API

These tests exist to prevent regression after PRD-FIX-061 layer violation
resolution. If a test fails, the failing import path must be resolved
before merge.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Root of the trw-mcp source tree
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"
_STATE_DIR = _SRC_ROOT / "state"
_SCORING_DIR = _SRC_ROOT / "scoring"

# Pattern matches "from trw_mcp.tools" or "import trw_mcp.tools" but not
# inside comments or string literals (simple heuristic: line must not start
# with '#' after stripping whitespace).
_TOOLS_IMPORT_RE = re.compile(r"^\s*(?:from\s+trw_mcp\.tools|import\s+trw_mcp\.tools)")


# --- FR07-T05: state/ must not import from tools/ ---


@pytest.mark.unit
def test_state_does_not_import_tools() -> None:
    """No state/ .py file should contain 'from trw_mcp.tools' imports.

    Walks all Python files under src/trw_mcp/state/ and checks that
    none of them import from tools/. Comments referencing tools/ are
    allowed (they describe intent, not actual imports).
    """
    violations: list[str] = []
    for py_file in sorted(_STATE_DIR.rglob("*.py")):
        rel = py_file.relative_to(_SRC_ROOT)
        for i, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # Skip comment lines
            if _TOOLS_IMPORT_RE.match(line):
                violations.append(f"{rel}:{i}: {line.strip()}")

    assert not violations, (
        f"Layer violation: state/ imports from tools/ ({len(violations)} violations):\n"
        + "\n".join(violations)
    )


# --- FR03-T04: scoring/_utils.py __all__ must not contain I/O primitives ---


@pytest.mark.unit
def test_scoring_utils_no_io_reexports() -> None:
    """scoring/_utils.py __all__ must not export FileStateReader/FileStateWriter."""
    from trw_mcp.scoring import _utils

    all_names = getattr(_utils, "__all__", [])
    io_primitives = {"FileStateReader", "FileStateWriter", "resolve_trw_dir"}
    leaked = io_primitives & set(all_names)
    assert not leaked, f"scoring/_utils.py __all__ contains I/O primitives: {leaked}"


# --- FR01: is_noise_summary lives in state layer ---


@pytest.mark.unit
def test_is_noise_summary_in_state_layer() -> None:
    """is_noise_summary must be importable from state._helpers or state.analytics."""
    from trw_mcp.state.analytics.core import is_noise_summary

    assert callable(is_noise_summary)
    assert is_noise_summary("Repeated operation: foo") is True
    assert is_noise_summary("Real learning about caching") is False


# --- FR02: _merge_session_events lives in state layer ---


@pytest.mark.unit
def test_merge_session_events_in_state_layer() -> None:
    """_merge_session_events must be importable from state._session_events."""
    from trw_mcp.state._session_events import _merge_session_events

    assert callable(_merge_session_events)
