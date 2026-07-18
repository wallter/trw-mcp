"""Typed reflection follow-through derivation (PRD-QUAL-120 FR06/FR07).

Reflection actions use the typed lifecycle in
:class:`trw_mcp.models.requirements.ReflectionActionState`. ROUTING IS FILING,
NOT CLOSURE: an approved action that links to a draft/approved PRD stays
``routed`` (or ``implementing`` once its target carries implementation state),
and debt stays open until the TARGET shows verified implementation
(implemented-family status with ``functionality_level: live``). Debt is a
derived JOIN over approved actions and current target truth — ledger prose can
never report zero while target work is open.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.requirements import ReflectionActionState
from trw_mcp.state.prd_utils import parse_frontmatter

logger = structlog.get_logger(__name__)

_IMPLEMENTED_FAMILY = frozenset({"implemented", "done", "delivered", "complete"})

# Typed open-debt reasons (bounded vocabulary).
REASON_TARGET_MISSING = "target_missing"
REASON_TARGET_NOT_IMPLEMENTED = "target_not_implemented"
REASON_TARGET_NOT_LIVE = "target_implemented_but_not_live"
REASON_UNROUTED = "approved_without_target"


@dataclass(frozen=True, slots=True)
class FollowThroughResult:
    """Derived state + open-debt reason for one reflection action."""

    action_id: str
    state: ReflectionActionState
    debt_open: bool
    reason: str = ""


def derive_reflection_action_state(
    action_id: str,
    recorded_state: str,
    target_prd_id: str,
    prds_dir: Path,
) -> FollowThroughResult:
    """Derive the typed follow-through state from CURRENT target evidence.

    - ``rejected``/``proposed`` pass through (proposed carries open debt only
      once approved, so it is not debt yet; rejected is closed by decision).
    - An approved/routed/implementing action joins to its target PRD:
      missing target        -> stays routed, debt open (``target_missing``);
      draft/approved target -> ``routed``, debt open;
      implemented target without ``functionality_level: live``
                            -> ``implementing``, debt open;
      implemented + live    -> ``verified_closed``, debt retired.
    - Recorded state is an INPUT, never the verdict: a ledger claiming
      ``verified_closed`` against a non-live target derives back to open.
    """
    normalized = recorded_state.strip().lower()
    if normalized == ReflectionActionState.REJECTED.value:
        return FollowThroughResult(action_id, ReflectionActionState.REJECTED, debt_open=False)
    if normalized == ReflectionActionState.PROPOSED.value:
        return FollowThroughResult(action_id, ReflectionActionState.PROPOSED, debt_open=False)

    if not target_prd_id.strip():
        return FollowThroughResult(action_id, ReflectionActionState.APPROVED, debt_open=True, reason=REASON_UNROUTED)
    target = prds_dir / f"{target_prd_id}.md"
    if not target.exists():
        return FollowThroughResult(
            action_id, ReflectionActionState.ROUTED, debt_open=True, reason=REASON_TARGET_MISSING
        )
    frontmatter = parse_frontmatter(target.read_text(encoding="utf-8", errors="replace"))
    status = str(frontmatter.get("status", "draft")).strip().lower()
    level = str(frontmatter.get("functionality_level", "")).strip().lower()
    if status not in _IMPLEMENTED_FAMILY:
        return FollowThroughResult(
            action_id,
            ReflectionActionState.ROUTED,
            debt_open=True,
            reason=REASON_TARGET_NOT_IMPLEMENTED,
        )
    if level != "live":
        return FollowThroughResult(
            action_id,
            ReflectionActionState.IMPLEMENTING,
            debt_open=True,
            reason=REASON_TARGET_NOT_LIVE,
        )
    return FollowThroughResult(action_id, ReflectionActionState.VERIFIED_CLOSED, debt_open=False)


DEFAULT_RECONCILE_MAX_ACTIONS = 500  # NFR04 bound — large ledgers terminate visibly


def reconcile_debt(
    actions: list[dict[str, str]],
    prds_dir: Path,
    *,
    max_actions: int = DEFAULT_RECONCILE_MAX_ACTIONS,
) -> tuple[list[FollowThroughResult], list[FollowThroughResult]]:
    """Join approved actions to target truth; return (open_debt, closed).

    Bounded (NFR04): only the provided actions and their named targets are
    read — no unrelated artifact trees are scanned. Prefer
    :func:`reconcile_debt_bounded` when the caller needs the typed
    counts/truncation report; this wrapper keeps the FR07 signature.
    """
    report = reconcile_debt_bounded(actions, prds_dir, max_actions=max_actions)
    open_debt = cast("list[FollowThroughResult]", report["open"])
    closed = cast("list[FollowThroughResult]", report["closed"])
    return open_debt, closed


def reconcile_debt_bounded(
    actions: list[dict[str, str]],
    prds_dir: Path,
    *,
    max_actions: int = DEFAULT_RECONCILE_MAX_ACTIONS,
) -> dict[str, object]:
    """NFR04 reconciliation: expose counts AND truncation, never hide skips.

    At most ``max_actions`` actions are evaluated; everything beyond the bound
    is reported as ``skipped_actions`` (ids) with ``truncated=True`` so a large
    fixture terminates without silently dropping targets. Only the provided
    actions and their named target PRDs are read — no unrelated artifact trees.
    """
    if max_actions <= 0:
        raise ValueError("max_actions must be positive")
    evaluated = actions[:max_actions]
    skipped = actions[max_actions:]
    open_debt: list[FollowThroughResult] = []
    closed: list[FollowThroughResult] = []
    for action in evaluated:
        result = derive_reflection_action_state(
            str(action.get("action_id", "")),
            str(action.get("state", "")),
            str(action.get("target_prd", "")),
            prds_dir,
        )
        (open_debt if result.debt_open else closed).append(result)
    if skipped:
        logger.warning(
            "reflection_debt_reconciliation_truncated",
            total=len(actions),
            evaluated=len(evaluated),
            skipped=len(skipped),
        )
    return {
        "open": open_debt,
        "closed": closed,
        "total_actions": len(actions),
        "evaluated_count": len(evaluated),
        "truncated": bool(skipped),
        "skipped_actions": [str(action.get("action_id", "")) for action in skipped],
    }
