"""P1 F8: per-learning recall outcomes must reach the bandit propensity_data.

Defect: ``_step_recall_outcome`` writes recall events + outcome rows to
``.trw/logs/recall_tracking.jsonl``, but ``_build_outcome_payload`` previously
only emitted ``composite_outcome`` / ``normalized_reward`` propensity_data —
the recall signals never reached the backend IPS / bandit arm-update loop, so
the recall -> weight learning loop could not close.

These tests drive the REAL ``_build_outcome_payload`` /
``_aggregate_recall_outcomes`` against a fixture recall_tracking.jsonl and
assert (a) recall outcomes now enter propensity_data keyed by learning_id with
the right shape, and (b) the pre-existing composite/normalized propensity
fields are unchanged (regression guard).
"""

from __future__ import annotations

import json
from pathlib import Path


def _write_recall_tracking(trw_dir: Path, records: list[dict[str, object]]) -> Path:
    logs = trw_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "recall_tracking.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


# --- _aggregate_recall_outcomes: real aggregation ---------------------------


def test_aggregate_recall_outcomes_counts_recalls_and_outcomes(tmp_path: Path) -> None:
    from trw_mcp.sync.outcomes import _aggregate_recall_outcomes

    _write_recall_tracking(
        tmp_path,
        [
            {"learning_id": "L-1", "query": "q1", "outcome": None},
            {"learning_id": "L-1", "query": "q2", "outcome": None},
            {"learning_id": "L-1", "outcome": "positive"},
            {"learning_id": "L-2", "query": "q3", "outcome": None},
            {"learning_id": "L-2", "outcome": "negative"},
            {"learning_id": "L-2", "outcome": "neutral"},
        ],
    )

    agg = _aggregate_recall_outcomes(tmp_path)

    assert set(agg) == {"L-1", "L-2"}
    # L-1: two recall receipts, one positive outcome
    assert agg["L-1"]["learning_id"] == "L-1"
    assert agg["L-1"]["recall_count"] == 2
    assert agg["L-1"]["positive"] == 1
    assert agg["L-1"]["negative"] == 0
    assert agg["L-1"]["neutral"] == 0
    # L-2: one recall receipt, one negative + one neutral outcome
    assert agg["L-2"]["recall_count"] == 1
    assert agg["L-2"]["negative"] == 1
    assert agg["L-2"]["neutral"] == 1


def test_aggregate_recall_outcomes_selection_probability_monotonic(tmp_path: Path) -> None:
    """More recalls -> higher selection_probability (bandit-weight proxy)."""
    from trw_mcp.sync.outcomes import _aggregate_recall_outcomes

    _write_recall_tracking(
        tmp_path,
        [
            {"learning_id": "L-low", "outcome": None},
            {"learning_id": "L-high", "outcome": None},
            {"learning_id": "L-high", "outcome": None},
            {"learning_id": "L-high", "outcome": None},
        ],
    )

    agg = _aggregate_recall_outcomes(tmp_path)

    low = float(agg["L-low"]["selection_probability"])  # type: ignore[arg-type]
    high = float(agg["L-high"]["selection_probability"])  # type: ignore[arg-type]
    # recall_count=1 -> 1 - 1/2 = 0.5 ; recall_count=3 -> 1 - 1/4 = 0.75
    assert low == 0.5
    assert high == 0.75
    assert high > low
    assert 0.0 < low <= 1.0
    assert 0.0 < high <= 1.0


def test_aggregate_recall_outcomes_torn_line_preserves_valid_rows(tmp_path: Path) -> None:
    """A torn concurrent append drops only its row, not the whole bandit signal.

    Regression: the strict reader raised StateError on the first malformed line
    and the broad fail-open returned {}, silently starving the backend IPS /
    arm-update loop of every learning's recall feedback for that sync cycle. The
    resilient reader keeps the valid rows.
    """
    from trw_mcp.sync.outcomes import _aggregate_recall_outcomes

    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "recall_tracking.jsonl"
    good_a = json.dumps({"learning_id": "L-1", "outcome": None})
    good_b = json.dumps({"learning_id": "L-1", "outcome": "positive"})
    torn = '{"learning_id": "L-2", "outcome": "neg'  # partial interleaved append
    path.write_text(f"{good_a}\n{torn}\n{good_b}\n", encoding="utf-8")

    agg = _aggregate_recall_outcomes(tmp_path)

    # Pre-fix this returned {}; now L-1's valid rows survive the torn L-2 row.
    assert set(agg) == {"L-1"}
    assert agg["L-1"]["recall_count"] == 1
    assert agg["L-1"]["positive"] == 1


