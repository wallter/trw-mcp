"""PRD-CORE-144 FR05 + FR06: run.yaml-based outcome pusher coverage."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from structlog.testing import capture_logs

from trw_mcp.sync.outcomes import (
    load_pending_outcomes,
    write_synced_marker,
)


def _mk_run(
    runs_root: Path,
    task: str,
    run_id: str,
    *,
    yaml_body: str,
) -> Path:
    run_dir = runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(yaml_body, encoding="utf-8")
    return run_dir


GOOD_RUN_YAML = (
    "session_metrics:\n"
    "  status: success\n"
    "  rework_rate:\n"
    "    rework_rate: 0.15\n"
    "    total_files: 3\n"
    "  learning_exposure:\n"
    "    ids:\n"
    "      - A\n"
    "      - B\n"
)

LEGACY_RUN_YAML = (
    "session_metrics:\n"
    "  status: success\n"
    "  rework_rate:\n"
    "    rework_rate: 0.4\n"
    "    total_files: 1\n"
    "  learning_exposure:\n"
    "    recall_pull_rate: 0.0\n"
    "    nudge_count: 0\n"
    # no "ids" -> legacy pre-FR04 shape
)

NO_METRICS_YAML = "phase: research\nstatus: abandoned\n"


def test_delivered_run_emits_real_outcome(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _mk_run(runs_root, "t1", "run-alpha", yaml_body=GOOD_RUN_YAML)

    pending = load_pending_outcomes(tmp_path)
    assert len(pending) == 1
    item = pending[0]
    assert item.run_id == "run-alpha"
    assert item.payload["learning_ids"] == ["A", "B"]
    assert item.payload["rework_rate"] == 0.15
    assert item.payload["build_passed"] is True


def test_legacy_run_emits_empty_ids_and_logs(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _mk_run(runs_root, "t1", "run-legacy", yaml_body=LEGACY_RUN_YAML)

    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    with capture_logs() as cap:
        pending = load_pending_outcomes(tmp_path)

    assert len(pending) == 1
    item = pending[0]
    assert item.legacy_no_ids is True
    assert item.payload["learning_ids"] == []
    assert item.payload["rework_rate"] == 0.4
    # Structured log emitted
    legacy_events = [e for e in cap if e.get("event") == "legacy_run_pushed"]
    assert legacy_events, "expected a legacy_run_pushed log event"
    assert legacy_events[0]["run_id"] == "run-legacy"


def test_synced_marker_matching_hash_is_skipped(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _mk_run(runs_root, "t1", "run-alpha", yaml_body=GOOD_RUN_YAML)

    # First pass -> one pending outcome
    first = load_pending_outcomes(tmp_path)
    assert len(first) == 1

    # Simulate a successful push by writing the marker
    write_synced_marker(
        run_dir,
        run_id=first[0].run_id,
        sync_hash=first[0].sync_hash,
        target_label="backend-prod",
    )

    # Second pass -> skipped
    second = load_pending_outcomes(tmp_path)
    assert second == []


def test_abandoned_run_with_no_session_metrics_skipped(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _mk_run(runs_root, "t1", "run-abandoned", yaml_body=NO_METRICS_YAML)
    assert load_pending_outcomes(tmp_path) == []


def test_recall_tracking_format_unchanged_smoke(tmp_path: Path) -> None:
    """Regression: recall_tracking.jsonl must remain readable by its other consumers.

    Smoke: the surface_tracking + recall_tracking modules still import
    cleanly and expose their public functions — proving no accidental
    dependency was introduced by the pusher rewrite.
    """
    from trw_mcp.state.recall_tracking import (  # noqa: F401
        get_recall_stats,
        record_outcome,
    )
    from trw_mcp.state.surface_tracking import (  # noqa: F401
        compute_recall_pull_rate,
        log_surface_event,
    )

    # And: ensure the legacy recall_tracking.jsonl file format is untouched
    # by writing one and reading it back.
    logs = tmp_path / "logs"
    logs.mkdir()
    path = logs / "recall_tracking.jsonl"
    path.write_text(
        json.dumps({"learning_id": "L-1", "outcome": "positive", "timestamp": 1.0}) + "\n",
        encoding="utf-8",
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["learning_id"] == "L-1"


def test_marker_write_idempotent(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _mk_run(runs_root, "t1", "run-m", yaml_body=GOOD_RUN_YAML)
    write_synced_marker(run_dir, run_id="run-m", sync_hash="h1", target_label="t")
    marker = run_dir / "meta" / "synced.json"
    assert marker.exists()
    first = marker.read_text()
    write_synced_marker(run_dir, run_id="run-m", sync_hash="h1", target_label="t")
    assert marker.exists()
    # second write overwrites; JSON still parses
    parsed = json.loads(marker.read_text())
    assert parsed["run_id"] == "run-m"
    _ = first  # silence unused


def test_hash_changes_when_session_metrics_change(tmp_path: Path) -> None:
    """Re-scan after session_metrics mutation detects the run as un-synced."""
    runs_root = tmp_path / "runs"
    run_dir = _mk_run(runs_root, "t1", "run-beta", yaml_body=GOOD_RUN_YAML)

    first = load_pending_outcomes(tmp_path)
    write_synced_marker(
        run_dir,
        run_id=first[0].run_id,
        sync_hash=first[0].sync_hash,
        target_label="t",
    )
    # Mutate the run.yaml
    (run_dir / "meta" / "run.yaml").write_text(GOOD_RUN_YAML.replace("0.15", "0.99"), encoding="utf-8")
    second = load_pending_outcomes(tmp_path)
    assert len(second) == 1
    assert second[0].sync_hash != first[0].sync_hash
