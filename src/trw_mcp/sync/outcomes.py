"""Outcome sync helpers for PRD-INFRA-051."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TRACKING_FILE = "logs/recall_tracking.jsonl"


@dataclass(frozen=True)
class PendingOutcome:
    """Outcome payload plus its append-only source position."""

    payload: dict[str, object]
    line_no: int


def load_pending_outcomes(trw_dir: Path, *, since_line: int = 0) -> list[PendingOutcome]:
    """Load unsynced recall outcomes from the local append-only tracking log."""
    tracking_path = trw_dir / _TRACKING_FILE
    if not tracking_path.exists():
        return []

    pending: list[PendingOutcome] = []
    for line_no, raw_line in enumerate(tracking_path.read_text(encoding="utf-8").splitlines(), start=1):
        if line_no <= since_line:
            continue
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            record: dict[str, Any] = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        learning_id = str(record.get("learning_id", "")).strip()
        outcome = str(record.get("outcome", "")).strip().lower()
        if not learning_id or outcome not in {"positive", "negative", "neutral"}:
            continue
        pending.append(
            PendingOutcome(
                payload=_serialize_outcome_record(record, learning_id=learning_id, outcome=outcome, line_no=line_no),
                line_no=line_no,
            )
        )
    return pending


def _serialize_outcome_record(
    record: dict[str, Any],
    *,
    learning_id: str,
    outcome: str,
    line_no: int,
) -> dict[str, object]:
    """Convert a local recall outcome row into backend outcome payload shape."""
    propensity_data: dict[str, object] = {
        "source": "recall_tracking",
        "outcome": outcome,
        "line_no": line_no,
    }
    timestamp = record.get("timestamp")
    if isinstance(timestamp, int | float):
        propensity_data["recorded_at"] = float(timestamp)

    payload: dict[str, object] = {
        "session_id": f"outcome-{line_no}-{learning_id}"[:128],
        "learning_ids": [learning_id],
        "propensity_data": propensity_data,
    }
    if outcome == "positive":
        payload["build_passed"] = True
    elif outcome == "negative":
        payload["build_passed"] = False
    else:
        payload["rework_rate"] = 0.5
    return payload