def test_aggregate_recall_outcomes_missing_file_returns_empty(tmp_path: Path) -> None:
    from trw_mcp.sync.outcomes import _aggregate_recall_outcomes

    assert _aggregate_recall_outcomes(tmp_path) == {}
    assert _aggregate_recall_outcomes(None) == {}


# --- _build_outcome_payload: recall outcomes reach propensity_data ----------


def test_build_outcome_payload_includes_recall_outcomes_keyed_by_learning_id(tmp_path: Path) -> None:
    from trw_mcp.sync.outcomes import _build_outcome_payload

    _write_recall_tracking(
        tmp_path,
        [
            {"learning_id": "L-1", "query": "q1", "outcome": None},
            {"learning_id": "L-1", "outcome": "positive"},
            {"learning_id": "L-2", "query": "q2", "outcome": None},
            # L-3 is recalled but NOT exposed this run -> must be scoped out
            {"learning_id": "L-3", "query": "q3", "outcome": None},
        ],
    )

    session_metrics: dict[str, object] = {
        "status": "success",
        "learning_exposure": {"ids": ["L-1", "L-2"]},
        "composite_outcome": 0.8,
        "normalized_reward": 0.69,
    }

    payload = _build_outcome_payload(
        run_id="run-1",
        run_dir=tmp_path / "runs" / "task-a" / "run-1",
        session_metrics=session_metrics,
        legacy_no_ids=False,
        trw_dir=tmp_path,
    )

    propensity = payload["propensity_data"]
    assert isinstance(propensity, dict)

    # FIX: recall outcomes now present, keyed by learning_id
    recall_outcomes = propensity["recall_outcomes"]
    assert isinstance(recall_outcomes, dict)
    # Scoped to learnings exposed THIS run (L-1, L-2) — L-3 excluded
    assert set(recall_outcomes) == {"L-1", "L-2"}
    assert recall_outcomes["L-1"]["learning_id"] == "L-1"
    assert recall_outcomes["L-1"]["recall_count"] == 1
    assert recall_outcomes["L-1"]["positive"] == 1
    # IPS path reads selection_probability — it must be present + real
    assert recall_outcomes["L-1"]["selection_probability"] == 0.5
    assert recall_outcomes["L-2"]["recall_count"] == 1


def test_build_outcome_payload_recall_outcomes_does_not_disturb_existing_fields(tmp_path: Path) -> None:
    """REGRESSION GUARD: existing composite/normalized propensity fields unchanged."""
    from trw_mcp.sync.outcomes import _build_outcome_payload

    _write_recall_tracking(
        tmp_path,
        [{"learning_id": "L-1", "outcome": None}],
    )

    session_metrics: dict[str, object] = {
        "status": "success",
        "learning_exposure": {"ids": ["L-1"]},
        "composite_outcome": 0.8,
        "normalized_reward": 0.69,
    }

    payload = _build_outcome_payload(
        run_id="run-1",
        run_dir=tmp_path / "runs" / "task-a" / "run-1",
        session_metrics=session_metrics,
        legacy_no_ids=False,
        trw_dir=tmp_path,
    )

    propensity = payload["propensity_data"]
    assert isinstance(propensity, dict)
    # Pre-existing fields preserved verbatim
    assert propensity["source"] == "run_yaml"
    assert propensity["composite_outcome"] == 0.8
    assert propensity["normalized_reward"] == 0.69
    assert "legacy_no_ids" not in propensity
    # Top-level OutcomeSync shape preserved
    assert payload["session_id"] == "run-1"
    assert payload["learning_ids"] == ["L-1"]
    assert payload["build_passed"] is True


