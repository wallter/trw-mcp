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

CALLER-VERIFIED SELF-EXCLUSION (PRD-FIX-149 review R1). The caller's own slot
is excluded only when THIS CALL is verified as the owning session's — see
:func:`trw_mcp.formation.own_slot_if_caller`. Without ``call_ctx`` (or without
a matching pin), the structural ``context.member_id`` match is NOT trusted:
a peer that passes ``run_path=<orchestrator run>`` explicitly must be treated
as an ordinary, blockable, non-terminal member, not as the orchestrator itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext

logger = structlog.get_logger(__name__)

__all__ = ["FormationGateOutcome", "evaluate_formation_gate"]


@dataclass(frozen=True)
class FormationGateOutcome:
    """Whether orchestrator delivery must block, and what to say."""

    should_block: bool = False
    message: str = ""
    warning: str = ""


def evaluate_formation_gate(
    resolved_run: Path | None,
    *,
    call_ctx: TRWCallContext | None = None,
) -> FormationGateOutcome:
    """Decide the formation gate for *resolved_run*.

    *call_ctx* is the CALLING session's own resolved identity (never derived
    from ``resolved_run`` itself, which may be an explicit, caller-supplied
    ``run_path``) — see the module docstring's R1 note. Omitting it is safe:
    the caller's own slot then simply never qualifies for exclusion, which is
    the conservative (fail-closed) answer, never the permissive one.
    """
    if resolved_run is None:
        return FormationGateOutcome()
    from trw_mcp.formation import FormationError, load, own_slot_if_caller, settings, status
    from trw_mcp.state._paths_pin_mgmt import get_pinned_run

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

    pinned_run = get_pinned_run(context=call_ctx) if call_ctx is not None else None
    verified_self_id = own_slot_if_caller(
        context,
        resolved_run,
        pinned_run=pinned_run,
        session_id=call_ctx.session_id if call_ctx is not None else None,
    )
    board = status(context=context)
    # The caller's own slot never blocks the caller: it is the delivery in
    # progress, stamped right after this gate passes (PRD-FIX-149 FR01/FR02),
    # but ONLY once verified as this session's own (review R1, above).
    pending = [row for row in (board.non_terminal if board else []) if row[0] != verified_self_id]
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
