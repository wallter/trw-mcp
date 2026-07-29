"""Regression: the two false-positive sets that sink a naive implementation.

Both are measured, not hypothetical. The naive version of the 2026-06-04 RCA's
own top fix scored a **47% false-positive rate** on the registrar set, and
trw-distill's generic dead-code scan returned 49 of 51 findings as test-only
helpers. A detector that cries wolf is disabled inside a week, so these sets are
pinned permanently.
"""

from __future__ import annotations

import ast
from pathlib import Path

from trw_mcp.wiring.detector import DetectorResult

# TOOL-LIVENESS-2026-07-24.md lines 126-140: 10 tools classified LIVE_NON_MCP.
# 8 of the 10 are reached only through SKILL.md files and agent frontmatter
# ``tools:`` lists; a checker that scans Python and shell but not those surfaces
# produces a 30%+ false-dead rate.
LIVE_NON_MCP_TOOLS: tuple[str, ...] = (
    "trw_code_search",
    "trw_code_symbol",
    "trw_code_index_update",
    "trw_submit_feedback",
    "trw_delivery_status",
    "trw_pipeline_health",
    "trw_dispatch",
    "trw_dispatch_status",
    "trw_channel_stats",
    "trw_mcp_security_status",
)

# Deliberate test-reset helpers the generic scan reported as dead.
TEST_RESET_HELPERS: tuple[str, ...] = (
    "reset_state",
    "_reset_session_id",
    "_reset_yaml_path_index",
    "reset_external_backends",
)


def _registrar_names(repo_root: Path) -> tuple[str, ...]:
    """Every registrar imported inside ``_tool_registrars()``.

    Read out of the source rather than hardcoded, so the fixture cannot drift
    away from the surface it is protecting.
    """
    source = repo_root / "trw-mcp/src/trw_mcp/server/_tools.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_tool_registrars":
            return tuple(
                sorted(
                    alias.name for child in ast.walk(node) if isinstance(child, ast.ImportFrom) for alias in child.names
                )
            )
    raise AssertionError("_tool_registrars() not found — the false-positive fixture has drifted")


def test_tuple_registered_registrars_not_flagged(live_result: DetectorResult, repo_root: Path) -> None:
    """The ~39 registrars wired via a TUPLE OF BARE FUNCTION REFERENCES stay unflagged.

    ``server/_tools.py`` returns ``(register_a, register_b, ...)`` with no call
    parens and invokes them via ``for registrar in _tool_registrars()``.
    Registration is by symbol *reference*, so a call-syntax matcher misses every
    single one and reports 35 of 74 as uncalled. This detector cannot make that
    mistake structurally — it never asks "is this called?" — and this test pins
    that property against a future contributor adding a call-graph shortcut.
    """
    registrars = _registrar_names(repo_root)
    assert len(registrars) >= 30, f"expected the full registrar tuple, got {len(registrars)}"
    rendered = "\n".join(finding.render() for finding in live_result.findings)
    flagged = [name for name in registrars if name in rendered]
    assert not flagged, f"registrar false positives: {flagged}"


def test_ten_live_non_mcp_tools_not_flagged(live_result: DetectorResult) -> None:
    """Zero findings against the 10 tools confirmed reachable via non-MCP paths."""
    rendered = "\n".join(finding.render() for finding in live_result.findings)
    flagged = [tool for tool in LIVE_NON_MCP_TOOLS if tool in rendered]
    assert not flagged, f"LIVE_NON_MCP false positives: {flagged}"


def test_test_reset_helpers_not_flagged(live_result: DetectorResult) -> None:
    """Deliberate test-reset helpers are not findings — test-only is not dead."""
    rendered = "\n".join(finding.render() for finding in live_result.findings)
    flagged = [helper for helper in TEST_RESET_HELPERS if helper in rendered]
    assert not flagged, f"test-helper false positives: {flagged}"


def test_re_export_does_not_count_as_invocation(repo_root: Path) -> None:
    """A re-export keeps a symbol *referenced* while it stays *uninvoked*.

    ``cold_start_seed`` is re-exported from its own module's ``__all__`` and
    imported by tests. Neither makes it wired. The producer check requires a
    non-test file OTHER than the defining module, resolved by module token — so
    the ``__all__`` entry and the test imports are both correctly ignored.
    """
    from trw_mcp.wiring._source import contains_text
    from trw_mcp.wiring.checks.existence import _iter_repo_sources

    candidates = [
        path
        for path in _iter_repo_sources(repo_root, "trw-distill")
        if contains_text(path, "cold_start_seed") and contains_text(path, "installer_hook")
    ]
    # The defining module re-exports the symbol in __all__; it must not be
    # counted as its own caller.
    assert any(path.stem == "installer_hook" for path in candidates), "fixture drift: defining module not found"
    non_defining = [path for path in candidates if path.stem != "installer_hook"]
    # Real callers today live in the distill-seed CLI command; test files are
    # excluded by the scanner, which is the property under test.
    assert all("test" not in path.name for path in non_defining), f"test file counted as a caller: {non_defining}"
