"""PRD-CORE-362 FR09 (CLI), FR10, NFR03: a tree-unknown row is not demoted at recall, and recall and session
start never reach the claim tree.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from trw_memory.lifecycle.verification_pass import MaintainVerifySummary

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)


def _row(
    row_id: str, *, last_result: bool | None, anchor_validity: float | None = 1.0, verified: bool = True
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": row_id,
        "assertions": [
            {
                "type": "grep_present",
                "pattern": "p",
                "target": "t.py",
                "last_result": last_result,
                "last_verified_at": "2026-10-10T05:00:00+00:00" if verified else None,
                "first_failed_at": "2026-10-09T05:00:00+00:00" if last_result is False else None,
                "last_evidence": "checkout HEAD 0123456789ab is an ancestor of ba9876543210, the commit this assertion was written at",
            }
        ],
    }
    if anchor_validity is not None:
        row["anchor_validity"] = anchor_validity
    return row


def test_recall_does_not_demote_a_tree_unknown_row() -> None:
    from trw_mcp.tools._recall_assertion_verification import keep_retrieval_order
    from trw_mcp.tools._stored_claim_evidence import stored_claim_evidence

    unknown = _row("L-unknown", last_result=None)
    evidence, fraction = stored_claim_evidence(unknown, ttl_seconds=3600.0, now=NOW)
    assert fraction == 0.0
    assert evidence["observation"] == "unknown"

    clean_before = _row("L-before", last_result=True)
    clean_after = _row("L-after", last_result=True)
    failed = _row("L-failed", last_result=False)
    retrieved = [clean_before, unknown, failed, clean_after]

    def penalty(entry: dict[str, object]) -> float:
        return 0.15 * stored_claim_evidence(entry, ttl_seconds=3600.0, now=NOW)[1]

    ordered = keep_retrieval_order(list(retrieved), [], 0.0, assertion_penalties=penalty)

    assert [e["id"] for e in ordered] == ["L-before", "L-unknown", "L-after", "L-failed"]


class _Raises:
    def __init__(self, *_a: object, **_kw: object) -> None:
        raise AssertionError("the claim tree was used on a path that must not reach it")


def _forbid_the_claim_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.lifecycle import claim_tree, verification_pass

    from trw_mcp.tools import _learn_assertion_stamp

    monkeypatch.setattr(claim_tree, "ClaimTree", _Raises)
    monkeypatch.setattr(claim_tree, "checkout_head", _Raises)
    monkeypatch.setattr(verification_pass, "ClaimTree", _Raises)
    monkeypatch.setattr(_learn_assertion_stamp.claim_tree, "checkout_head", _Raises)


def test_recall_and_session_start_never_reach_the_claim_tree(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._ceremony_session_start_steps import step_assertion_health
    from trw_mcp.tools._recall_assertion_verification import _verify_assertions, keep_retrieval_order

    _forbid_the_claim_tree(monkeypatch)
    with pytest.raises(AssertionError, match="claim tree"):  # the guard itself bites
        from trw_memory.lifecycle import claim_tree

        claim_tree.ClaimTree(tmp_path)

    rows = [_row("L-a", last_result=None), _row("L-b", last_result=False), _row("L-c", last_result=True)]
    ranked = _verify_assertions(rows, ["p"], TRWConfig(), keep_retrieval_order, rank_always=True)
    assert {r["id"] for r in ranked} == {"L-a", "L-b", "L-c"}

    step_assertion_health(tmp_path, None, TRWConfig())  # type: ignore[call-arg]


@pytest.mark.parametrize("as_json", [True, False])
def test_maintain_verify_output_names_tree_behind(
    as_json: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._subcommands_maintain import _run_maintain_verify

    summary = MaintainVerifySummary(entries_processed=5, tree_behind=2)
    monkeypatch.setattr(
        "trw_mcp.tools._maintain_verify.run_maintain_verify_for_project", lambda namespace=None: summary
    )

    _run_maintain_verify(argparse.Namespace(namespace=None, as_json=as_json))

    out = capsys.readouterr().out
    if as_json:
        assert json.loads(out)["tree_behind"] == 2
    else:
        assert "2 held as unknown" in out and "5 entries" in out
