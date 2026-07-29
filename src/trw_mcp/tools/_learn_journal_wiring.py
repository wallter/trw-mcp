"""Write-ahead-journal wiring for ``execute_learn``.

Belongs to the ``_learn_impl.py`` learn path. Keeps the durability plumbing
(payload capture, journal append/consume, and crash replay) out of
``execute_learn`` so that module stays under the 350 effective-LOC gate. The
durable-journal mechanics themselves live in ``state/learn_journal.py``; this
module is the thin adapter between them and the learn orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.state import learn_journal

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

# The subset of ``execute_learn`` parameters that must be persisted to faithfully
# re-run an accepted learning on replay. Injected test-seam deps (``_adapter_store``
# …), the resolved ``trw_dir``/``config``, and the un-serializable ``is_solution_fn``
# are deliberately excluded — replay resolves those from the live process.
JOURNAL_ARG_KEYS: frozenset[str] = frozenset(
    {
        "summary",
        "detail",
        "tags",
        "evidence",
        "impact",
        "shard_id",
        "source_type",
        "source_identity",
        "client_profile",
        "model_id",
        "consolidated_from",
        "assertions",
        "type",
        "nudge_line",
        "expires",
        "confidence",
        "task_type",
        "domain",
        "phase_origin",
        "phase_affinity",
        "team_origin",
        "protection_tier",
        "session_id",
        "scope",
    }
)


def capture_journal_payload(call_locals: dict[str, object]) -> dict[str, object]:
    """Snapshot the replayable original args from ``execute_learn``'s locals.

    Called at function entry (before any local mutation), so the captured
    ``impact``/``tags``/typed fields are the RAW caller inputs — replay must not
    persist post-calibration values (``calibrate_impact`` is not idempotent).
    """
    return {k: call_locals[k] for k in JOURNAL_ARG_KEYS if k in call_locals}


def journal_accepted(
    trw_dir: Path,
    config: TRWConfig,
    learning_id: str,
    payload: dict[str, object],
    *,
    from_journal: bool,
) -> None:
    """Journal an accepted learning before the slow pre-store pipeline.

    No-op when the journal is disabled or when this call is ITSELF a replay
    (``from_journal``) — the on-disk record already exists in that case.
    """
    if from_journal or not config.learn_journal_enabled:
        return
    learn_journal.journal_pending(trw_dir, learning_id, payload, learnings_dir=config.learnings_dir)


def consume_journal(trw_dir: Path, config: TRWConfig, learning_id: str) -> None:
    """Consume a pending record after a confirmed terminal outcome.

    Called on every path where the learning is durable or intentionally handled
    (stored / deduped / quarantined) — NEVER on a store error, which must retain
    the record for the next replay. No-op when journaling is disabled.
    """
    if not config.learn_journal_enabled:
        return
    learn_journal.consume_pending(trw_dir, learning_id, learnings_dir=config.learnings_dir)


def replay_journaled_learn(
    trw_dir: Path,
    config: TRWConfig,
    learning_id: str,
    payload: dict[str, object],
) -> str:
    """Re-run one journaled learning through ``execute_learn``, idempotently.

    Passes the ORIGINAL ``learning_id`` back so exact-content/semantic dedup
    collapses the replay against any row a partial earlier attempt already wrote
    (exactly-once). ``execute_learn`` owns consuming the journal file on a
    durable/handled outcome and retaining it on a store error. Returns the
    terminal status string so the drain driver can count outcomes.
    """
    from trw_mcp.tools._learn_impl import execute_learn
    from trw_mcp.tools._learning_module_helpers import _coerce_learn_type

    kwargs: dict[str, Any] = {k: v for k, v in payload.items() if k in JOURNAL_ARG_KEYS}
    summary = str(kwargs.pop("summary", ""))
    detail = str(kwargs.pop("detail", ""))
    # Replay must be EQUIVALENT to the original tool call. `trw_learn` coerces
    # advertised type aliases (e.g. the docstring-advertised "gotcha") to a real
    # MemoryType *at the tool layer* before enum validation; `execute_learn` does
    # not. Without this, a payload carrying an alias raises
    # `ValueError: 'gotcha' is not a valid MemoryType` inside the store, the D8
    # contract RETAINS the record on a store error, and the same record then fails
    # identically on every future drain — permanent journal poison that can never
    # be consumed. Observed 2026-07-25: 6 of 9 pending records retained this way.
    if "type" in kwargs:
        kwargs["type"] = _coerce_learn_type(str(kwargs["type"]))
    result = execute_learn(
        summary=summary,
        detail=detail,
        trw_dir=trw_dir,
        config=config,
        _replay_learning_id=learning_id,
        _from_journal=True,
        **kwargs,
    )
    status = result.get("status", "") if isinstance(result, dict) else ""
    return str(status)
