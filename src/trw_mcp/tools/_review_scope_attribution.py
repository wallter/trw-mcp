"""Review-scope attribution (PRD-QUAL-123). Belongs to the ``_delivery_event_checks`` facade; re-exported there."""

from __future__ import annotations


def _drop_provably_foreign(
    events: list[dict[str, object]],
    session_id: str | None,
) -> list[dict[str, object]]:
    """PRD-QUAL-123 FR02: drop ``file_modified`` events provably written by ANOTHER host session.

    The run's own record is the ownership evidence: the ``host_session_id`` values stamped on events carrying
    the delivering ``session_id``. A ``file_modified`` event with no ``session_id`` whose non-empty
    ``host_session_id`` is outside that set is foreign (the hook wrote it into this run's log from another
    agent). Conservative by construction: an unscoped caller, a caller with no own host evidence, an event
    with no host id, and any path the delivering session also touched ("owned beats foreign") all stay counted.
    """
    if session_id is None:
        return events
    own_hosts = {
        str(ev["host_session_id"])
        for ev in events
        if str(ev.get("session_id", "")) == session_id and ev.get("host_session_id")
    }
    if not own_hosts:
        return events
    own_paths = {
        str(ev.get("file", ""))
        for ev in events
        if str(ev.get("event", "")) == "file_modified" and str(ev.get("session_id", "")) == session_id
    }
    return [
        ev
        for ev in events
        if not (
            str(ev.get("event", "")) == "file_modified"
            and not ev.get("session_id")
            and ev.get("host_session_id")
            and str(ev["host_session_id"]) not in own_hosts
            and str(ev.get("file", "")) not in own_paths
        )
    ]
