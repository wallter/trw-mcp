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

import math
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.comms._identity import CallerBinding, IdentityError, resolve_authority_snapshot
from trw_mcp.comms._paging import decode_cursor, encode_cursor, fits
from trw_mcp.formation import (
    CANDIDATE_CAP,
    LIVE_CANDIDATE_STATES,
    TERMINAL_STATUSES,
    CandidateError,
    CandidateState,
    CoordinationRoot,
    FormationError,
    announce_candidate,
    bootstrap_root,
    candidate_for,
    live_candidates,
    own_root,
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
DISCOVER_CANDIDATE_LIMIT = CANDIDATE_CAP
BOOTSTRAP_ACTIONS = frozenset({"announce", "withdraw", "discover"})
_CANDIDATE_ID = re.compile(r"[0-9a-f]{32}\Z")


def _refused(reason: str, detail: str) -> dict[str, Any]:
    return {"status": "refused", "reason": reason, "detail": detail}


def bootstrap(action: str, ctx: Context | None, config: TRWConfig, *, cursor: str | None = None) -> dict[str, Any]:
    """Run one bootstrap action; refusals carry a closed reason and the next action."""
    call_context = build_call_context(ctx)
    run_path = get_pinned_run(context=call_context)
    if run_path is None:
        return _refused("no_pinned_run", "pin a run first: trw_init, or `trw-mcp run adopt` to resume one")
    root = bootstrap_root()
    try:
        if action == "announce":
            return _announce(root, call_context.session_id, run_path, config)
        if action == "withdraw":
            return _withdraw(root, call_context.session_id, run_path)
        return _discover(root, ctx, config, cursor=cursor)
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


def _orchestrator_binding(ctx: Context | None) -> CallerBinding | None:
    """The formation this caller orchestrates, bound at the bootstrap root or at its own root.

    Its own root covers an orchestrator whose run lives in a linked worktree with no
    FR17 record for itself (lane C review, D3); either way it must bind under FR01.
    """
    root = own_root()
    try:
        snapshot = resolve_authority_snapshot(ctx, trw_dir=root.trw_dir, project_root=root.project_root)
        snapshot.assert_eligible()
    except IdentityError:  # trw-fail-silent-allow: a nonmember or terminal lead sees no private candidates
        # the authority resolver forbids own-root fallback when a shared record exists.
        return None
    return snapshot.binding if snapshot.binding.is_orchestrator else None


def _lead_pending(binding: CallerBinding, now: float) -> dict[str, str | int | None]:
    """Read only this lead's pending mailbox facts, without fetch or ACK."""
    from trw_mcp.comms._envelope import MessageState
    from trw_mcp.comms._store import database_path

    try:
        database = database_path(binding.manifest_path)
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
        try:
            count, oldest = conn.execute(
                "SELECT COUNT(*),MIN(admitted_at) FROM admissions "
                "WHERE group_id=? AND recipient_member_id=? AND state=? AND expires_at>?",
                (binding.group_id, binding.member_id, MessageState.PENDING.value, now),
            ).fetchone()
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):  # trw-fail-silent-allow: unreadable mailbox is not_measured
        return {"measurement": "not_measured"}
    return {
        "measurement": "measured",
        "count": int(count),
        "earliest_age_seconds": int(max(0, now - float(oldest))) if oldest is not None else None,
    }


def _candidate_after(cursor: str | None, binding: CallerBinding) -> tuple[float, str] | None:
    if cursor is None:
        return None
    fields = decode_cursor(cursor, max_chars=512, arity=4)
    if (
        fields is None
        or fields[0] != binding.group_id
        or fields[1] != binding.member_id
        or type(fields[2]) not in (int, float)
        or not math.isfinite(fields[2])
        or not isinstance(fields[3], str)
        or _CANDIDATE_ID.fullmatch(fields[3]) is None
    ):
        raise ValueError("invalid_cursor")
    return float(fields[2]), str(fields[3])


def _discover(root: CoordinationRoot, ctx: Context | None, config: TRWConfig, *, cursor: str | None) -> dict[str, Any]:
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
    binding = _orchestrator_binding(ctx)
    if cursor is not None and binding is None:
        return _refused("invalid_cursor", "candidate pages require an eligible orchestrator")
    if binding is not None:
        try:
            after = _candidate_after(cursor, binding)
        except ValueError:
            return _refused("invalid_cursor", "use next_cursor from this formation's previous discover page")
        now = time.time()
        result["lead_pending"] = _lead_pending(binding, now)
        live = live_candidates(root.trw_dir, now)
        remaining = sorted(
            (c for c in live if after is None or (c.announced_at, c.candidate_id) > after),
            key=lambda c: (c.announced_at, c.candidate_id),
        )
        result.update(candidates=[], candidate_total=len(live), candidates_truncated=False, next_cursor=None)
        budget = max(0, config.comms_response_max_bytes - 1024)
        for c in remaining[:DISCOVER_CANDIDATE_LIMIT]:
            item = {
                "candidate_id": c.candidate_id,
                "client": c.client[:32],
                "worktree": Path(c.worktree).name[:64] if c.worktree else None,
                "age_seconds": int(max(0, now - c.announced_at)),
                "state": c.state,
            }
            next_cursor = encode_cursor(binding.group_id, binding.member_id, c.announced_at, c.candidate_id)
            proposed = {
                **result,
                "candidates": [*result["candidates"], item],
                "next_cursor": next_cursor,
                "candidates_truncated": True,
            }
            if not fits(proposed, budget):
                break
            result = proposed
        result["candidates_truncated"] = len(result["candidates"]) < len(remaining)
        if not result["candidates_truncated"]:
            result["next_cursor"] = None
        if remaining and not result["candidates"]:
            return _refused("response_too_large", "one candidate does not fit the configured response cap")
    # Reserve framing/guidance room added by the public trw_inbox adapter.
    budget = max(0, config.comms_response_max_bytes - 1024)
    if not fits(result, budget):
        return _refused("response_too_large", "formation listing exceeds the configured response cap")
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
