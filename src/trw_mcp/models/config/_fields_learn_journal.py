"""Learn write-ahead-journal durability + drain fields.

Its own domain mixin rather than more lines in ``_fields_memory.py``, which sits
against the 200-line domain-mixin gate — the same split ``_fields_memory_truth``
and ``_fields_boot_maintenance`` already took. Admission records live in
``_field_admission_registry`` and ``_field_admission_drain_budget``.
"""

from __future__ import annotations

from pydantic import Field


class _LearnJournalFields:
    """Learn-journal domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Learn write-ahead journal (durability) --
    # An ACCEPTED trw_learn is fsync'd to `.trw/learnings/pending/<id>.json`
    # BEFORE the slow pre-store pipeline that a 120s client tool timeout can cut
    # short, then replayed on the next session_start sweep if the store was never
    # reached. Rationale + interactions: see `_field_admission.py`. Kill switch
    # for operators who want the legacy (loss-prone) path:
    learn_journal_enabled: bool = True
    # Max pending records replayed per session_start sweep. Bounds the recovery
    # cost so a large backlog cannot stall boot; the remainder drains next sweep.
    learn_journal_drain_limit: int = Field(default=50, ge=1)
    # Retry budget for a TRANSIENTLY failing replay (backend down, DB lock);
    # past it the record moves to `.trw/learnings/dead_letter/` instead of being
    # re-attempted forever. A DETERMINISTIC refusal (accept-gate rejection,
    # invalid enum) bypasses the budget and moves aside immediately; 0 disables
    # the budget for transient failures only:
    learn_journal_max_replay_attempts: int = Field(default=5, ge=0)
    # PRD-FIX-130-FR01 wall-clock bound on the session_start drain (a count
    # cannot bound bimodal per-record cost: 77 records took 353,590 ms). Soft —
    # checked between records; the remainder continues on a background thread.
    # 0 = background-only, the config-only rollback. See _field_admission_drain_budget:
    learn_journal_drain_budget_ms: int = Field(default=3000, ge=0, le=120000)
