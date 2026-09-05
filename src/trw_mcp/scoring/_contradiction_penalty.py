"""Contradiction-sourced negative reward (PRD-CORE-244 FR04).

Belongs to the ``_correlation.py`` facade, which re-exports both public names.
Split out for the same reason ``_proximal_correlation.py`` is: this is a
different correlation SOURCE. ``process_outcome`` reads a time-windowed recall
receipt log; this reads the result of a verification pass, applies no recency
discount, and touches only the entries that actually contradicted the tree.

It adds a SIGNAL to a reward loop that is already live — it does not build one.
The same two-phase persistence has written 111,214 dated reward events across
6,169 rows between 2026-04 and 2026-08.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import trw_mcp.scoring._utils as _su
from trw_mcp.scoring._io_boundary import (
    _batch_sync_to_sqlite,
    _default_lookup_entry,
    _PendingUpdate,
    _write_pending_entries,
)
from trw_mcp.scoring._utils import TRWConfig, get_config, safe_float, safe_int

#: ``outcome_history`` label for an FR04 penalty. Distinct from every
#: ``REWARD_MAP`` key so a contradiction is separable from a build outcome when
#: the history is read back — which is exactly how FR06 finds the entries this
#: session disproved without adding a second persistence mechanism.
CONTRADICTION_EVENT_LABEL = "assertion_contradicted"

__all__ = ["CONTRADICTION_EVENT_LABEL", "apply_contradiction_penalty"]


def _already_penalised_today(data: dict[str, object]) -> bool:
    """True when this entry already took a contradiction penalty today (FR04).

    The cooldown window is one UTC day because that is the only unit
    ``outcome_history`` can express -- its entries are stamped
    ``YYYY-MM-DD:<reward>:<label>`` by ``_update_entry_history``. Deriving the
    window from the existing record rather than adding a timestamp column keeps
    this a read of state the reward loop already writes, and makes the check
    correct across process restarts.
    """
    history = data.get("outcome_history")
    if not isinstance(history, list):
        return False
    today = datetime.now(tz=timezone.utc).date().isoformat()
    return any(
        str(item).startswith(f"{today}:") and str(item).endswith(f":{CONTRADICTION_EVENT_LABEL}") for item in history
    )


def apply_contradiction_penalty(entry_ids: list[str], trw_dir: Path) -> list[str]:
    """Apply a per-entry NEGATIVE Q observation for a failing assertion (FR04).

    ``run_verification_pass`` has always computed ``outcome.failing`` — a
    specific, per-entry, human-free negative signal — and then discarded it after
    down-ranking one recall. Meanwhile the bandit's only reward was
    ``process_outcome``'s uniform session-wide signal, and explicit feedback
    (``helpful_count``) was measured at 0 of 9,366 rows. So the system optimised
    retrieval FREQUENCY, which happily promotes a confidently-wrong memory that
    keeps matching the query.

    This adds a SIGNAL to a reward loop that is already live; it does not build a
    loop. The same persistence path has written 111,214 dated reward events
    across 6,169 rows between 2026-04 and 2026-08.

    Two deliberate differences from ``process_outcome``:

    * ``discount=1.0`` — a contradiction found now is a fact about the entry, not
      about how recently it was recalled, so no recency discount applies.
    * scoped to the entries that ACTUALLY contradicted, not to every entry in the
      recall receipt window. That is the whole point: it is the first signal in
      the system that is specific to an entry and requires no human action.
    * rate-limited to once per entry per UTC day. The same broken assertion
      surfaced five times in a session is ONE fact about the claim, not five;
      penalising per recall would quietly turn the contradiction signal into
      another retrieval-frequency term.

    ``invalidated_by`` is deliberately NOT written. ``MemoryEntry`` enforces that
    ``invalid_from`` and ``invalidated_by`` are set together and the latter names
    a SUPERSEDING record; a contradiction with no replacement has no such record,
    so writing one would fabricate a reference. Retraction is FR06's obligation.

    Args:
        entry_ids: Entries whose stored assertions failed on this pass.
        trw_dir: Path to the ``.trw`` directory.

    Returns:
        The learning IDs whose Q-values were actually updated.
    """
    # Call-time import, matching _proximal_correlation: ``_correlation`` imports
    # THIS module at its foot, so a module-level import here would be a cycle.
    from trw_mcp.scoring._correlation import _update_entry_history, _update_entry_q_values

    if not entry_ids:
        return []
    cfg: TRWConfig = get_config()
    reward = -cfg.contradiction_penalty_reward
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir

    pending_updates: list[_PendingUpdate] = []
    deltas: dict[str, tuple[float, float]] = {}
    skipped_cooldown = 0
    for lid in dict.fromkeys(entry_ids):
        entry_path, data = _default_lookup_entry(lid, trw_dir, entries_dir)
        if data is None:
            continue
        if _already_penalised_today(data):
            # One entry, one broken assertion, five recalls in a session must not
            # be five penalties: the SIGNAL is "this claim is false", and its
            # strength is a property of the claim, not of how often the retriever
            # happened to surface it. Without this, contradiction reward becomes
            # another recall-frequency term -- the same defect FR11's decay floor
            # bounds, arriving from the opposite direction.
            skipped_cooldown += 1
            continue
        q_old = safe_float(data, "q_value", safe_float(data, "impact", 0.5))
        q_new, data = _update_entry_q_values(data, reward, 1.0, cfg)
        data = _update_entry_history(data, reward, CONTRADICTION_EVENT_LABEL, cfg.learning_outcome_history_cap)
        history = data.get("outcome_history", [])
        if not isinstance(history, list):
            history = []
        pending_updates.append((lid, entry_path, data, q_new, safe_int(data, "q_observations", 0), history))
        deltas[lid] = (q_old, q_new)

    if skipped_cooldown:
        _su.logger.debug("contradiction_penalty_cooldown_skipped", entries=skipped_cooldown)
    if not pending_updates:
        return []

    # One batched write for the whole pass (NFR01): a 25-result recall costs at
    # most one additional write, not one per contradicted entry.
    updated_ids = _write_pending_entries(pending_updates)
    seen_updated = set(updated_ids)
    for lid, entry_path, _data, _q_new, _q_obs, _history in pending_updates:
        if entry_path is None and lid not in seen_updated:
            updated_ids.append(lid)
            seen_updated.add(lid)
    _batch_sync_to_sqlite(pending_updates, trw_dir)

    for lid in updated_ids:
        q_old, q_new = deltas.get(lid, (0.0, 0.0))
        # NFR05: name the entry and the resulting q_value delta.
        _su.logger.info(
            "assertion_contradiction_penalty",
            entry_id=lid,
            reward=reward,
            q_before=round(q_old, 4),
            q_after=round(q_new, 4),
            q_delta=round(q_new - q_old, 4),
        )
    return updated_ids
