"""Tests for outcome sync helpers."""

from __future__ import annotations

import json


def test_load_pending_outcomes_filters_synced_lines_and_maps_labels(tmp_path) -> None:
    """Only new outcome rows are converted, with outcome labels mapped to payloads."""
    from trw_mcp.sync.outcomes import load_pending_outcomes

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    tracking_path = logs_dir / "recall_tracking.jsonl"
    tracking_path.write_text(
        "\n".join(
            [
                json.dumps({"learning_id": "L-old", "outcome": "positive", "timestamp": 1.0}),
                json.dumps({"learning_id": "L-neutral", "outcome": "neutral", "timestamp": 2.0}),
                json.dumps({"learning_id": "L-ignored", "outcome": None, "timestamp": 3.0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    pending = load_pending_outcomes(tmp_path, since_line=1)

    assert [item.line_no for item in pending] == [2]
    payload = pending[0].payload
    assert payload["learning_ids"] == ["L-neutral"]
    assert payload["rework_rate"] == 0.5
    assert "build_passed" not in payload
    assert payload["propensity_data"]["source"] == "recall_tracking"
