"""Formation-level deliver gate for the ORCHESTRATOR run (PRD-CORE-265-FR11).

An orchestrator that passes every single-run gate can still be delivering while
three peers are mid-implementation — a completion claim that outruns its
evidence, which the value hierarchy ranks above velocity. This computes the
waiting list; ``_deliver_gate_dispatch`` turns it into a STRUCTURED block behind
the same PRD-CORE-191 acceptable-failure record every other hard gate honours.

SCOPE, STATED NARROWLY. It fires ONLY on the run that owns the manifest. A
member delivering on its own is untouched — the gate keys on being the
orchestrator — so it cannot deadlock a formation by blocking the very members it
is waiting for.

WHAT COUNTS AS FINISHED. ``delivered``, ``abandoned``, and ``reassigned``, and
nothing else. A ``pending`` member never joined, produced no run, and therefore
produced no evidence to wait on. A member whose entry SAYS ``delivered`` but
whose own run carries no delivery record is treated as NON-terminal: the stamp
is the member's self-report, and this gate re-checks it against the run rather
than trusting it, which is the whole reason FR11 exists.

FAIL-CLOSED (NFR02). An unreadable or schema-invalid manifest BLOCKS and names
the file and the parse error. "I could not read the coordination artifact" must
never resolve to "everyone is finished" — that is the reassuring fallback this
PRD was written to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["FormationGateOutcome", "evaluate_formation_gate"]


@dataclass(frozen=True)
class FormationGateOutcome:
    """Whether orchestrator delivery must block, and what to say."""

    should_block: bool = False
    message: str = ""
    warning: str = ""


def evaluate_formation_gate(resolved_run: Path | None) -> FormationGateOutcome:
    """Decide the formation gate for *resolved_run*."""
    if resolved_run is None:
        return FormationGateOutcome()
    from trw_mcp.formation import FormationError, load, settings, status

    try:
        context = load(resolved_run)
    except FormationError as exc:
        return FormationGateOutcome(
            should_block=True,
            message=(
                f"Formation gate: the formation manifest could not be read, so member completion "
                f"cannot be established. {exc}"
            ),
        )
    if context is None or not context.is_orchestrator:
        return FormationGateOutcome()

    board = status(context=context)
    pending = board.non_terminal if board is not None else []
    if not pending:
        return FormationGateOutcome()
    listed = "; ".join(f"{member_id} ({member_status})" for member_id, member_status in pending)
    sentence = (
        f"Formation gate: formation {context.manifest.formation_id!r} has "
        f"{len(pending)} member(s) that are neither delivered, abandoned, nor reassigned: {listed}. "
        "Wait for them, or record the outcome by orchestrator revision "
        "(trw-mcp formation status shows their current state)."
    )
    if settings().deliver_gate == "advisory":
        logger.info("formation_deliver_gate_advisory", run=str(resolved_run), members=len(pending))
        return FormationGateOutcome(warning=sentence)
    return FormationGateOutcome(should_block=True, message=sentence)
