"""Pin management helpers — extracted from _paths.py for module-size compliance.

Belongs to the ``_paths.py`` facade. Re-exported there for back-compat.

Three pin-management public helpers writing through to ``.trw/runtime/pins.json``
via the lower-level ``state._pin_store`` module:
- ``pin_active_run`` — pin a run directory as the active run for a session
- ``unpin_active_run`` — remove the run pin for a session
- ``get_pinned_run`` — return the currently pinned run directory, or None
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.state._pin_store import (
    _iso_now,
    _load_pin_store_uncached,
    _pin_store_file_lock,
    _pin_store_threading_lock,
    _write_pin_store_locked,
    get_pin_entry,
    load_pin_store,
    pin_record,
    remove_pin_entry,
    upsert_pin_entry,
)
from trw_mcp.state._process_identity import pid_is_alive, process_start_time

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext

logger = structlog.get_logger(__name__)

_sibling_adoption_done = False
#: PRD-CORE-274 FR14 (R1): the pin THIS process adopted, keyed by the pin it adopted it
#: under. In memory only -- the proof of client lineage is that this very process
#: performed the adoption; the ``adopted_from`` field persisted in the pin store is an
#: audit record and is never accepted as proof.
_ADOPTED_FROM: dict[str, str] = {}
_superseded_logged = False


def pin_active_run(
    run_dir: Path,
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> None:
    """Pin a run directory as the active run for a session.

    After pinning, ``find_active_run`` returns this directory. Writes
    through to ``.trw/runtime/pins.json`` via ``upsert_pin_entry`` so the
    pin survives MCP server restart (PRD-CORE-141 FR04).

    Args:
        run_dir: Absolute path to the run directory to pin.
        context: TRWCallContext resolved from the FastMCP Context (preferred,
            PRD-CORE-141 FR01).  When provided, its ``session_id`` wins.
        session_id: Legacy kwarg — retained for backward compat with direct
            Python callers.  Ignored when ``context`` is provided.
    """
    from trw_mcp.state._paths import _resolve_session_id

    sid = _resolve_session_id(context, session_id)
    record = upsert_pin_entry(sid, run_dir)
    logger.debug(
        "pin_saved",
        pin_key=sid,
        run_path=record["run_path"],
        pid=record["pid"],
    )


def unpin_active_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> None:
    """Remove the run pin for a session, reverting to filesystem scan.

    Persists the removal to ``.trw/runtime/pins.json`` (PRD-CORE-141 FR04).

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred).
        session_id: Legacy kwarg; ignored when ``context`` is provided.
    """
    from trw_mcp.client_profiles.session_identity import resolve_client_session_id
    from trw_mcp.state._paths import _resolve_session_id

    global _sibling_adoption_done
    sid = _resolve_session_id(context, session_id)
    removed = remove_pin_entry(sid)
    if sid == resolve_client_session_id():
        # Explicit user intent wins over reconnect recovery, even if this
        # process has not performed its first automatic adoption yet.
        _sibling_adoption_done = True
    if removed:
        logger.debug("pin_cleared", pin_key=sid)


def get_pinned_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Return the currently pinned run directory for a session, or None.

    Reads through the 1-second-TTL pin-store cache (PRD-CORE-141 FR04).

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred).
        session_id: Legacy kwarg; ignored when ``context`` is provided.
    """
    from trw_mcp.state._paths import _resolve_session_id

    return run_path_for_pin(_resolve_session_id(context, session_id), adopt_sibling=True)


def run_path_for_pin(pin_key: str, *, adopt_sibling: bool = False) -> Path | None:
    """The run *pin_key* is pinned to, or ``None``. PIN-FIRST AND PIN-ONLY (ledger RC-010).

    The one low-level resolver behind both enforcement surfaces: the MCP comms
    path (through :func:`get_pinned_run`, which resolves the key from a FastMCP
    context first) and ``scripts/check_formation_ownership.py`` (which has no
    context and resolves the key itself). They used to reach the pin store by
    two different call chains with nothing pinning them to the same answer.

    No mtime scan, ever: guessing a run would attribute a commit to whichever
    run was touched most recently, which is how a concurrent agent's identity
    gets borrowed.

    A lookup never refreshes a pin's TTL: re-stamping a resumed pin is the server's
    explicit :func:`claim_resumed_pin`, done at boot. *adopt_sibling* carries a pin
    across a client reconnect (PRD-INFRA-189 FR08) and is passed only by
    :func:`get_pinned_run`; a git hook's parent is git, so it would match nothing.
    """
    entry = get_pin_entry(pin_key) or (_adopt_client_sibling_pin(pin_key) if adopt_sibling else None)
    if entry is None:
        return None
    _note_if_superseded(pin_key, entry)
    run_path = entry.get("run_path")
    if isinstance(run_path, str) and run_path:
        return Path(run_path)
    return None


