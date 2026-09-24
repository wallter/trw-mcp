"""The outcome sync payload carries no session-local recall data.

Recall receipts in ``.trw/logs/recall_tracking.jsonl`` stay local: the
``recall_outcomes`` propensity block that used to feed the backend bandit was
removed in trw-mcp 6.1.0 (the backend never read it).
"""

from __future__ import annotations

import json
from pathlib import Path

# --- PRD-FIX-144 FR05 / NFR04 ------------------------------------------------


def test_sync_payload_after_session_carries_no_session_keys_or_observations(tmp_path: Path) -> None:
    """NFR04: new join keys and the session observation log never reach sync."""
    from trw_mcp.models.build import BuildStatus
    from trw_mcp.state.recall_tracking import record_recall
    from trw_mcp.sync.outcomes import load_pending_outcomes
    from trw_mcp.tools.build._registration import _record_session_observation

    trw_dir = tmp_path / ".trw"
    query = 'how does "auth" work — ünïcode & <tags>'
    assert record_recall("L-1", query) is True
    status = BuildStatus(tests_passed=True, test_count=4, timestamp="2026-09-18T00:00:00+00:00", scope="full")
    _record_session_observation(trw_dir, status)
    logs = trw_dir / "logs"
    assert (logs / "session_outcomes.jsonl").read_text().strip(), "fixture must have written an observation"

    receipt_line = (logs / "recall_tracking.jsonl").read_text().splitlines()[0]
    # Byte-identical query serialization to the pre-change writer (json.dumps defaults).
    assert json.dumps({"query": query})[1:-1] in receipt_line
    assert json.loads(receipt_line)["query"] == query

    meta = trw_dir / "runs" / "task-a" / "run-1" / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "session_metrics:\n  status: success\n  learning_exposure:\n    ids: [L-1]\n", encoding="utf-8"
    )
    pending = load_pending_outcomes(trw_dir)
    assert len(pending) == 1
    payload = pending[0].payload
    serialized = json.dumps(payload)
    assert "process_session_id" not in serialized
    assert "session_outcomes" not in serialized
    assert "build_check" not in serialized
    assert query not in serialized
    assert payload["session_id"] == "run-1"  # the run-level field is unchanged
    assert "recall_outcomes" not in payload["propensity_data"]  # type: ignore[operator]
