"""Unit tests for meta-tune Pydantic v2 data models.

PRD-HPO-SAFE-001 §7.3 — field contracts + strict/frozen invariants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models.meta_tune import (
    AuditEntry,
    CandidateEdit,
    PromotionDecision,
    SandboxResult,
)


# --- Fixtures -----------------------------------------------------------------


def _candidate() -> CandidateEdit:
    return CandidateEdit(
        edit_id="11111111-1111-1111-1111-111111111111",
        proposer_id="agent:session-abc",
        target_path=Path("CLAUDE.md"),
        diff="--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ -1,1 +1,1 @@\n-old\n+new\n",
        created_ts=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
    )


def _sandbox_result() -> SandboxResult:
    return SandboxResult(
        edit_id="11111111-1111-1111-1111-111111111111",
        corpus_version="v1",
        seed=42,
        scores={"task-a": 0.9, "task-b": 0.8},
        eval_gaming_flags=[],
        elapsed_ms=1234,
    )


def _promotion_decision() -> PromotionDecision:
    return PromotionDecision(
        edit_id="11111111-1111-1111-1111-111111111111",
        outcome_correlation_ok=True,
        goodhart_ok=True,
        reviewer_id="reviewer@example.com",
        approval_ts=datetime(2026, 4, 17, 12, 5, tzinfo=timezone.utc),
        decision="promoted",
        reason="promoted",
    )


def _audit_entry() -> AuditEntry:
    return AuditEntry(
        edit_id="11111111-1111-1111-1111-111111111111",
        event="proposed",
        payload={"proposer": "agent:session-abc"},
        ts=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        prev_hash="0" * 64,
        entry_hash="a" * 64,
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


def test_sandbox_result_serializable() -> None:
    """SandboxResult round-trips through model_dump / model_validate."""
    r = _sandbox_result()
    dumped = r.model_dump()
    assert dumped["corpus_version"] == "v1"
    assert dumped["seed"] == 42
    assert dumped["scores"]["task-a"] == 0.9
    assert dumped["eval_gaming_flags"] == []
    assert dumped["elapsed_ms"] == 1234

    revived = SandboxResult.model_validate(dumped)
    assert revived == r


def test_audit_entry_hash_fields_present() -> None:
    """AuditEntry exposes prev_hash + entry_hash for chain verification (FR-4)."""
    e = _audit_entry()
    assert e.prev_hash == "0" * 64
    assert e.entry_hash == "a" * 64
    assert e.event == "proposed"
    assert e.edit_id.startswith("11111111")


# --- Strict/frozen invariants -------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [_candidate, _sandbox_result, _promotion_decision, _audit_entry],
)
def test_frozen_models_reject_mutation(factory: object) -> None:
    """Every meta-tune model is frozen: attribute assignment raises."""
    instance = factory()  # type: ignore[operator]
    with pytest.raises(ValidationError):
        instance.edit_id = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("cls", "kwargs"),
    [
        (
            CandidateEdit,
            {
                "edit_id": "x",
                "proposer_id": "y",
                "target_path": Path("a"),
                "diff": "",
                "created_ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "extra_field": "nope",
            },
        ),
        (
            SandboxResult,
            {
                "edit_id": "x",
                "corpus_version": "v1",
                "seed": 1,
                "scores": {},
                "eval_gaming_flags": [],
                "elapsed_ms": 0,
                "extra_field": "nope",
            },
        ),
        (
            PromotionDecision,
            {
                "edit_id": "x",
                "outcome_correlation_ok": True,
                "goodhart_ok": True,
                "decision": "rejected",
                "reason": "nope",
                "extra_field": "nope",
            },
        ),
        (
            AuditEntry,
            {
                "edit_id": "x",
                "event": "proposed",
                "payload": {},
                "ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "prev_hash": "0" * 64,
                "entry_hash": "a" * 64,
                "extra_field": "nope",
            },
        ),
    ],
)
def test_strict_rejects_extras(cls: type, kwargs: dict[str, object]) -> None:
    """strict=True + extra=forbid blocks unknown fields from all 4 models."""
    with pytest.raises(ValidationError):
        cls(**kwargs)


# --- PromotionDecision nuances ------------------------------------------------


def test_promotion_decision_allows_null_reviewer_for_queued_state() -> None:
    """A decision awaiting review has reviewer_id=None + approval_ts=None."""
    d = PromotionDecision(
        edit_id="x",
        outcome_correlation_ok=True,
        goodhart_ok=True,
        reviewer_id=None,
        approval_ts=None,
        decision="rejected",
        reason="awaiting-review",
    )
    assert d.reviewer_id is None
    assert d.approval_ts is None


def test_promotion_decision_rejects_invalid_decision_literal() -> None:
    """decision field only accepts 'promoted' | 'rejected'."""
    with pytest.raises(ValidationError):
        PromotionDecision(
            edit_id="x",
            outcome_correlation_ok=True,
            goodhart_ok=True,
            decision="queued",  # type: ignore[arg-type]
            reason="awaiting-review",
        )


def test_audit_entry_rejects_unknown_event_literal() -> None:
    """event field is restricted to the 5 lifecycle events."""
    with pytest.raises(ValidationError):
        AuditEntry(
            edit_id="x",
            event="sandboxing",  # type: ignore[arg-type]
            payload={},
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
            prev_hash="0" * 64,
            entry_hash="a" * 64,
        )
