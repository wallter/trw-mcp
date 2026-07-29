"""Operator-invocable learn-journal drain (PRD-INFRA-171-FR06 (e)).

Before this, the ``trw_session_start`` maintenance sweep was the journal's ONLY
consumer — and it is exactly the path writer pressure throttles. An operator
staring at a full ``.trw/learnings/pending`` had no way to flush it; a search for
``drain`` across the CLI subcommand modules returned zero matches.

``trw-mcp learn-drain`` replays pending records on demand, ignoring writer
pressure (an explicit operator act is not a best-effort background sweep). It is
non-destructive by construction: replay goes through ``execute_learn``, which is
idempotent on the original learning id, RETAINS any record whose store errors,
and MOVES ASIDE (never deletes) any record that can never be replayed.

Exit contract — the ONLY thing that earns a ``0`` is the journal actually
getting smaller:

* ``0`` — nothing was pending, or every attempted record left the pending
  directory by being persisted.
* ``1`` — records were retained for a later sweep, or ``pending_after`` did not
  fall below ``pending_before`` (e.g. every pending record is poison and the
  replay scan skips it). The ids are still on disk, so this means "try again",
  never "lost".
* ``2`` — records were dead-lettered: they will NOT be retried and need an
  operator decision. Their bodies are intact under ``.trw/learnings/dead_letter``
  with the refusal reason recorded; moving one back into ``pending/`` re-arms it.

An earlier version could exit ``0`` while ``pending_after == pending_before``,
because a replay rejected by the write-time accept gates returns ``"rejected"``
(not ``"error"``) and was booked as ``recovered`` — the accounting defect this
contract exists to make unrepresentable.
"""

from __future__ import annotations

import argparse
import json
import sys

_EXIT_OK = 0
_EXIT_TRY_AGAIN = 1
_EXIT_DEAD_LETTERED = 2


def _run_learn_drain(args: argparse.Namespace) -> None:
    """Handle ``learn-drain`` — flush the learn write-ahead journal on demand."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state import _paths, learn_journal
    from trw_mcp.tools import _learn_journal_wiring

    config = get_config()
    trw_dir = _paths.resolve_trw_dir()
    limit = int(getattr(args, "limit", None) or config.learn_journal_drain_limit)
    as_json = bool(getattr(args, "as_json", False))

    before = learn_journal.pending_count(trw_dir, learnings_dir=config.learnings_dir)
    if not config.learn_journal_enabled:
        # Replay would store the learning but ``consume_journal`` is a no-op
        # while the journal is disabled, so every record would stay pending.
        # Say so instead of reporting a drain that cannot happen.
        _emit(
            as_json,
            {"pending_before": before, "pending_after": before, "disabled": True},
            f"learn-drain: journal disabled (learn_journal_enabled=false); {before} pending, nothing drained",
        )
        if before:
            sys.exit(_EXIT_TRY_AGAIN)
        return

    result = learn_journal.drain_pending(
        trw_dir,
        lambda lid, payload: _learn_journal_wiring.replay_journaled_learn(trw_dir, config, lid, payload),
        limit=limit,
        learnings_dir=config.learnings_dir,
        max_attempts=config.learn_journal_max_replay_attempts,
    )
    after = learn_journal.pending_count(trw_dir, learnings_dir=config.learnings_dir)
    retained = int(result.get("retained", 0))
    dead_lettered = int(result.get("dead_lettered", 0))

    payload: dict[str, object] = {
        "pending_before": before,
        "pending_after": after,
        "replayed": int(result.get("replayed", 0)),
        "recovered": int(result.get("recovered", 0)),
        "dead_lettered": dead_lettered,
        "retained": retained,
        "deferred": int(result.get("deferred", 0)),
    }
    text = (
        f"learn-drain: {payload['replayed']} replayed "
        f"({payload['recovered']} recovered, {dead_lettered} dead-lettered, {retained} retained), "
        f"pending {before} -> {after}"
    )
    if dead_lettered:
        dead_dir = learn_journal.dead_letter_dir(trw_dir, config.learnings_dir)
        payload["dead_letter_dir"] = str(dead_dir)
        text += f"\n  dead-lettered records (never retried): {dead_dir}"
    _emit(as_json, payload, text)

    if dead_lettered:
        sys.exit(_EXIT_DEAD_LETTERED)
    if retained or (before and after >= before):
        sys.exit(_EXIT_TRY_AGAIN)


def _emit(as_json: bool, payload: dict[str, object], text: str) -> None:
    print(json.dumps(payload, indent=2) if as_json else text)


__all__ = ["_run_learn_drain"]
