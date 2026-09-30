"""Write-ahead-journal wiring for ``execute_learn``.

Belongs to the ``_learn_impl.py`` learn path. Keeps the durability plumbing
(payload capture, journal append/consume, and crash replay) out of
``execute_learn`` so that module stays under the 350 effective-LOC gate. The
durable-journal mechanics themselves live in ``state/learn_journal.py``; this
module is the thin adapter between them and the learn orchestrator.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog
from trw_memory.exceptions import DaemonError
from trw_memory.security.credentials import credential_spans, mask_low_confidence

from trw_mcp.state import learn_journal
from trw_mcp.state._learn_journal_disposition import dead_letter

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.typed_dicts import LearnResultDict

logger = structlog.get_logger(__name__)

#: One sweep's replay entry point, and the one-shot index flush that closes it.
ReplayFn = Callable[[str, dict[str, object]], str]
FlushFn = Callable[[], bool]


@dataclass(frozen=True)
class SweepContext:
    """One drain sweep's shared per-sweep work, plus its degradation truth.

    ``replay`` handles each record; ``flush`` batches index rows. ``degraded``
    remains a compatibility callback returning False: capture no longer loads
    a shared active set for quota decisions. ``index_failed`` still reports
    failed index writes, whose pending rows remain retained for retry.
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
    ``impact``/``tags``/typed fields retain raw caller inputs before validation,
    clamping, and metadata normalization.
    """
    return {k: call_locals[k] for k in JOURNAL_ARG_KEYS if k in call_locals}


#: Payload fields whose masked value is what gets stored. Every OTHER payload field is a
#: short metadata scalar or list, which must not contain a credential shape at all.
_STORED_TEXT_KEYS: frozenset[str] = frozenset({"summary", "detail", "nudge_line", "tags", "evidence", "assertions"})


def _leaves(value: object) -> Iterator[str]:
    """Every string in *value*, recursing through dicts (keys and values), lists and tuples."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(key)
            yield from _leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaves(item)


def _mask_leaves(value: object) -> Any:
    """*value* with ``mask_low_confidence`` applied to every string leaf, dict keys included."""
    if isinstance(value, str):
        return mask_low_confidence(value)
    if isinstance(value, dict):
        masked: dict[Any, Any] = {}
        next_n: dict[Any, int] = {}  # per-base repeat counter: linear even when many keys mask alike
        for key, item in value.items():
            base = new_key = _mask_leaves(key)
            # Two keys masked to one placeholder must not silently drop a field: number the repeats.
            while new_key in masked:
                next_n[base] = next_n.get(base, 1) + 1
                new_key = f"{base} #{next_n[base]}"
            masked[new_key] = _mask_leaves(item)
        return masked
    if isinstance(value, (list, tuple)):
        return [_mask_leaves(item) for item in value]
    return value


_REDACTION_MARK = re.compile(r"<REDACTED:(\w+)>")


def redaction_note(before: dict[str, object], after: dict[str, object]) -> str | None:
    """Name what :func:`store_bound_text` masked: KINDS and a count, never a value or a length (INC-119 d).

    Counts placeholders that *after* holds beyond those *before* already held, so a replayed, already-masked
    learning claims nothing.
    """

    def marks(payload: dict[str, object]) -> Counter[str]:
        return Counter(
            m for key in _STORED_TEXT_KEYS for leaf in _leaves(payload.get(key)) for m in _REDACTION_MARK.findall(leaf)
        )

    added = marks(after) - marks(before)
    if not added:
        return None
    return (
        f"{sum(added.values())} credential-shaped span(s) ({', '.join(sorted(added))}) were masked before storing; "
        "the stored learning holds <REDACTED:...> placeholders, not the original text."
    )


def queued_unavailable_error(error: Exception, learning_id: str) -> Exception:
    """*error* rewritten to be true for a learning that IS journaled (INC-119 e).

    The daemon client says "No memory was read or written" for any operation; for a learn the write-ahead journal
    already holds the accepted record, so that sentence is false and the learning's fate is unstated.
    """
    return error.__class__(
        str(error).replace("No memory was read or written", "Nothing was stored in memory")
        + f" The learning was NOT lost: it is journaled as {learning_id} in .trw/learnings/pending/ and is stored by a "
        "later session start (or `trw-mcp memory learn-drain`) once the daemon is reachable."
    )


def failed_store_result(learning_id: str, store_result: dict[str, object]) -> LearnResultDict:
    """The response for a store that returned ``error`` or ``rate_limited``; no sidecar exists for either.

    A rate-limited write used to look stored (an id and a ``sqlite://`` locator) although no row exists (INC-119 a).
    """
    failed: dict[str, object] = {
        "learning_id": learning_id,
        "path": str(store_result.get("path", f"sqlite://{learning_id}")),
        "status": str(store_result["status"]),
        "distribution_warning": "",
    }
    if failed["status"] == "rate_limited":
        failed["path"] = ""
        failed["message"] = (
            "NOT stored yet: the memory write was rate-limited (or collided with a concurrent write). The learning is "
            "held in .trw/learnings/pending/ and is stored by a later session start or `trw-mcp memory learn-drain`; "
            "retry after the delay in retry_after (seconds) if you need it now."
        )
        if isinstance(store_result.get("retry_after"), (int, float)):
            failed["retry_after"] = store_result["retry_after"]
    return cast("LearnResultDict", failed)


