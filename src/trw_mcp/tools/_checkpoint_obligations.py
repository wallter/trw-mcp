"""Ceremony-obligation rendering for the ``checkpoint.py`` facade.

Belongs to the ``checkpoint.py`` facade. Re-exported there for back-compat.

WHY THIS IS ITS OWN MODULE, AND WHY EVERY CONSEQUENCE IS DERIVED. The list this
module renders is written into ``.trw/context/compact_instructions.txt`` and the
``pending_ceremony`` field of ``.trw/context/pre_compact_state.json`` — the two
artifacts the NEXT session reads first, immediately after a context compaction.
That is the moment an agent has the least context with which to check a claim,
so it is the worst possible place to assert a gate that will not fire.

The list previously carried three FLAT consequence strings, none of which the
code consulted:

- ``trw_build_check() — required before delivery``. False for most runs. Under
  the shipped ``deliver_gate_mode=block_coding`` a missing build check blocks
  only coding/rca/eval; a docs, research, planning, or unknown-task-type run
  delivers successfully without one. This is the same over-claim
  ``_orchestration_gate_scan._build_gate_would_block`` was written to correct in
  the ``trw_status`` summary — restated flat here, where nothing had caught it.
- ``trw_review() — recommended before delivery``. Wrong in the other direction:
  under ``review_gate_mode=block`` a STANDARD/COMPREHENSIVE run with no review
  is HARD-BLOCKED, and "recommended" invites the agent to skip it and hit a wall.
- ``trw_deliver() — required at session end``. No code enforces this one at all.

CONSTITUTION HB-1 forbids asserting a consequence the system does not enforce,
and the value hierarchy puts Truthfulness above the convenience of a fixed
string. So each consequence is now resolved through THE SAME predicate the
deliver path uses — imported, never re-derived — and the deliver obligation is
restated on the ground that actually holds it up (VISION Principle 5: nothing
else persists the session's learnings), not a gate that does not exist.

Reusing ``_orchestration_gate_scan`` rather than re-reading config here is
deliberate: ``trw_status``'s ``deliver_gate_summary``, ``trw_deliver``'s
enforcement, and this post-compaction artifact now share one source of truth,
so a future change to gate policy cannot leave this surface behind.

Fail-open throughout: an unresolvable gate degrades to the WEAKER (advisory)
wording. A preview that cannot prove a block must never claim one.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

#: Obligation rows that carry no consequence claim — the description is a
#: statement of fact about ceremony state, so there is nothing to derive.
_SESSION_STARTED = ("session_started", "trw_session_start()", "not yet called")

#: VISION Principle 5, not a gate: no tool blocks on a missing ``trw_deliver``.
#: The obligation is real (a session that ends without persisting material
#: discoveries is a failure) so it is stated as the consequence that actually
#: follows — the learnings are lost — rather than as an enforcement that is not
#: there. Keeping the row is required: nudges are removed only by the operator.
_DELIVER_DESC = "required at session end — nothing else persists this session's learnings"

#: Wording used when a gate is proven to hard-block, and when it is not. Both
#: keep the obligation visible; only the CONSEQUENCE differs, so nothing is
#: hidden from the recovering session.
_BLOCKS = "blocks trw_deliver for this run"
_ADVISORY_BUILD = "recommended before delivery (the deliver gate is advisory for this run's task type)"
_ADVISORY_REVIEW = "recommended before delivery (not enforced for this run)"


def _build_consequence(run_dir: Path | None) -> str:
    """Resolve the build-check consequence through the deliver-path predicate.

    Delegates to :func:`_orchestration_gate_scan._build_gate_would_block`, which
    reads the run's ``task_type``, applies ``deliver_gate_task_type_overrides``,
    and calls ``resolve_deliver_gate_decision`` — the single function that
    decides this at deliver time. ``missing_build=True`` is correct by
    construction: this consequence is only rendered for an obligation that is
    still pending, i.e. no build check has been recorded.
    """
    try:
        from trw_mcp.tools._orchestration_gate_scan import _build_gate_would_block

        if _build_gate_would_block(run_dir, missing_build=True):
            return _BLOCKS
    # INFO, not debug: this is a DEGRADED path, and the degradation is invisible
    # in the artifact (the agent simply reads the weaker wording). A default
    # Claude Code install ships no --debug, where a debug event is dropped before
    # any processor runs — so debug here would destroy the only record that the
    # consequence was guessed rather than resolved.
    except Exception:  # justified: fail-open — an unprovable block must not be claimed
        logger.info("build_obligation_consequence_degraded", exc_info=True)
    return _ADVISORY_BUILD


def _review_consequence(run_dir: Path | None, events: list[dict[str, object]]) -> str:
    """Resolve the review consequence through the deliver-path predicate.

    Delegates to :func:`_orchestration_gate_scan._review_gate_would_block`, which
    mirrors the exact hard-block set ``trw_deliver`` computes (verdict=block on a
    STANDARD+ run, ``review_gate_mode=block`` with no review, integration review,
    and the >5-modified-files scope rule).
    """
    try:
        from trw_mcp.tools._orchestration_gate_scan import _review_gate_would_block

        if _review_gate_would_block(run_dir, events):
            return _BLOCKS
    except Exception:  # justified: fail-open — an unprovable block must not be claimed
        logger.info("review_obligation_consequence_degraded", exc_info=True)  # INFO: see _build_consequence
    return _ADVISORY_REVIEW


def compute_pending_ceremony(
    ceremony_state: dict[str, object],
    *,
    run_dir: Path | None = None,
    events: list[dict[str, object]] | None = None,
) -> list[str]:
    """Render the still-pending ceremony obligations, consequences derived.

    Order is the ceremony order an agent works through, so the recovering
    session reads its remaining obligations in the sequence it must do them.

    Args:
        ceremony_state: the ceremony-progress mapping; a key that is falsy or
            absent means that obligation is still pending.
        run_dir: the active run, used to resolve the gate consequences. When
            ``None`` (no resolvable run) both gates degrade to advisory wording
            rather than claiming a block that cannot be evaluated.
        events: the run's event records, required by the review scope rule.
            Defaults to empty, which can only weaken the claim, never strengthen it.
    """
    pending: list[str] = []
    if not ceremony_state.get(_SESSION_STARTED[0]):
        pending.append(f"{_SESSION_STARTED[1]} — {_SESSION_STARTED[2]}")
    # Consequences are resolved lazily, per pending row: each one costs a
    # run.yaml read, and compaction is imminent when this runs. An obligation
    # already satisfied needs no consequence computed at all.
    if not ceremony_state.get("build_checked"):
        pending.append(f"trw_build_check() — {_build_consequence(run_dir)}")
    if not ceremony_state.get("review_done"):
        pending.append(f"trw_review() — {_review_consequence(run_dir, events or [])}")
    if not ceremony_state.get("delivered"):
        pending.append(f"trw_deliver() — {_DELIVER_DESC}")
    return pending
