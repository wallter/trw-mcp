"""``trw_init``'s formation keys — create one, or join one (FR03, FR04).

Belongs to the ``orchestration.py`` facade; its own sibling so the facade stays
well under the module-size gate and so the CLI verb and the tool call reach the
SAME function. Neither entry point may drift from the other, which is what
FR03's "byte-identical manifests" acceptance asserts.

Both keys on one call is REFUSED rather than ordered. Creating and joining in
one ``trw_init`` would mean a run that is simultaneously the orchestrator of one
formation and a member of another; the operator who wrote that meant one of the
two, and picking for them would silently produce the other.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError

logger = structlog.get_logger(__name__)

__all__ = ["apply_formation_init", "record_member_delivery"]

#: The only keys a ``join_formation`` payload may carry. Refused, not ignored —
#: a typo'd key here would otherwise join the caller to nothing while ``trw_init``
#: reported success.
_JOIN_KEYS = ("formation_id", "member_id")


def apply_formation_init(
    formation: dict[str, object] | None,
    join_formation: dict[str, str] | None,
    run_root: Path,
    ctx: object | None,
    result: dict[str, str],
) -> None:
    """Create or join a formation, recording the outcome on *result*."""
    from trw_mcp.formation import FormationError, create, join

    if formation is not None and join_formation is not None:
        raise StateError(
            "advanced accepts formation OR join_formation, not both: a run cannot be the "
            "orchestrator of one formation and a member of another in the same trw_init call."
        )
    try:
        if formation is not None:
            manifest = create(run_root, formation)
            result["formation_id"] = manifest.formation_id
            result["formation_manifest"] = str(run_root / "formation.yaml")
            result["formation_members"] = ",".join(m.member_id for m in manifest.members)
            result["formation_revision"] = str(manifest.revision)
            return
        payload = dict(join_formation or {})
        unknown = sorted(set(payload) - set(_JOIN_KEYS))
        if unknown:
            raise StateError(f"join_formation has unknown key(s) {unknown}. Accepted keys: {', '.join(_JOIN_KEYS)}")
        missing = [key for key in _JOIN_KEYS if not str(payload.get(key, "")).strip()]
        if missing:
            raise StateError(f"join_formation requires {', '.join(_JOIN_KEYS)}; missing {', '.join(missing)}")
        manifest = join(
            str(payload["formation_id"]),
            str(payload["member_id"]),
            run_root,
            pin_key=_resolve_pin_key(ctx),
        )
        result["formation_id"] = manifest.formation_id
        result["formation_member_id"] = str(payload["member_id"])
        result["formation_revision"] = str(manifest.revision)
    except FormationError as exc:
        # Re-raised as StateError so the tool boundary reports it the way it
        # reports every other refused ``advanced`` payload — one failure shape
        # for the caller, with the facade's own sentence preserved verbatim.
        raise StateError(str(exc)) from exc


def _resolve_pin_key(ctx: object | None) -> str | None:
    """The pin this member's session is keyed on, or ``None`` when unresolvable.

    Recorded at join so FR08 can ask the pin store whether the member is still
    alive without a heartbeat scheduler. ``None`` is honest: a member that joined
    without a resolvable pin is reported stale ("pin absent") rather than assumed
    healthy, because absence of liveness evidence is not evidence of liveness.
    """
    try:
        from trw_mcp.state._paths import resolve_pin_key

        return resolve_pin_key(ctx) or None
    except Exception:  # justified: liveness is advisory; an unresolved pin is reported, not fatal
        logger.debug("formation_pin_key_unresolved", exc_info=True)
        return None


def record_member_delivery(run_path: Path, results: dict[str, object]) -> None:
    """A member's own successful ``trw_deliver`` self-reports completion (FR11).

    Two writes, in this order and for two different readers: a
    ``trw_deliver_complete`` event on the member's OWN run — the durable
    delivery RECORD the orchestrator's gate re-checks — and then the
    ``delivered`` status on its manifest entry, through the facade, which only
    accepts the report from the run recorded for that member at join.

    The record is written FIRST on purpose. A stamp without a record is exactly
    what FR11 treats as non-terminal, so a crash between the two leaves the
    orchestrator waiting rather than believing an unevidenced completion.

    Fail-open: a member is not blocked from delivering because its formation
    could not be updated. The failure is surfaced on the result so the operator
    can retire the member by revision instead of discovering silence.
    """
    from trw_mcp.formation import FormationError, load, mark_member_delivered

    try:
        context = load(run_path)
        if context is None or context.member_id is None:
            return
        from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

        FileEventLogger(FileStateWriter()).log_event(
            run_path / "meta" / "events.jsonl", "trw_deliver_complete", {"member_id": context.member_id}
        )
        manifest = mark_member_delivered(run_path)
        if manifest is not None:
            results["formation_member_delivered"] = context.member_id
    except FormationError as exc:
        results["formation_member_delivery_error"] = str(exc)
        logger.warning("formation_member_delivery_unrecorded", run=str(run_path), error=str(exc))
    except Exception:  # justified: a member must never be blocked from delivering
        logger.warning("formation_member_delivery_degraded", run=str(run_path), exc_info=True)
