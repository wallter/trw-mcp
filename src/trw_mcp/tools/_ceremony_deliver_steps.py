"""Deliver-tool step helpers — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Three step helpers used by ``trw_deliver``:

- ``unpack_gate_result`` — copy delivery-gate verdict keys to the typed
  result dict.
- ``step_clear_score`` — compute + persist PRD-HPO-MEAS-001 FR-5 CLEAR
  score for the closing session.
- ``log_deliver_complete`` — emit deliver_ok / deliver_failed /
  trw_deliver_complete log lines.

Extracted as DIST-243 batch 64 to push parent ``ceremony.py`` away from
the 717-LOC top-of-list violator position.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.state.persistence import FileStateReader

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import DeliverResultDict, DeliveryGatesDict

logger = structlog.get_logger(__name__)

_GATE_KEYS: tuple[str, ...] = (
    "review_warning",
    "review_advisory",
    "integration_review_block",
    "integration_review_warning",
    "untracked_warning",
    "build_gate_warning",
    "warning",
    "review_scope_block",
    "checkpoint_blocker_warning",
    "complexity_drift_warning",
)


def unpack_gate_result(gate_result: DeliveryGatesDict, results: DeliverResultDict) -> None:
    """Copy delivery-gate verdict keys from ``gate_result`` to ``results``.

    Each key in :data:`_GATE_KEYS` is conditionally promoted onto the typed
    result dict so callers see only populated fields.
    """
    for key in _GATE_KEYS:
        if key in gate_result:
            results[key] = gate_result[key]  # type: ignore[literal-required]


def step_clear_score(resolved_run: Path, results: DeliverResultDict) -> None:
    """PRD-HPO-MEAS-001 FR-5 — compute + persist CLEAR score for the run.

    One record per closed session; failure is fail-open so the scorer
    never blocks deliver completion.
    """
    try:
        from trw_mcp.scoring.clear import load_and_score_run

        session_id = str(resolved_run.name)
        clear_score = load_and_score_run(session_id, resolved_run)
        if clear_score is None:
            return
        clear_path = resolved_run / "meta" / "session_clear_score.json"
        clear_path.write_text(
            json.dumps(clear_score.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        results["clear_score"] = cast("dict[str, object]", clear_score.model_dump(mode="json"))
        logger.info(
            "clear_score_persisted",
            session_id=session_id,
            cost=clear_score.cost,
            latency=clear_score.latency,
            efficacy=clear_score.efficacy,
            assurance=clear_score.assurance,
            reliability=clear_score.reliability,
        )
    except Exception:  # justified: fail-open — CLEAR scoring must not block deliver
        logger.debug("clear_score_step_failed", exc_info=True)


def log_deliver_complete(
    *,
    resolved_run: Path | None,
    results: DeliverResultDict,
    errors: list[str],
    deferred_status: str,
    critical_elapsed: float,
) -> None:
    """Emit deliver_ok / deliver_failed / trw_deliver_complete log lines.

    Reads events.jsonl from the run dir for the events_logged field when
    available; missing/unreadable counts fall back to 0.
    """
    reader = FileStateReader()
    run_id = str(resolved_run.name) if resolved_run else ""
    events_jsonl = resolved_run / "meta" / "events.jsonl" if resolved_run else None
    events_logged = len(reader.read_jsonl(events_jsonl)) if events_jsonl and events_jsonl.exists() else 0
    if not errors:
        logger.info(
            "deliver_ok",
            run_id=run_id,
            task=str(results.get("run_path", "")),
            events_logged=events_logged,
        )
    else:
        logger.warning("deliver_failed", run_id=run_id, errors=errors)
    if deferred_status == "skipped_already_running":
        logger.warning("deliver_deferred", reason="background_thread_running")
    logger.info(
        "trw_deliver_complete",
        critical_steps=results.get("critical_steps_completed"),
        deferred=deferred_status,
        critical_elapsed=critical_elapsed,
        errors=len(errors),
    )