def test_build_outcome_payload_no_recall_tracking_omits_recall_outcomes(tmp_path: Path) -> None:
    """No recall_tracking.jsonl -> payload is byte-identical to the pre-fix shape."""
    from trw_mcp.sync.outcomes import _build_outcome_payload

    session_metrics: dict[str, object] = {
        "status": "success",
        "learning_exposure": {"ids": ["L-1"]},
        "composite_outcome": 0.8,
        "normalized_reward": 0.69,
    }

    payload = _build_outcome_payload(
        run_id="run-1",
        run_dir=tmp_path / "runs" / "task-a" / "run-1",
        session_metrics=session_metrics,
        legacy_no_ids=False,
        trw_dir=tmp_path,  # exists but has no logs/recall_tracking.jsonl
    )

    propensity = payload["propensity_data"]
    assert isinstance(propensity, dict)
    # ADDITIVE: when there is nothing to add, the key is absent (no empty stub)
    assert "recall_outcomes" not in propensity
    assert propensity["composite_outcome"] == 0.8
    assert propensity["normalized_reward"] == 0.69


def test_build_outcome_payload_legacy_no_ids_uses_all_recall_outcomes(tmp_path: Path) -> None:
    """Legacy runs (no exposure ids) fall back to all aggregated learnings."""
    from trw_mcp.sync.outcomes import _build_outcome_payload

    _write_recall_tracking(
        tmp_path,
        [
            {"learning_id": "L-1", "outcome": None},
            {"learning_id": "L-2", "outcome": None},
        ],
    )

    session_metrics: dict[str, object] = {"status": "success"}

    payload = _build_outcome_payload(
        run_id="run-legacy",
        run_dir=tmp_path / "runs" / "task-a" / "run-legacy",
        session_metrics=session_metrics,
        legacy_no_ids=True,
        trw_dir=tmp_path,
    )

    propensity = payload["propensity_data"]
    assert isinstance(propensity, dict)
    assert propensity["legacy_no_ids"] is True
    recall_outcomes = propensity["recall_outcomes"]
    assert isinstance(recall_outcomes, dict)
    assert set(recall_outcomes) == {"L-1", "L-2"}


def test_load_pending_outcomes_threads_recall_outcomes_into_payload(tmp_path: Path) -> None:
    """End-to-end: load_pending_outcomes wires trw_dir -> recall_outcomes in payload."""
    from trw_mcp.sync.outcomes import load_pending_outcomes

    # Recall tracking under <trw_dir>/logs
    _write_recall_tracking(
        tmp_path,
        [
            {"learning_id": "L-1", "outcome": None},
            {"learning_id": "L-1", "outcome": "positive"},
        ],
    )

    # A delivered run exposing L-1
    runs_root = tmp_path / "runs"
    meta = runs_root / "task-a" / "run-1" / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "run.yaml").write_text(
        (
            "session_metrics:\n"
            "  status: success\n"
            "  learning_exposure:\n"
            "    ids:\n"
            "      - L-1\n"
            "  composite_outcome: 0.8\n"
            "  normalized_reward: 0.69\n"
        ),
        encoding="utf-8",
    )

    pending = load_pending_outcomes(tmp_path)
    assert len(pending) == 1
    propensity = pending[0].payload["propensity_data"]
    assert isinstance(propensity, dict)
    recall_outcomes = propensity["recall_outcomes"]
    assert isinstance(recall_outcomes, dict)
    assert recall_outcomes["L-1"]["recall_count"] == 1
    assert recall_outcomes["L-1"]["positive"] == 1
    # regression: existing propensity fields still present
    assert propensity["composite_outcome"] == 0.8
    assert propensity["normalized_reward"] == 0.69


def test_load_pending_outcomes_aggregates_recall_log_once(tmp_path: Path, monkeypatch) -> None:
    """The append-only recall log is parsed once, not once per pending run."""
    from trw_mcp.sync import outcomes

    _write_recall_tracking(tmp_path, [{"learning_id": "L-1", "outcome": None}])
    for run_id in ("run-1", "run-2"):
        meta = tmp_path / "runs" / "task-a" / run_id / "meta"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "run.yaml").write_text(
            "session_metrics:\n  status: success\n  learning_exposure:\n    ids: [L-1]\n",
            encoding="utf-8",
        )

    real_aggregate = outcomes._aggregate_recall_outcomes
    calls = 0

    def counted_aggregate(trw_dir: Path | None) -> dict[str, dict[str, object]]:
        nonlocal calls
        calls += 1
        return real_aggregate(trw_dir)

    monkeypatch.setattr(outcomes, "_aggregate_recall_outcomes", counted_aggregate)

    pending = outcomes.load_pending_outcomes(tmp_path)

    assert len(pending) == 2
    assert calls == 1
