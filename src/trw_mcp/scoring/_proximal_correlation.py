"""Proximal nudge->action reward application (ledger UF-026).

Belongs to the ``_correlation.py`` facade. Re-exported there, and from
``trw_mcp.scoring``, for a single import point.

The policy counterpart to ``proximal_reward.detect_proximal_signals`` (the
detector): the detector genuinely ran and produced real nudge->action records
for months, but every caller assigned them to ``result["proximal_signals"]``, a
reporting field nothing read back. The signal was computed and discarded.

Kept out of ``_correlation.py`` because that module is a *recall-receipt*
correlation facade under a 350-raw-line gate, and this is a different
correlation source (nudge attribution, no recency window).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import trw_mcp.scoring._utils as _su
from trw_mcp.scoring._io_boundary import (
    _batch_sync_to_sqlite,
    _default_lookup_entry,
    _PendingUpdate,
    _write_pending_entries,
)
from trw_mcp.scoring._reward_resolution import _resolve_event_reward
from trw_mcp.scoring._utils import TRWConfig, get_config, safe_int

if TYPE_CHECKING:
    from pathlib import Path

    from trw_mcp.scoring._correlation import EntryLookupFn
    from trw_mcp.scoring.proximal_reward import ProximalSignal

# Ledger UF-026: proximal ``signal_type`` -> the canonical outcome event whose
# reward it inherits, so nudge->action rewards ride the SAME REWARD_MAP scale as
# every other outcome instead of introducing an unanchored magic constant.
_PROXIMAL_SIGNAL_EVENTS: dict[str, str] = {"test_rerun": "tests_passed"}

__all__ = ["apply_proximal_rewards"]


def apply_proximal_rewards(
    trw_dir: Path,
    signals: list[ProximalSignal],
    *,
    lookup_fn: EntryLookupFn | None = None,
) -> list[str]:
    """Move Q-values for learnings a nudge demonstrably preceded (ledger UF-026).

    ``detect_proximal_signals`` genuinely runs and produces real nudge->action
    records, but every caller assigned them to a reporting field and nothing
    read them back — the reward signal was computed and discarded.

    Distinct from :func:`process_outcome`, which correlates by *recall receipt*
    inside a time window and cannot see nudge attribution. Here the learning id
    comes straight off the ``nudge_shown`` event, so the update is applied to
    exactly the learnings that were surfaced, with no recency discount to
    estimate: a proximal signal is already defined as "within ``max_offset``
    events".

    ``signal_type`` resolves its reward through the shared ``REWARD_MAP``
    (``test_rerun`` -> ``tests_passed``) so proximal rewards cannot drift away
    from the outcome scale the rest of the scorer uses. Synthetic ``SYS-nudge-*``
    ids for pool nudges with no learning anchor simply miss the lookup and are
    skipped.

    Returns the learning IDs whose Q-values were updated.
    """
    if not signals:
        return []
    cfg: TRWConfig = get_config()
    effective_lookup = lookup_fn if lookup_fn is not None else _default_lookup_entry

    rewards: dict[str, tuple[float, str]] = {}
    for signal in signals:
        learning_id = str(signal.get("learning_id", ""))
        if not learning_id:
            continue
        reward, label = _resolve_event_reward(_PROXIMAL_SIGNAL_EVENTS.get(signal.get("signal_type", ""), ""))
        if reward is None:
            continue
        # One update per learning per pass; keep the strongest observed signal.
        existing = rewards.get(learning_id)
        if existing is None or reward > existing[0]:
            rewards[learning_id] = (reward, f"proximal_{label}")

    pending_updates = _entry_updates(trw_dir, rewards, lookup_fn=effective_lookup, cfg=cfg)
    if not pending_updates:
        return []
    updated_ids = _write_pending_entries(pending_updates)
    seen = set(updated_ids)
    for lid, entry_path, _data, _q, _obs, _hist in pending_updates:
        if entry_path is None and lid not in seen:
            updated_ids.append(lid)
            seen.add(lid)
    _batch_sync_to_sqlite(pending_updates, trw_dir)
    _su.logger.info(
        "proximal_reward_applied",
        signal_count=len(signals),
        updated_count=len(updated_ids),
    )
    return updated_ids


def _entry_updates(
    trw_dir: Path,
    rewards: dict[str, tuple[float, str]],
    *,
    lookup_fn: EntryLookupFn,
    cfg: TRWConfig,
) -> list[_PendingUpdate]:
    """Compute (no writes) the Q-value/history update for each rewarded learning."""
    from trw_mcp.scoring._correlation import _update_entry_history, _update_entry_q_values

    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    pending: list[_PendingUpdate] = []
    for learning_id, (reward, label) in rewards.items():
        entry_path, data = lookup_fn(learning_id, trw_dir, entries_dir)
        if data is None:
            # Synthetic SYS-nudge-* ids (pool nudges with no learning anchor)
            # legitimately miss the store; there is nothing to reward.
            continue
        q_new, data = _update_entry_q_values(data, reward, 1.0, cfg)
        data = _update_entry_history(data, reward, label, cfg.learning_outcome_history_cap)
        history = data.get("outcome_history", [])
        if not isinstance(history, list):
            history = []
        pending.append((learning_id, entry_path, data, q_new, safe_int(data, "q_observations", 0), history))
    return pending
