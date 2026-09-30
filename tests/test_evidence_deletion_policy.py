"""EVIDENCE-DELETION-POLICY: a scope or evidence that was ever declared cannot be undeclared by deleting files.

(a) The PRD scope a safety-critical gate reads is also witnessed in the append-only ``meta/events.jsonl``
    (``run_init.prd_scope`` and ``audit_cycle_complete.prd_id``); deleting run.yaml and the review receipts
    leaves it. An untrustworthy log is an UNKNOWN scope, never a smaller one.
(c) INT-REVIEW-ABSENCE-POLICY: a recorded integration review deleted, emptied or edited afterwards blocks the
    NO_ESCAPE gate by name; one that was never recorded and is absent still means no review was run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._review_modes_support import run_dir  # noqa: F401
from tests._tools_orchestration_support import orch_tools  # noqa: F401
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


def _run(tmp_path: Path) -> Path:
    run = tmp_path / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True)
    return run


def _events(run: Path, *records: dict[str, object], torn_tail: str = "") -> None:
    body = "".join(json.dumps(r) + "\n" for r in records) + torn_tail
    (run / "meta" / "events.jsonl").write_text(body, encoding="utf-8")


def _scope(run: Path) -> Any:
    from trw_mcp.tools._delivery_safety_critical_gate import declared_scope

    return declared_scope(run)


# --- (a) scope ---------------------------------------------------------------------------------------------------


def test_trw_init_witnesses_the_declared_scope_in_the_event_log(orch_tools: dict[str, Any]) -> None:
    result = orch_tools["trw_init"].fn(task_name="witness", prd_scope=["PRD-SC-001"])
    run = Path(str(result["run_path"]))
    (run / "meta" / "run.yaml").unlink()
    assert _scope(run).union == ["PRD-SC-001"]


def test_a_deleted_run_yaml_and_receipts_do_not_shrink_the_scope(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "meta" / "run.yaml").write_text("prd_scope: [PRD-SC-001]\n", encoding="utf-8")
    _events(
        run,
        {"event": "run_init", "task": "t", "prd_scope": ["PRD-SC-001"]},
        {"event": "audit_cycle_complete", "prd_id": "PRD-SC-002", "verdict": "PASS"},
    )
    (run / "meta" / "run.yaml").unlink()
    assert not (run / "meta" / "receipts").exists()
    declared = _scope(run)
    assert declared.union == ["PRD-SC-001", "PRD-SC-002"] and declared.unreadable_receipts == ()


def test_nested_data_fields_from_older_writers_are_read(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _events(run, {"event": "run_init", "data": {"prd_scope": ["PRD-SC-003"]}})
    assert _scope(run).union == ["PRD-SC-003"]


@pytest.mark.parametrize("damage", ["middle-line", "symlink", "directory"])
def test_an_untrustworthy_witness_is_an_unknown_scope(tmp_path: Path, damage: str) -> None:
    from trw_mcp.tools._delivery_safety_critical_gate import UNKNOWN_SCOPE, _resolve_scope

    run = _run(tmp_path)
    (run / "meta" / "run.yaml").write_text("prd_scope: []\n", encoding="utf-8")
    events = run / "meta" / "events.jsonl"
    if damage == "middle-line":
        events.write_text('{"event": "run_init", "prd_scope": ["PRD-SC-001"]\n{"event": "x"}\n', encoding="utf-8")
    elif damage == "symlink":
        target = tmp_path / "elsewhere.jsonl"
        target.write_text('{"event": "run_init", "prd_scope": []}\n', encoding="utf-8")
        events.symlink_to(target)
    else:
        events.mkdir()
    resolution = _resolve_scope(run)
    assert resolution.value == UNKNOWN_SCOPE
    assert "meta/events.jsonl" in resolution.unreadable_receipts


def test_a_torn_final_append_is_tolerated(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _events(run, {"event": "run_init", "prd_scope": ["PRD-SC-001"]}, torn_tail='{"event": "audit_cyc')
    declared = _scope(run)
    assert declared.union == ["PRD-SC-001"] and declared.unreadable_receipts == ()


def test_a_run_without_a_log_is_judged_as_before(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "meta" / "run.yaml").write_text("prd_scope: [PRD-SC-001]\n", encoding="utf-8")
    assert _scope(run).union == ["PRD-SC-001"]


# --- (c) integration review --------------------------------------------------------------------------------------


def _record_review(run: Path, body: dict[str, object]) -> Path:
    from trw_mcp.tools._review_auto import _witness_integration_review

    writer = FileStateWriter()
    path = run / "meta" / "integration-review.yaml"
    writer.write_yaml(path, body)
    _witness_integration_review(run, path, writer)
    return path


def _gate(run: Path) -> tuple[str | None, str | None]:
    from trw_mcp.tools._delivery_helpers import _check_integration_review_gate

    return _check_integration_review_gate(run, FileStateReader())


_BLOCKING = {"verdict": "block", "findings": [{"severity": "critical"}]}


@pytest.mark.parametrize("how", ["deleted", "emptied"])
def test_a_recorded_review_cannot_be_withdrawn(tmp_path: Path, how: str) -> None:
    run = _run(tmp_path)
    path = _record_review(run, _BLOCKING)
    if how == "deleted":
        path.unlink()
    else:
        path.write_text("", encoding="utf-8")
    block, _ = _gate(run)
    assert block is not None and "was recorded" in block and "'block'" in block


def test_an_edited_review_blocks_as_changed(tmp_path: Path) -> None:
    run = _run(tmp_path)
    path = _record_review(run, _BLOCKING)
    path.write_text("verdict: pass\nfindings: []\n", encoding="utf-8")
    block, _ = _gate(run)
    assert block is not None and "no longer matches" in block


def test_an_untouched_recorded_review_is_judged_on_its_verdict(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _record_review(run, {"verdict": "pass", "findings": []})
    assert _gate(run) == (None, None)
    _record_review(run, _BLOCKING)  # a later review supersedes the earlier record
    block, _ = _gate(run)
    assert block is not None and "1 critical finding(s)" in block


def test_never_recorded_and_absent_still_means_no_review(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _events(run, {"event": "run_init", "prd_scope": []})
    assert _gate(run) == (None, None)


def test_an_unreadable_log_with_no_artifact_is_unknown_and_blocks(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "meta" / "events.jsonl").write_text('{"event": \n{"event": "x"}\n', encoding="utf-8")
    block, _ = _gate(run)
    assert block is not None and "UNKNOWN" in block


def test_an_unreadable_log_does_not_block_a_readable_artifact(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "meta" / "integration-review.yaml").write_text("verdict: pass\n", encoding="utf-8")
    (run / "meta" / "events.jsonl").write_text('{"event": \n{"event": "x"}\n', encoding="utf-8")
    assert _gate(run) == (None, None)


def test_trw_review_auto_mode_witnesses_the_integration_review(
    tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real writer path: trw_review(auto) records the review, so deleting it afterwards blocks."""
    from unittest.mock import patch

    from tests._ceremony_helpers import make_ceremony_server
    from trw_mcp.models.config import TRWConfig, _reset_config

    tools = make_ceremony_server(monkeypatch, tmp_path)
    _reset_config(TRWConfig(review_confidence_threshold=0))
    findings = [{"reviewer_role": "integration", "confidence": 90, "category": "wiring", "severity": "critical",
                 "description": "duplicate API contract"}]  # fmt: skip
    with (
        patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
    ):
        tools["trw_review"].fn(reviewer_findings=findings)
    artifact = run_dir / "meta" / "integration-review.yaml"
    assert artifact.exists()
    before, _ = _gate(run_dir)
    assert before is None or "was recorded" not in before  # judged on its verdict, not as withdrawn
    artifact.unlink()
    block, _ = _gate(run_dir)
    assert block is not None and "was recorded" in block
