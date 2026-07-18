"""FPI #7 documented-exception tests (PRD-CORE-213 residual fix).

docs/requirements-aare-f/CLAUDE.md permits status=implemented with
functionality_level=partial WHEN implementation_scope names the deferred
paths AND stubs[] enumerates them. The validator previously hard-failed
every non-live combination, punishing truthful partial claims.
"""

from __future__ import annotations

from trw_mcp.state.validation.prd_integrity import (
    _check_functionality_level_matches_status,
)


def _rules(frontmatter: dict[str, object]) -> set[str]:
    return {f.rule for f in _check_functionality_level_matches_status(frontmatter)}


_STUB_ENTRY = [
    {
        "id": "STUB-01",
        "location": "trw-mcp/src/trw_mcp/tools/orchestration.py",
        "description": "base_commit recording deferred (other-workstream ownership)",
        "activation_gate": "FR08 lands",
        "upgraded_by": "PRD-CORE-213",
    }
]


def test_implemented_partial_with_scope_and_stubs_is_permitted() -> None:
    frontmatter: dict[str, object] = {
        "status": "implemented",
        "functionality_level": "partial",
        "implementation_scope": "FR08 deferred: base_commit recording at trw_init.",
        "stubs": _STUB_ENTRY,
    }
    assert "aaref_implemented_requires_live" not in _rules(frontmatter)


def test_implemented_partial_without_scope_still_fails() -> None:
    frontmatter: dict[str, object] = {
        "status": "implemented",
        "functionality_level": "partial",
        "stubs": _STUB_ENTRY,
    }
    assert "aaref_implemented_requires_live" in _rules(frontmatter)


def test_implemented_partial_without_stubs_still_fails() -> None:
    frontmatter: dict[str, object] = {
        "status": "implemented",
        "functionality_level": "partial",
        "implementation_scope": "FR08 deferred.",
        "stubs": [],
    }
    # Fails BOTH the requires-live rule (no enumerated stubs) and the
    # non-live-requires-stubs rule further down.
    assert "aaref_implemented_requires_live" in _rules(frontmatter)


def test_implemented_stub_never_gets_the_exception() -> None:
    frontmatter: dict[str, object] = {
        "status": "implemented",
        "functionality_level": "stub",
        "implementation_scope": "scaffold only",
        "stubs": _STUB_ENTRY,
    }
    assert "aaref_implemented_requires_live" in _rules(frontmatter)


def test_implemented_live_clean_still_passes() -> None:
    frontmatter: dict[str, object] = {
        "status": "implemented",
        "functionality_level": "live",
        "stubs": [],
    }
    assert _rules(frontmatter) == set()
