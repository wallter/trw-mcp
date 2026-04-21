"""Tests for outcome sync helpers — PRD-CORE-144 FR05/FR06 source-switch.

The legacy recall_tracking.jsonl reader was replaced with a run.yaml-
based iterator. This file preserves coverage for the core helper
surface; richer pusher coverage lives in ``test_outcome_pusher_run_yaml.py``.
"""

from __future__ import annotations

from pathlib import Path


def _write_run_yaml(runs_root: Path, task: str, run_id: str, body: str) -> Path:
    run_dir = runs_root / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "run.yaml").write_text(body, encoding="utf-8")
    return run_dir


def test_load_pending_outcomes_returns_empty_when_no_runs_dir(tmp_path: Path) -> None:
    from trw_mcp.sync.outcomes import load_pending_outcomes

    # tmp_path has no runs subdir -> empty list, no crash
    assert load_pending_outcomes(tmp_path) == []


def test_load_pending_outcomes_emits_one_payload_per_delivered_run(tmp_path: Path) -> None:
    from trw_mcp.sync.outcomes import load_pending_outcomes

    runs_root = tmp_path / "runs"
    _write_run_yaml(
        runs_root,
        "task-a",
        "run-1",
        (
            "session_metrics:\n"
            "  status: success\n"
            "  rework_rate:\n"
            "    rework_rate: 0.2\n"
            "    total_files: 5\n"
            "  learning_exposure:\n"
            "    ids:\n"
            "      - L-1\n"
            "      - L-2\n"
            "  composite_outcome: 0.8\n"
            "  normalized_reward: 0.69\n"
        ),
    )

    pending = load_pending_outcomes(tmp_path)
    assert len(pending) == 1
    item = pending[0]
    assert item.run_id == "run-1"
    assert item.sync_hash  # non-empty
    assert item.legacy_no_ids is False
    payload = item.payload
    assert payload["session_id"] == "run-1"
    assert payload["learning_ids"] == ["L-1", "L-2"]
    assert payload["rework_rate"] == 0.2
    assert payload["build_passed"] is True
    assert payload["files_changed"] == 5
    assert payload["propensity_data"]["source"] == "run_yaml"
    assert payload["propensity_data"]["normalized_reward"] == 0.69
