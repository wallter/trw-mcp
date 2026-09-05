"""Write-ahead-journal wiring for ``execute_learn``.

Belongs to the ``_learn_impl.py`` learn path. Keeps the durability plumbing
(payload capture, journal append/consume, and crash replay) out of
``execute_learn`` so that module stays under the 350 effective-LOC gate. The
durable-journal mechanics themselves live in ``state/learn_journal.py``; this
module is the thin adapter between them and the learn orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.state import learn_journal

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

#: One sweep's replay entry point, and the one-shot index flush that closes it.
ReplayFn = Callable[[str, dict[str, object]], str]
FlushFn = Callable[[], bool]


@dataclass(frozen=True)
class SweepContext:
    """One drain sweep's shared per-sweep work, plus its degradation truth.

    ``replay`` is the per-record entry point; ``flush`` writes the batched index
    rows exactly once and returns False when that write failed. ``degraded``
    reports that the shared active set could not be materialized, so the sweep's
    soft-cap and distribution decisions ran against an empty set rather than the
    real one — a condition the caller must surface rather than memoize silently
    (FIX130-09). ``index_failed`` reports the same for the index write, whose
    rows are RETAINED in the sink so nothing is discarded (FIX130-10).
    """

    replay: ReplayFn
    flush: FlushFn
    degraded: Callable[[], bool]
    index_failed: Callable[[], bool]


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


def make_sweep_replay(trw_dir: Path, config: TRWConfig) -> SweepContext:
    """Build one drain sweep's replay function plus the flush that closes it.

    PRD-FIX-130-FR03. Two costs were being paid PER RECORD that are really per
    SWEEP, and together they dominated a journal drain:

    * the whole-file read-modify-write of ``learnings/index.yaml`` under a lock
      (measured 623-757 ms idle, 2.27 s under concurrent boot load, 53-69% of a
      stored record's replay). The sweep now collects entries in an index sink
      and :func:`flush` writes them in ONE locked round-trip;
    * materializing every active row into validated models for the soft cap and
      the distribution enforcer (211-521 ms against 4,824 rows). The sweep
      resolves that list at most once and hands the SAME value to every record.

    Both ride the injection parameters ``execute_learn`` already exposes, so the
    interactive ``trw_learn`` path is untouched: no sink, no shared set, exactly
    the pre-change per-record behaviour.

    **Semantic dedup is deliberately NOT hoisted.** The duplicate probe reads the
    backend on every record so a record stored earlier in this sweep is visible
    to a later duplicate. Caching it would let one sweep store N copies of one
    learning — the exact failure the journal's exactly-once contract exists to
    prevent.

    **Known, bounded staleness**: a record stored earlier in the sweep is not in
    the shared active set, so the soft cap and the distribution enforcer see the
    sweep's STARTING set. The undercount is at most the per-sweep replay limit
    (default 50) against a measured 4,824-row active set — under 1.04% — and it
    moves an advisory threshold, never a stored value.

    **One context spans BOTH phases of a split sweep** (FIX130-04). When the FR01
    wall-clock budget stops the inline phase, the same context is handed to the
    FR02 background continuation, which flushes it once at the end. A sweep that
    is split therefore still pays exactly one index read-modify-write and one
    active-set materialization, which is what FR03 promises; building a second
    context in the continuation quietly doubled both.

    Returns:
        A :class:`SweepContext`. Call ``flush`` exactly once when the sweep ends,
        in a ``finally``, or the sweep's entries never reach the index.
    """
    index_sink: list[Any] = []
    active_memo: list[list[dict[str, object]]] = []
    state = {"active_degraded": False, "index_failed": False}

    def _shared_active(_td: Path) -> list[dict[str, object]]:
        if active_memo:
            return active_memo[0]
        from trw_mcp.state.memory_adapter import list_active_learnings

        # FIX130-09: one listing failure used to be memoized as a valid EMPTY
        # set for the whole sweep, silently moving every record's soft-cap and
        # distribution decision, at DEBUG. Retry once, then say so loudly and
        # mark the sweep degraded so the caller can report it.
        for attempt in (1, 2):
            try:
                resolved = list_active_learnings(trw_dir)
            except Exception:  # justified: fail-open, matches execute_learn's own suppression
                if attempt == 1:
                    logger.debug("drain_shared_active_set_retry", exc_info=True)
                    continue
                logger.warning(
                    "drain_shared_active_set_unavailable",
                    outcome="soft_cap_and_distribution_decisions_degraded",
                    exc_info=True,
                )
                state["active_degraded"] = True
                active_memo.append([])
                return active_memo[0]
            active_memo.append(resolved)
            return active_memo[0]
        return []

    def _sink_save(td: Path, entry: Any) -> Path:
        from trw_mcp.state.analytics.entries import save_learning_entry

        return save_learning_entry(td, entry, index_sink=index_sink)

    def _replay(learning_id: str, payload: dict[str, object]) -> str:
        return replay_journaled_learn(
            trw_dir,
            config,
            learning_id,
            payload,
            list_active=_shared_active,
            save_entry=_sink_save,
        )

    def _flush() -> bool:
        """Write the sweep's collected index rows once. False = write failed.

        FIX130-10: the rows STAY in the sink on failure. Clearing first meant a
        failed write silently discarded the whole batched projection while the
        backend rows it described were already durable — the index simply lost
        them, permanently and invisibly on the background path.
        """
        if not index_sink:
            return True
        from trw_mcp.state.analytics.entries import update_learning_index_batch

        rows = list(index_sink)
        for attempt in (1, 2):
            try:
                update_learning_index_batch(trw_dir, rows)
            except Exception:  # justified: the backend rows are already durable; only the index missed out
                if attempt == 1:
                    logger.debug("learn_journal_index_flush_retry", entries=len(rows), exc_info=True)
                    continue
                logger.warning(
                    "learn_journal_index_flush_failed",
                    entries=len(rows),
                    outcome="index_not_updated_rows_retained",
                    exc_info=True,
                )
                state["index_failed"] = True
                return False
            index_sink.clear()
            state["index_failed"] = False
            return True
        return False

    return SweepContext(
        replay=_replay,
        flush=_flush,
        degraded=lambda: bool(state["active_degraded"]),
        index_failed=lambda: bool(state["index_failed"]),
    )


def replay_journaled_learn(
    trw_dir: Path,
    config: TRWConfig,
    learning_id: str,
    payload: dict[str, object],
    *,
    list_active: Any = None,
    save_entry: Any = None,
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
        _list_active_learnings=list_active,
        _save_learning_entry=save_entry,
        **kwargs,
    )
    status = result.get("status", "") if isinstance(result, dict) else ""
    return str(status)
