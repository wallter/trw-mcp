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
_TOOLS_DIR = _SRC_ROOT / "tools"

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

    assert not violations, f"Layer violation: state/ imports from tools/ ({len(violations)} violations):\n" + "\n".join(
        violations
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


# --- FIX-061 regression: truncate_nudge_line must live in state layer ---


@pytest.mark.unit
def test_truncate_nudge_line_in_state_layer() -> None:
    """truncate_nudge_line is defined in state and re-exported from tools."""
    from trw_mcp.state._helpers import truncate_nudge_line
    from trw_mcp.tools._learning_helpers import truncate_nudge_line as tools_truncate_nudge_line

    assert truncate_nudge_line is tools_truncate_nudge_line
    assert truncate_nudge_line("Recurring impl gap: wiring missing") == ("Recurring impl gap: wiring missing")


# --- FR02: _merge_session_events lives in state layer ---


@pytest.mark.unit
def test_merge_session_events_in_state_layer() -> None:
    """_merge_session_events must be importable from state._session_events."""
    from trw_mcp.state._session_events import _merge_session_events

    assert callable(_merge_session_events)


@pytest.mark.unit
def test_detect_audit_finding_recurrence_lives_in_helper_module() -> None:
    """Audit-pattern promotion logic lives outside _cycle.py for module-size hygiene."""
    from trw_mcp.state.consolidation import detect_audit_finding_recurrence

    assert detect_audit_finding_recurrence.__module__ == ("trw_mcp.state.consolidation._audit_patterns")


@pytest.mark.unit
def test_consolidation_cycle_module_under_500_lines() -> None:
    """_cycle.py stays below the review threshold after FIX-061 refactor."""
    cycle_src = _STATE_DIR / "consolidation" / "_cycle.py"
    line_count = len(cycle_src.read_text(encoding="utf-8").splitlines())
    assert line_count < 500, f"_cycle.py is {line_count} lines, should be < 500"


@pytest.mark.unit
def test_scoring_correlation_module_under_500_lines() -> None:
    """_correlation.py stays below the review threshold after FIX-061 refactor."""
    correlation_src = _SCORING_DIR / "_correlation.py"
    line_count = len(correlation_src.read_text(encoding="utf-8").splitlines())
    assert line_count < 500, f"_correlation.py is {line_count} lines, should be < 500"


@pytest.mark.unit
def test_deferred_steps_learning_module_under_500_lines() -> None:
    """_deferred_steps_learning.py stays below the review threshold after FIX-061 refactor."""
    learning_src = _TOOLS_DIR / "_deferred_steps_learning.py"
    line_count = len(learning_src.read_text(encoding="utf-8").splitlines())
    assert line_count < 500, f"_deferred_steps_learning.py is {line_count} lines, should be < 500"


@pytest.mark.unit
def test_rework_metrics_helper_lives_outside_deferred_steps_learning() -> None:
    """Audit rework metric parsing stays extracted for module-size hygiene."""
    from trw_mcp.tools._deferred_steps_learning import _step_collect_rework_metrics

    assert _step_collect_rework_metrics.__module__ == ("trw_mcp.tools._deferred_learning_rework")


# --- FR05-T06: correlation accepts injected finder (no hard-coded state imports) ---


@pytest.mark.unit
def test_correlation_accepts_finder_arg() -> None:
    """process_outcome accepts a custom lookup_fn — no hard-coded state imports in _correlation.py.

    Verifies FR05: scoring/_correlation.py has zero from trw_mcp.state.analytics
    and from trw_mcp.state.memory_adapter imports.
    """
    correlation_src = _SCORING_DIR / "_correlation.py"
    content = correlation_src.read_text(encoding="utf-8")
    assert "from trw_mcp.state.analytics" not in content, (
        "_correlation.py imports from state.analytics (FR05 violation)"
    )
    assert "from trw_mcp.state.memory_adapter" not in content, (
        "_correlation.py imports from state.memory_adapter (FR05 violation)"
    )
    # Verify the lookup_fn parameter exists (dependency injection in place)
    assert "lookup_fn" in content, "_correlation.process_outcome must have a lookup_fn parameter"


# --- FR06-T07: decay accepts entry iterator (no file I/O imports in _decay.py) ---


@pytest.mark.unit
def test_decay_accepts_entry_iterator() -> None:
    """_decay.py has zero iter_yaml_entry_files and FileStateReader imports.

    Verifies FR06: compute_impact_distribution delegates I/O to _io_boundary.
    """
    decay_src = _SCORING_DIR / "_decay.py"
    content = decay_src.read_text(encoding="utf-8")
    assert "iter_yaml_entry_files" not in content, "_decay.py still references iter_yaml_entry_files (FR06 violation)"
    assert "FileStateReader" not in content, "_decay.py still references FileStateReader (FR06 violation)"
    # Verify _load_entries_from_dir is still accessible (re-exported from _io_boundary)
    from trw_mcp.scoring._decay import _load_entries_from_dir

    assert callable(_load_entries_from_dir)


# --- FR03/FR05/FR06-T08: scoring computation modules have no direct state I/O imports ---


@pytest.mark.unit
def test_scoring_no_direct_state_io_imports() -> None:
    """The pure scoring modules (_correlation.py, _decay.py) have no state.persistence
    or state.memory_adapter imports — all I/O goes through _io_boundary.py.

    _io_boundary.py is intentionally excluded as it IS the boundary module.
    _utils.py is excluded as it only imports safe_float/safe_int from state._helpers
    (pure helpers, not I/O primitives).
    """
    # Modules that must not have direct state I/O imports
    io_violation_modules = ["_correlation.py", "_decay.py"]
    forbidden_patterns = [
        "from trw_mcp.state.persistence",
        "from trw_mcp.state.memory_adapter",
    ]

    violations: list[str] = []
    for module_name in io_violation_modules:
        py_file = _SCORING_DIR / module_name
        if not py_file.exists():
            continue
        content = py_file.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if pattern in content:
                violations.append(f"{module_name}: contains '{pattern}'")

    assert not violations, "scoring/ I/O layer violations (FR03/FR05/FR06):\n" + "\n".join(violations)
