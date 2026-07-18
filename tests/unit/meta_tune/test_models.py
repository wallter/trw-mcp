"""Unit tests for the meta-tune CandidateEdit model.

PRD-HPO-SAFE-001 §7.3 — field contracts + strict/frozen invariants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models.meta_tune import CandidateEdit

# --- Fixtures -----------------------------------------------------------------


def _candidate() -> CandidateEdit:
    return CandidateEdit(
        edit_id="11111111-1111-1111-1111-111111111111",
        proposer_id="agent:session-abc",
        target_path=Path("CLAUDE.md"),
        diff="--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ -1,1 +1,1 @@\n-old\n+new\n",
        created_ts=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
    )


# --- FR-exec-plan: required fields --------------------------------------------


def test_candidate_edit_required_fields() -> None:
    """CandidateEdit rejects instances missing any of the 5 §7.3 fields."""
    with pytest.raises(ValidationError):
        CandidateEdit()  # type: ignore[call-arg]

    edit = _candidate()
    assert edit.edit_id.startswith("11111111")
    assert edit.proposer_id == "agent:session-abc"
    assert edit.target_path == Path("CLAUDE.md")
    assert edit.diff.startswith("--- a/CLAUDE.md")
    assert edit.created_ts.tzinfo is timezone.utc


# --- Strict/frozen invariants -------------------------------------------------


def test_candidate_edit_rejects_mutation() -> None:
    """CandidateEdit is frozen: attribute assignment raises."""
    instance = _candidate()
    with pytest.raises(ValidationError):
        instance.edit_id = "mutated"


def test_candidate_edit_rejects_extras() -> None:
    """CandidateEdit rejects fields outside its proposal contract."""
    with pytest.raises(ValidationError):
        CandidateEdit(
            edit_id="x",
            proposer_id="y",
            target_path=Path("a"),
            diff="",
            created_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
            extra_field="nope",  # type: ignore[call-arg]
        )
