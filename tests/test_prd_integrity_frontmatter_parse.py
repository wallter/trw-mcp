"""FR01 (PRD-QUAL-091): malformed-frontmatter is a validation failure.

A PRD whose ``---`` block does not parse to a mapping must produce a
``ValidationFailure`` with rule ``aaref_frontmatter_parse`` rather than
degrading silently to an empty frontmatter (the gate-escape hole).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.validation.prd_integrity import (
    _check_frontmatter_parses,
    run_prd_integrity_checks,
)

# A frontmatter block with a DUPLICATE ``evidence:`` key — the canonical
# acceptance case from the PRD. ruamel's safe loader rejects duplicate keys.
_DUP_KEY_PRD = """---
prd:
  id: PRD-CORE-999
  status: draft
evidence:
  level: strong
evidence:
  level: weak
---

# PRD-CORE-999: duplicate-key frontmatter
Body text.
"""

# Unclosed flow sequence — another unparseable shape.
_BAD_FLOW_PRD = """---
foo: [unclosed
bar: 1
---

# Body
"""

_WELL_FORMED_PRD = """---
prd:
  id: PRD-CORE-001
  status: draft
functionality_level: planned
---

# PRD-CORE-001
Body.
"""

_NO_FRONTMATTER = """# Just a markdown heading

No frontmatter block here at all.
"""


def _rules(failures: list[object]) -> set[str]:
    return {getattr(f, "rule", "") for f in failures}


def test_malformed_duplicate_key_emits_parse_failure() -> None:
    failures = _check_frontmatter_parses(_DUP_KEY_PRD)
    assert "aaref_frontmatter_parse" in _rules(failures)
    assert all(f.severity == "error" for f in failures)
    assert failures[0].field == "frontmatter"


def test_malformed_unclosed_flow_emits_parse_failure() -> None:
    failures = _check_frontmatter_parses(_BAD_FLOW_PRD)
    assert "aaref_frontmatter_parse" in _rules(failures)


def test_well_formed_frontmatter_no_failure() -> None:
    assert _check_frontmatter_parses(_WELL_FORMED_PRD) == []


def test_no_frontmatter_is_not_a_parse_failure() -> None:
    # No ``---`` block at all is a distinct (legitimate) case — not a malformed PRD.
    assert _check_frontmatter_parses(_NO_FRONTMATTER) == []


def test_run_integrity_checks_surfaces_parse_failure(tmp_path: Path) -> None:
    failures, _warnings = run_prd_integrity_checks(
        _DUP_KEY_PRD,
        {},  # parse_frontmatter degraded to {} upstream — the hole this gate plugs
        project_root=tmp_path,
        prds_relative_path="docs/requirements-aare-f/prds",
    )
    assert "aaref_frontmatter_parse" in _rules(failures)


def test_validate_prd_quality_v2_fails_open_on_malformed(tmp_path: Path) -> None:
    """NFR02: a malformed PRD records the failure but scoring STILL returns."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.validation import validate_prd_quality_v2

    result = validate_prd_quality_v2(
        _DUP_KEY_PRD,
        get_config(),
        project_root=str(tmp_path),
    )
    # Did not raise; produced a score; marked invalid; carries the failure rule.
    assert result is not None
    assert result.total_score is not None
    assert result.valid is False
    assert "aaref_frontmatter_parse" in {f.rule for f in result.failures}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