def claim_resumed_pin(pin_key: str) -> None:
    """Server boot only: claim the creator PID of a pin this server resumed from a dead one (ledger N11).

    A managed restart relaunches the server in a new process under the same pin
    key. Until something re-stamps the entry it names the dead predecessor, TTL
    expiry falls back to the heartbeat alone, and only a heartbeat checkpoint
    call or a session start refreshes that -- so a live, working server lost its pin once
    the TTL passed. A live creator (an older server kept beside a new one after
    ``/mcp``) is never re-stamped. Compare-and-swap on ``pid`` under the store
    lock; client lineage (``client_pid``/``client_start``) does not move, because
    reconnect adoption and the watch judge it.

    Explicit and never reached by a lookup, so an offline CLI or hook resolving a
    pin cannot stamp its short-lived PID and extend a dead server's TTL.
    """
    entry = get_pin_entry(pin_key)
    pid = None if entry is None else entry.get("pid")
    if entry is None or pid == os.getpid() or (isinstance(pid, int) and pid_is_alive(pid)):
        return
    with _pin_store_threading_lock, _pin_store_file_lock():
        store = _load_pin_store_uncached()
        current = store.get(pin_key)
        if not isinstance(current, dict) or current.get("pid") != pid:
            return
        store[pin_key] = {**current, "pid": os.getpid(), "last_heartbeat_ts": _iso_now()}
        _write_pin_store_locked(store)
    logger.info("pin_restamped_on_resume", pin_key=pin_key, previous_pid=pid)


def _adopt_client_sibling_pin(pin_key: str) -> dict[str, Any] | None:
    """Carry this client's run pin across a reconnect (PRD-INFRA-189 FR08).

    Claude Code hands an MCP server ``CLAUDE_CODE_SESSION_ID`` as it stood when the
    server was SPAWNED. An interactive ``/resume`` changes the live session id
    without respawning the server, so the server keeps pinning under the stale
    id; the next ``/mcp`` spawns a server with the live id, which finds no pin.
    ``resolve_pin_key`` is right on both sides -- the id really changed -- so the
    new server adopts the newest pin written by a server of the SAME client
    process: ``client_pid`` is our parent AND ``client_start`` is its birth time,
    so a dead client's pid recycled by an unrelated client matches nothing, and
    a pin written before ``client_start`` existed is never adopted. Only for a key
    that came from the client's own session variable: clients that publish none
    (Codex hosts several threads per process) key on per-connection ids and must
    not share a run.
    The old entry is left in place for the older server, which may still be live.
    """
    from trw_mcp.client_profiles.session_identity import resolve_client_session_id

    global _sibling_adoption_done
    parent = os.getppid()
    if _sibling_adoption_done or parent <= 1 or pin_key != resolve_client_session_id():
        return None
    # Cheap cached pre-check: this runs on every unpinned lookup, so take the
    # file lock only when a candidate exists.
    if not any(_from_client(entry, parent) for entry in load_pin_store().values()):
        return None
    with _pin_store_threading_lock, _pin_store_file_lock():
        store = _load_pin_store_uncached()
        if pin_key in store:
            return store[pin_key]
        siblings = [(key, entry) for key, entry in store.items() if _from_client(entry, parent)]
        if not siblings:
            return None
        previous_key, previous = max(siblings, key=lambda item: str(item[1].get("last_heartbeat_ts", "")))
        now = _iso_now()
        record = pin_record(str(previous["run_path"]), now, now)
        record["adopted_from"] = previous_key  # audit only; FR14 trusts _ADOPTED_FROM
        store[pin_key] = record
        _ADOPTED_FROM[pin_key] = previous_key
        _write_pin_store_locked(store)
        # Once per process: a reconnect is a boot-time event, and a pin this
        # server later drops (adoption by another session) must stay dropped.
        _sibling_adoption_done = True
    logger.info(
        "pin_adopted_from_client_sibling",
        pin_key=pin_key,
        previous_pin_key=previous_key,
        previous_pid=previous.get("pid"),
        run_path=record["run_path"],
    )
    return record


def adopted_from(pin_key: str) -> str | None:
    """The pin this process itself adopted under *pin_key* (FR14 client lineage), or None."""
    return _ADOPTED_FROM.get(pin_key)


def _from_client(entry: dict[str, Any], client_pid: int) -> bool:
    """True when *entry* was written by a server of this exact client process (pid + birth time)."""
    start = process_start_time(client_pid)
    return start is not None and entry.get("client_pid") == client_pid and entry.get("client_start") == start


def _note_if_superseded(pin_key: str, own: dict[str, Any]) -> None:
    """Log ``superseded_by_newer_server`` once when this client started a newer server (FR07).

    After ``/mcp`` the client may keep this older process alive beside the new
    one. The signal is a pin written later by ANOTHER server of the SAME client
    process -- not merely a newer writer on the same ``.trw/``, which every
    concurrent session produces.
    """
    global _superseded_logged
    parent = os.getppid()
    if _superseded_logged or parent <= 1 or not _from_client(own, parent):
        return
    own_created = str(own.get("created_ts", ""))
    for key, entry in load_pin_store().items():
        if (
            key != pin_key
            and _from_client(entry, parent)
            and entry.get("pid") != os.getpid()
            and str(entry.get("created_ts", "")) > own_created
        ):
            _superseded_logged = True
            logger.warning(
                "superseded_by_newer_server",
                pid=os.getpid(),
                newer_pid=entry.get("pid"),
                newer_pin_key=key,
                remedy=f"this server is no longer the client's current connection; kill {os.getpid()} once confirmed",
            )
            return