def store_bound_text(payload: dict[str, object]) -> LearnResultDict | None:
    """Decide BLOCK on the ORIGINAL payload, then make it the store-bound text, ONCE.

    *payload* is the whole journal snapshot (:func:`capture_journal_payload`). A high-confidence
    credential in ANY string leaf, nested assertion fields included, refuses the learning
    (``API_KEY=sk-ant-...`` is refused, not stored masked) and nothing of it is journaled. A
    placeholder-prone shape (``KEY=value``, URL password, ...) in a stored text field is masked
    in place; in any other field it also refuses, since such a field never legitimately holds
    one. Afterwards *payload* is exactly what the store receives, so a crash-replay stores what
    the original call would have; no field can bypass this because the walk covers all of them.
    """
    blocked = any(credential_spans(text) for text in _leaves(payload))
    masked = {key: _mask_leaves(value) for key, value in payload.items()}
    if blocked or any(masked[key] != payload[key] for key in payload if key not in _STORED_TEXT_KEYS):
        rejection: LearnResultDict = {
            "status": "rejected",
            "reason": "pii_blocked",
            "message": "memory entry blocked by PII policy: api_key",
        }
        return rejection
    payload.update(masked)
    return None


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


def dead_letter_refused(trw_dir: Path, config: TRWConfig, learning_id: str, reason: str) -> None:
    """Move a store-refused learning's pending record to ``dead_letter/``, credentials masked.

    The store refusing the content (PII policy, schema, poisoning) is deterministic, so the
    record is never replayable; leaving the raw payload in ``pending/`` until the next drain
    kept a refused secret on disk. No-op when journaling is disabled.
    """
    if not config.learn_journal_enabled:
        return
    path = learn_journal.pending_dir(trw_dir, config.learnings_dir) / f"{learning_id}.json"
    dead_letter(
        path,
        target_dir=learn_journal.dead_letter_dir(trw_dir, config.learnings_dir),
        reason="deterministic_rejection:store",
        status="rejected",
        error=reason,
        attempt=1,
    )


def make_sweep_replay(trw_dir: Path, config: TRWConfig) -> SweepContext:
    """Batch index persistence without distribution-only active-set loading.

    Semantic dedup remains per record, so earlier successful writes in this
    sweep are visible to later records. One context spans inline/background
    phases; flush in finally when the sweep ends. Failed index writes retain
    their rows for retry. No shared corpus cache or score reshaping is needed.
    """
    index_sink: list[Any] = []
    state = {"index_failed": False}

    def _sink_save(td: Path, entry: Any) -> Path:
        from trw_mcp.state.analytics.entries import save_learning_entry

        return save_learning_entry(td, entry, index_sink=index_sink)

    def _replay(learning_id: str, payload: dict[str, object]) -> str:
        return replay_journaled_learn(
            trw_dir,
            config,
            learning_id,
            payload,
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
        degraded=lambda: False,
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
    ``list_active`` is retained as an unused compatibility keyword; quota-only
    corpus materialization was retired from capture and replay.
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
        _save_learning_entry=save_entry,
        **kwargs,
    )
    status = result.get("status", "") if isinstance(result, dict) else ""
    return str(status)


def masked_store_bound_text(payload: dict[str, object]) -> tuple[LearnResultDict | None, str | None]:
    """:func:`store_bound_text`, plus the :func:`redaction_note` for what it masked (``None`` when nothing was)."""
    before = copy.deepcopy(payload)
    rejection = store_bound_text(payload)
    return rejection, redaction_note(before, payload)


def store_with_journal_truth(
    store: Callable[..., Any],
    trw_dir: Path,
    store_kwargs: dict[str, object],
    learning_id: str,
    *,
    journaled: bool,
) -> Any:
    """Run the store; a daemon failure on a JOURNALED learning says the learning is queued, not unwritten."""
    from trw_mcp.tools._learn_side_effects import _store_accepts_positional_trw_dir

    try:
        if _store_accepts_positional_trw_dir(store):
            return store(trw_dir, **store_kwargs)
        return store(trw_dir=trw_dir, **store_kwargs)
    except DaemonError as exc:
        if not journaled:
            raise
        raise queued_unavailable_error(exc, learning_id) from exc


def attach_response_notes(result: dict[str, Any], masking_note: str | None, store_result: dict[str, object]) -> None:
    """Add what the store did beyond the request to a recorded-learning response (INC-119 d, f).

    *masking_note*: kinds and a count of masked spans, never a value or length. ``auto_added_tags``: topic tags the
    store appended that the caller did not ask for.
    """
    if masking_note is not None:
        result["redaction_note"] = masking_note
    if store_result.get("auto_added_tags"):
        result["auto_added_tags"] = store_result["auto_added_tags"]
