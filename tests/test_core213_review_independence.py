"""PRD-CORE-213 FR01-FR03 — reviewer provenance stamp + independence classifier.

FR01: manual review stamps a ``reviewer`` provenance block (source derived from
mode); operator source requires a receipt.
FR02: ``classify_review_independence`` distinguishes same-session self-review from
independent review from unknown.
FR03: ``review_receipt_satisfied`` caps a P0/P1 same-session self-review.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._review_provenance import (
    RunIdentity,
    build_reviewer_block,
    classify_review_independence,
    derive_reviewer_source,
    review_receipt_satisfied,
)


def _make_run(tmp_path: Path, writer: FileStateWriter, *, run_id: str, session_id: str | None) -> Path:
    run_dir = tmp_path / "task" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    payload: dict[str, object] = {"run_id": run_id, "task": "task", "status": "active"}
    if session_id is not None:
        payload["owner_session_id"] = session_id
    writer.write_yaml(meta / "run.yaml", payload)
    writer.append_jsonl(meta / "events.jsonl", {"ts": "2026-07-10T00:00:00Z", "event": "run_init"})
    return run_dir


# --------------------------------------------------------------------------- #
# FR01 — provenance stamp
# --------------------------------------------------------------------------- #


def test_manual_defaults_to_self(tmp_path: Path, writer: FileStateWriter, reader: FileStateReader) -> None:
    from trw_mcp.tools._review_manual import handle_manual_mode

    run_dir = _make_run(tmp_path, writer, run_id="R1", session_id="S1")
    handle_manual_mode(
        [{"category": "security", "severity": "high", "description": "sql injection"}],
        run_dir,
        "review-abcd",
        "2026-07-10T01:00:00Z",
    )
    review = reader.read_yaml(run_dir / "meta" / "review.yaml")
    assert review["reviewer"]["source"] == "self"  # type: ignore[index]
    assert review["reviewer"]["run_id"] == "R1"  # type: ignore[index]
    assert review["reviewer"]["session_id"] == "S1"  # type: ignore[index]
    assert "receipt_id" not in review["reviewer"]  # type: ignore[operator]


def test_manual_result_carries_reviewer_block(tmp_path: Path, writer: FileStateWriter) -> None:
    from trw_mcp.tools._review_manual import handle_manual_mode

    run_dir = _make_run(tmp_path, writer, run_id="R1", session_id="S1")
    result = handle_manual_mode([], run_dir, "review-abcd", "2026-07-10T01:00:00Z")
    assert result["reviewer"]["source"] == "self"  # type: ignore[index]
    assert result["reviewer"]["run_id"] == "R1"  # type: ignore[index]


def test_reviewer_run_id_empty_when_no_active_run() -> None:
    block = build_reviewer_block(None, None, source="self", ts="2026-07-10T00:00:00Z")
    assert block["run_id"] == ""
    assert block["session_id"] == ""
    assert block["source"] == "self"


def test_derive_source_from_mode() -> None:
    assert derive_reviewer_source("manual", None) == "self"
    assert derive_reviewer_source("auto", None) == "subagent"
    assert derive_reviewer_source("cross_model", None) == "cross_model"
    assert derive_reviewer_source("reconcile", None) == "self"


def test_explicit_source_overrides_mode() -> None:
    assert derive_reviewer_source("manual", "operator") == "operator"
    assert derive_reviewer_source("auto", "self") == "self"


def test_invalid_explicit_source_raises() -> None:
    with pytest.raises(ValueError):
        derive_reviewer_source("manual", "bogus")


def test_operator_without_receipt_raises() -> None:
    # FR01: an explicit operator source requires a non-empty receipt_id.
    with pytest.raises(ValueError):
        build_reviewer_block(None, None, source="operator", receipt_id=None)
    with pytest.raises(ValueError):
        build_reviewer_block(None, None, source="operator", receipt_id="   ")


def test_operator_with_receipt_stamps_receipt_id() -> None:
    block = build_reviewer_block(None, None, source="operator", receipt_id="OP-TOKEN-1")
    assert block["receipt_id"] == "OP-TOKEN-1"
    assert block["source"] == "operator"


def test_subagent_auto_generates_receipt_id() -> None:
    block = build_reviewer_block(None, None, source="subagent")
    assert str(block["receipt_id"])  # non-empty token


def test_operator_review_via_manual_handler_raises(tmp_path: Path, writer: FileStateWriter) -> None:
    from trw_mcp.tools._review_manual import handle_manual_mode

    run_dir = _make_run(tmp_path, writer, run_id="R1", session_id="S1")
    with pytest.raises(ValueError):
        handle_manual_mode([], run_dir, "rid", "2026-07-10T01:00:00Z", reviewer_source="operator")


# --------------------------------------------------------------------------- #
# FR02 — independence classification
# --------------------------------------------------------------------------- #


def test_same_run_is_self() -> None:
    review = {"reviewer": {"source": "self", "run_id": "R1", "session_id": "S1"}}
    assert classify_review_independence(review, RunIdentity(run_id="R1", session_id="S1")) == "self_same_session"


def test_different_run_is_independent() -> None:
    review = {"reviewer": {"source": "self", "run_id": "R1", "session_id": "S1"}}
    assert classify_review_independence(review, RunIdentity(run_id="R2", session_id="S2")) == "independent"


def test_no_reviewer_block_is_unknown() -> None:
    assert classify_review_independence({}, RunIdentity(run_id="R1")) == "unknown"


def test_self_review_empty_run_id_falls_back_to_session() -> None:
    review = {"reviewer": {"source": "self", "run_id": "", "session_id": "S1"}}
    assert classify_review_independence(review, RunIdentity(run_id="", session_id="S1")) == "self_same_session"
    assert classify_review_independence(review, RunIdentity(run_id="", session_id="S9")) == "independent"


def test_self_review_no_identity_on_either_side_is_unknown() -> None:
    review = {"reviewer": {"source": "self", "run_id": "", "session_id": ""}}
    assert classify_review_independence(review, RunIdentity()) == "unknown"


def test_subagent_distinct_identity_is_independent() -> None:
    # OQ-001 resolution: verifiable independence requires a DIFFERING identity.
    review = {"reviewer": {"source": "subagent", "run_id": "R2", "receipt_id": "x"}}
    assert classify_review_independence(review, RunIdentity(run_id="R1")) == "independent"


def test_subagent_same_run_id_is_asserted_only() -> None:
    # A subagent sharing the delivering run_id cannot self-mint independence.
    review = {"reviewer": {"source": "subagent", "run_id": "R1", "receipt_id": "x"}}
    assert classify_review_independence(review, RunIdentity(run_id="R1")) == "asserted_independent"


def test_subagent_no_identity_is_asserted_only() -> None:
    review = {"reviewer": {"source": "cross_model", "run_id": "", "session_id": ""}}
    assert classify_review_independence(review, RunIdentity(run_id="R1")) == "asserted_independent"


def test_operator_with_receipt_is_independent() -> None:
    review = {"reviewer": {"source": "operator", "run_id": "R1", "receipt_id": "OP"}}
    assert classify_review_independence(review, RunIdentity(run_id="R1")) == "independent"


def test_operator_without_receipt_is_unknown() -> None:
    review = {"reviewer": {"source": "operator", "run_id": "R1"}}
    assert classify_review_independence(review, RunIdentity(run_id="R1")) == "unknown"


# --------------------------------------------------------------------------- #
# FR03 — P0/P1 self-review cap
# --------------------------------------------------------------------------- #


def test_p0_self_review_capped() -> None:
    review = {"reviewer": {"source": "self", "run_id": "R1", "session_id": "S1"}}
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1", session_id="S1")) is False


def test_p1_self_review_capped() -> None:
    review = {"reviewer": {"source": "self", "run_id": "R1"}}
    assert review_receipt_satisfied("P1", review, RunIdentity(run_id="R1")) is False


def test_p0_independent_subagent_ok() -> None:
    review = {"reviewer": {"source": "subagent", "run_id": "R2", "receipt_id": "tok"}}
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1")) is True


def test_p2_self_review_ok() -> None:
    review = {"reviewer": {"source": "self", "run_id": "R1"}}
    assert review_receipt_satisfied("P2", review, RunIdentity(run_id="R1")) is True


def test_p0_operator_without_receipt_id_not_satisfied() -> None:
    # A stamped operator block missing receipt_id cannot certify.
    review = {"reviewer": {"source": "operator", "run_id": "R2"}}
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1")) is False


def test_p0_operator_with_receipt_id_satisfied() -> None:
    review = {"reviewer": {"source": "operator", "run_id": "R2", "receipt_id": "OP-1"}}
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1")) is True


def test_p0_unknown_provenance_not_satisfied() -> None:
    assert review_receipt_satisfied("P0", {}, RunIdentity(run_id="R1")) is False


def test_p0_asserted_independent_blocked_under_block_mode() -> None:
    # subagent sharing the delivering run_id = asserted, not verifiable.
    review = {"reviewer": {"source": "subagent", "run_id": "R1", "receipt_id": "x"}}
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1"), gate_mode="block") is False


def test_p0_asserted_independent_accepted_under_warn_mode() -> None:
    review = {"reviewer": {"source": "subagent", "run_id": "R1", "receipt_id": "x"}}
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1"), gate_mode="warn") is True


def test_p0_verifiable_independent_satisfies_both_modes() -> None:
    review = {"reviewer": {"source": "subagent", "run_id": "R2", "receipt_id": "x"}}
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1"), gate_mode="block") is True
    assert review_receipt_satisfied("P0", review, RunIdentity(run_id="R1"), gate_mode="warn") is True
