"""Non-authoritative bootstrap actions: announce, withdraw, discover (PRD-CORE-274-FR18).

Belongs to the ``trw_mcp.comms`` facade; ``peers()`` dispatches here.

These three actions grant nothing, so they run at the FR17 BOOTSTRAP root (the
main worktree when the git back-pointer is consistent) and need only a pinned
run, not formation membership. Identity is taken from the trusted call context
and the pin store, never from an argument. Nothing identifying is returned: not
the pin, not a path, not a body. Only the orchestrator of a formation at this
root sees the live candidates, and then only handle, client, worktree label and
age.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.comms._identity import IdentityError, resolve_snapshot
from trw_mcp.formation import (
    LIVE_CANDIDATE_STATES,
    TERMINAL_STATUSES,
    CandidateError,
    CandidateState,
    CoordinationRoot,
    FormationError,
    announce_candidate,
    authority_roots,
    bootstrap_root,
    candidate_for,
    live_candidates,
    read_manifest,
    registered_formations,
    set_candidate_state,
)
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths_pin_mgmt import get_pinned_run

if TYPE_CHECKING:
    from fastmcp import Context

    from trw_mcp.models.config import TRWConfig

_logger = structlog.get_logger(__name__)
#: Bounds the discover page: a root with more open formations than this is an
#: operator problem, and the response must stay small (tool response budget).
DISCOVER_FORMATION_LIMIT = 16
BOOTSTRAP_ACTIONS = frozenset({"announce", "withdraw", "discover"})


def _refused(reason: str, detail: str) -> dict[str, Any]:
    return {"status": "refused", "reason": reason, "detail": detail}


def bootstrap(action: str, ctx: Context | None, config: TRWConfig) -> dict[str, Any]:
    """Run one bootstrap action; refusals carry a closed reason and the next action."""
    call_context = build_call_context(ctx)
    run_path = get_pinned_run(context=call_context)
    if run_path is None:
        return _refused("no_pinned_run", "pin a run first: trw_init, or trw_adopt_run to resume one")
    root = bootstrap_root()
    try:
        if action == "announce":
            return _announce(root, call_context.session_id, run_path, config)
        if action == "withdraw":
            return _withdraw(root, call_context.session_id, run_path)
        return _discover(root, ctx)
    except CandidateError as exc:
        return _refused(exc.reason, "the candidate registry is full; retry after candidates expire or withdraw")
    except FormationError:
        _logger.info("comms_bootstrap_store_unreadable", action=action)
        return _refused("formation_unavailable", "the formation store is unreadable; ask the operator to repair it")


def _announce(root: CoordinationRoot, pin_key: str, run_path: Path, config: TRWConfig) -> dict[str, Any]:
    from trw_mcp.state.source_detection import detect_client_profile

    found = announce_candidate(
        root.trw_dir,
        pin_key=pin_key,
        run_path=run_path,
        worktree=root.worktree,
        client=detect_client_profile() or "unknown",
        ttl_seconds=config.comms_candidate_ttl_seconds,
    )
    return {
        "status": "ok",
        "state": "candidate",
        "candidate_id": found.candidate_id,
        "expires_in_seconds": config.comms_candidate_ttl_seconds,
    }


def _withdraw(root: CoordinationRoot, pin_key: str, run_path: Path) -> dict[str, Any]:
    mine = candidate_for(root.trw_dir, pin_key, run_path)
    if mine is not None and mine.state in LIVE_CANDIDATE_STATES:
        set_candidate_state(root.trw_dir, mine.candidate_id, CandidateState.WITHDRAWN)
    return {"status": "ok", "state": "opted_out"}


def _orchestrator_formation(ctx: Context | None) -> str | None:
    """The formation this caller orchestrates, bound at the bootstrap root or at its own root.

    Its own root covers an orchestrator whose run lives in a linked worktree with no
    FR17 record for itself (lane C review, D3); either way it must bind under FR01.
    """
    for candidate_root in authority_roots():
        try:
            snapshot = resolve_snapshot(ctx, trw_dir=candidate_root.trw_dir, project_root=candidate_root.project_root)
        except IdentityError:
            # trw-fail-silent-allow: a non-member discovers formations only; candidates stay hidden
            continue
        if snapshot.binding.is_orchestrator:
            return snapshot.binding.formation_id
    return None


def _discover(root: CoordinationRoot, ctx: Context | None) -> dict[str, Any]:
    formations: list[dict[str, Any]] = []
    for formation_id, manifest_path in sorted(registered_formations(root.trw_dir).items()):
        try:
            manifest = read_manifest(manifest_path)
        except (FormationError, OSError):
            # trw-fail-silent-allow: a removed run's stale index entry is not an open formation
            continue
        if all(str(m.status) in TERMINAL_STATUSES for m in manifest.members):
            continue
        formations.append(
            {
                "formation_id": formation_id,
                "members": [
                    {"member_id": m.member_id, "status": str(m.status), "open_join": m.open_join}
                    for m in manifest.members
                ],
            }
        )
        if len(formations) >= DISCOVER_FORMATION_LIMIT:
            break
    result: dict[str, Any] = {"status": "ok", "formations": formations}
    if _orchestrator_formation(ctx) is not None:
        now = time.time()
        result["candidates"] = [
            {
                "candidate_id": c.candidate_id,
                "client": c.client,
                "worktree": Path(c.worktree).name if c.worktree else None,
                "age_seconds": int(now - c.announced_at),
            }
            for c in live_candidates(root.trw_dir, now)
            if c.state == CandidateState.ACTIVE
        ]
    return result


_CANDIDATE_STATE = {
    CandidateState.ACTIVE.value: "candidate",
    CandidateState.ADMITTED.value: "admitted",
    CandidateState.JOINING.value: "admitted",
    CandidateState.WITHDRAWN.value: "opted_out",
}


def caller_state(ctx: Context | None) -> str:
    """FR18 state of a caller that does not bind as a member: its run and candidate record."""
    call_context = build_call_context(ctx)
    run_path = get_pinned_run(context=call_context)
    if run_path is None:
        return "no_run"
    try:
        mine = candidate_for(bootstrap_root().trw_dir, call_context.session_id, run_path)
    except FormationError:
        return "unannounced"  # trw-fail-silent-allow: an unreadable registry reports the conservative state
    return _CANDIDATE_STATE.get(mine.state, "unannounced") if mine is not None else "unannounced"


__all__ = [
    "BOOTSTRAP_ACTIONS",
    "DISCOVER_FORMATION_LIMIT",
    "bootstrap",
    "caller_state",
]
