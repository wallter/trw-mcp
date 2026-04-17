from __future__ import annotations

from trw_mcp.state.validation import score_traceability_v2


def _make_prd_with_matrix(row: str) -> str:
    return (
        "---\n"
        "prd:\n"
        "  id: PRD-CORE-999\n"
        "  title: Traceability Ref Test\n"
        "  version: '1.0'\n"
        "  status: draft\n"
        "  priority: P1\n"
        "traceability:\n"
        '  implements: ["REQ-001"]\n'
        '  depends_on: ["PRD-CORE-074"]\n'
        '  enables: ["PRD-CORE-097"]\n'
        "---\n\n"
        "## 12. Traceability Matrix\n\n"
        "| Requirement | Source | Implementation | Test | Status |\n"
        "|-------------|--------|----------------|------|--------|\n"
        f"{row}"
    )


def test_traceability_counts_hyphenated_repo_paths() -> None:
    prd = _make_prd_with_matrix(
        "| FR01 | user request | `trw-mcp/src/trw_mcp/bootstrap/_opencode.py` | `trw-mcp/tests/test_bootstrap.py` | Pending |\n"
    )
    result = score_traceability_v2(
        {
            "traceability": {
                "implements": ["REQ-001"],
                "depends_on": ["PRD-CORE-074"],
                "enables": ["PRD-CORE-097"],
            }
        },
        prd,
    )
    assert result.details["matrix_score"] > 0.0


def test_traceability_counts_shell_script_impl_refs() -> None:
    prd = _make_prd_with_matrix(
        "| FR01 | compatibility policy | `scripts/check-bundle-sync.sh` | `trw-mcp/tests/test_prd_traceability_refs.py` | Pending |\n"
    )
    result = score_traceability_v2(
        {
            "traceability": {
                "implements": ["REQ-001"],
                "depends_on": ["PRD-CORE-084"],
                "enables": ["PRD-CORE-112"],
            }
        },
        prd,
    )
    assert result.details["matrix_score"] > 0.0
