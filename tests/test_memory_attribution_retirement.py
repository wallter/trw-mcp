"""R10: real lifecycle callers preserve observations without inventing usefulness."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests._tools_ceremony_support import _apply_stubs, _stub_all_deferred_steps
from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.state.memory_adapter import find_entry_by_id


@pytest.mark.parametrize("passed", [True, False])
def test_registered_lifecycle_keeps_exposure_and_impact_without_credit(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch, passed: bool
) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    trw_dir = tmp_project / ".trw"
    logs = trw_dir / "logs"
    logs.mkdir(exist_ok=True)
    tracking = logs / "recall_tracking.jsonl"
    # Old pooled success counts must not change a new caller's impact.
    tracking.write_text(json.dumps({"learning_id": "L-other", "outcome": "positive", "timestamp": 1}) + "\n")
    learn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    result = learn(
        summary="A diagnostic must ignore requirements outside its execution plan section",
        detail="Scan the real execution section only; a phantom elsewhere is not a plan failure.",
        impact=0.83,
        tags=["testing"],
    )
    lid = result["learning_id"]
    before = find_entry_by_id(trw_dir, lid)
    assert before is not None and before["impact"] == 0.83
    now = time.time()
    exposures = [
        {"learning_id": lid, "query": query, "timestamp": timestamp, "outcome": None}
        for query, timestamp in (("unrelated", now), ("old", 1), ("client-timeout", now))
    ]
    with tracking.open("a") as handle:
        for row in exposures:
            handle.write(json.dumps(row) + "\n")
    tracking_before = tracking.read_bytes()
    run = tmp_project / "run"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "run.yaml").write_text("run_id: attribution\nstatus: active\nphase: validate\nprd_scope: []\n")
    from trw_mcp.state._ceremony_progress_state import record_nudge_shown

    record_nudge_shown(trw_dir, lid, "validate", turn=1)
    build = extract_tool_fn(make_test_server("build"), "trw_build_check")
    build_result = build(
        tests_passed=passed,
        static_checks_clean=True,
        test_count=1,
        failure_count=0 if passed else 1,
        scope="unrelated checks",
        run_path=str(run),
    )
    assert "q_learning_deferred" not in build_result
    raw_events = (run / "meta" / "events.jsonl").read_text()
    events = [json.loads(line) for line in raw_events.splitlines()]
    observed = next(event for event in events if event["event"] == "build_check_complete")
    assert observed["tests_passed"] is passed
    assert observed["scope"] == "unrelated checks"

    from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics

    metrics = _step_delivery_metrics(trw_dir, run)
    assert [signal["learning_id"] for signal in metrics["proximal_signals"]] == [lid]
    assert "proximal_q_updates" not in metrics

    from trw_mcp.tools._deferred_delivery import _run_deferred_steps

    # Drive the real deferred roster; isolate unrelated maintenance/network work.
    stubs = _stub_all_deferred_steps()
    del stubs["_step_outcome_correlation"]
    del stubs["_step_recall_outcome"]
    with _apply_stubs(stubs):
        _run_deferred_steps(trw_dir, run, {})
    deferred = json.loads((logs / "deferred-deliver.jsonl").read_text().splitlines()[-1])
    assert deferred["results"]["outcome_correlation"] == {"status": "skipped", "updated": 0}
    assert deferred["results"]["recall_outcome"] == {"status": "skipped", "recorded": 0}
    assert tracking.read_bytes() == tracking_before
    after = find_entry_by_id(trw_dir, lid)
    assert after is not None
    for field in ("impact", "q_value", "q_observations", "outcome_history"):
        assert after.get(field) == before.get(field), field

    # Positive control: entry-specific contradiction evidence remains actionable.
    from trw_mcp.scoring import apply_contradiction_penalty

    assert apply_contradiction_penalty([lid], trw_dir) == [lid]
    contradicted = find_entry_by_id(trw_dir, lid)
    assert contradicted is not None
    assert int(contradicted.get("q_observations", 0)) > int(after.get("q_observations", 0))
