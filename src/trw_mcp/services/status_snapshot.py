"""Read-only status contract v1 for human-facing UIs (PRD-CORE-354 FR01, FR02, FR04).

:func:`build_status_snapshot` answers "what is this session's TRW state right
now" for a statusLine, a Claude Code mod pane or any other client adapter. The
JSON shape is frozen at ``schema_version`` 1; consumers (the statusLine script and
the ``trw-ui`` mod) parse it, so a field is never renamed or retyped here without a
version bump.

Truthfulness rules (FR02, HB-1):

- The run comes from the pin store entry for ``session_id`` only. There is no
  recency fallback: a session with no pin has ``run.state == "none"``.
- A positive build/review/deliver state needs run-scoped events
  (``<run>/meta/events.jsonl``) or, for build only, this session's own entry in
  ``session_build_results``. Project-wide aggregates from ``ceremony-state.json``
  are reported under ``project_aggregate`` and never drive an ``evidence`` state;
  when they are the only evidence the ``evidence`` state is ``unknown``.
- The gate preview reuses the deliver gate's own predicates and is labelled
  ``preview: true``; it is never a verdict.
- Every block carries ``state``; a source that could not be read sets that state
  to ``unknown``. The top-level ``unknown`` list names every dotted field whose
  state is ``unknown`` (invariant: the list and the states agree).

Read-only by construction: the builder opens files for reading and the formation
mailbox with ``?mode=ro``. It never imports the memory store and never returns a
message or learning body. The opt-in cache (:func:`read_cached_snapshot` /
:func:`write_cached_snapshot`) is the feature's ONLY write and lives under
``.trw/runtime/status/``.

Import cost matters (NFR01: the statusLine runs this on every refresh): heavy
modules -- the gate preview's helpers and the formation/comms packages, which
pull in fastmcp -- are imported lazily, the latter only when the pinned run
actually belongs to a formation.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.services import _status_sources as _src
from trw_mcp.services._status_sources import CHECKPOINT_STALE_AFTER_S

logger = structlog.get_logger(__name__)

SCHEMA_VERSION = 1

#: Same admissibility rule the hooks apply before a session id becomes a path
#: component (lib-trw.sh ``trw_degraded_marker_path``): letters, digits, dot,
#: underscore, hyphen; at most 200 chars; no ``..``; not ``.``; no leading ``-``.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")

_STATUS_CACHE_SUBDIR = ("runtime", "status")
_DEGRADED_LATCH_SUBDIR = ("runtime", "degraded-mode")
_SESSION_EVENTS_RELATIVE = ("context", "session-events.jsonl")
_CEREMONY_RELATIVE = ("context", "ceremony-state.json")


def valid_session_id(session_id: str | None) -> bool:
    """True when *session_id* is safe to use as a single path component."""
    if not session_id or session_id in {".", ".."} or session_id.startswith("-") or ".." in session_id:
        return False
    return bool(_SESSION_ID_RE.match(session_id))


def _unreadable(source: str) -> None:
    logger.debug("status_source_unreadable", source=source, exc_info=True)


def _run_block(
    trw_dir: Path, sid: str | None, run_path: Path | None, as_of: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(run block, parsed run.yaml)``; the run comes from the pin or an explicit path only."""
    run: dict[str, Any] = {
        "state": "none",
        "run_id": "",
        "task": "",
        "phase": "",
        "status": "",
        "run_path": "",
        "as_of": as_of,
    }
    try:
        resolved = run_path if run_path is not None else (_src.resolve_pinned_run(trw_dir, sid) if sid else None)
    except Exception:  # justified: scan-resilience, an unreadable pin store is reported as unknown
        _unreadable("pins")
        run["state"] = "unknown"
        return run, {}
    if resolved is None:
        return run, {}
    run["run_path"] = str(resolved)
    try:
        data = _src.read_run_yaml(resolved)
    except Exception:  # justified: scan-resilience, a missing/corrupt run.yaml is reported as unknown
        _unreadable("run.yaml")
        run["state"] = "unknown"
        return run, {}
    run.update(
        state="ok",
        run_id=str(data.get("run_id", resolved.name)),
        task=str(data.get("task", resolved.parent.name)),
        phase=str(data.get("phase", "")),
        status=str(data.get("status", "")),
    )
    return run, data


def _evidence(
    events: list[dict[str, Any]] | None,
    run: dict[str, Any],
    ceremony: dict[str, Any] | None,
    aggregate: dict[str, Any],
    sid: str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """``(build, review, deliver)`` under the FR02 rule: positive only from run or session scope."""
    if events is None:
        return (
            _src.unknown_build(),
            {"state": "unknown", "scope": "run", "ts": None},
            {
                "state": "unknown",
                "scope": "run",
                "ts": None,
            },
        )
    build = (
        _src.build_from_events(events)
        or _src.session_build(ceremony, sid)
        or {
            "state": "none",
            "scope": "run" if run["state"] == "ok" else "unknown",
            "ts": None,
            "test_count": None,
            "build_scope": None,
        }
    )
    review = _src.review_from_events(events) or {"state": "none", "scope": "run", "ts": None}
    deliver = _src.deliver_from_events(events) or {
        "state": "none",
        "scope": "run",
        "ts": None,
    }
    # trw:intentional FR02/HB-1 -- aggregate-only evidence is "unknown", never a tick and never "none",
    # UNLESS the run's own log is server-written (it holds trw_* tool_call rows): the server logs
    # build_check/review/deliver into that same log, so their absence there is proof of "none".
    if any(ev.get("event") == "tool_call" and str(ev.get("tool_name") or "").startswith("trw_") for ev in events):
        return build, review, deliver
    if build["state"] == "none" and aggregate["build_check_result"] is not None:
        build = _src.unknown_build()
    if review["state"] == "none" and aggregate["review_verdict"] is not None:
        review["state"] = "unknown"
    if deliver["state"] == "none" and aggregate["deliver_called"] is True:
        deliver["state"] = "unknown"
    return build, review, deliver


def build_status_snapshot(
    trw_dir: Path,
    session_id: str | None,
    *,
    now: datetime | None = None,
    run_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the v1 status snapshot for *session_id* in the project at *trw_dir*.

    Reads only. *run_path* is an explicit identity override (``--run-path``);
    without it the run comes from the pin for *session_id* alone. *environ*
    defaults to ``os.environ`` and only labels ``client``.
    """
    moment = now or datetime.now(timezone.utc)
    as_of = moment.astimezone(timezone.utc).isoformat()
    sid = session_id if valid_session_id(session_id) else None
    env = environ if environ is not None else os.environ
    run, run_data = _run_block(trw_dir, sid, run_path, as_of)
    run_dir = Path(run["run_path"]) if run["state"] == "ok" else None

    checkpoint: dict[str, Any] = {
        "state": "none",
        "count": 0,
        "last_ts": None,
        "age_s": None,
        "scope": "run",
        "as_of": as_of,
    }
    events: list[dict[str, Any]] | None = [] if run["state"] != "unknown" else None
    if run["state"] == "unknown":
        checkpoint["state"] = "unknown"
    elif run_dir is not None:
        try:
            checkpoint.update(_src.checkpoint_fields(run_dir, moment))
        except Exception:  # justified: scan-resilience, unreadable checkpoints are reported as unknown
            _unreadable("checkpoints")
            checkpoint["state"] = "unknown"
        try:
            events = _src.read_jsonl(run_dir / "meta" / "events.jsonl")
        except Exception:  # justified: scan-resilience, an unreadable event log makes evidence unknown
            _unreadable("events")
            events = None

    ceremony: dict[str, Any] | None = None
    ceremony_ok = True
    try:
        ceremony = _src.read_json_object(trw_dir.joinpath(*_CEREMONY_RELATIVE))
    except Exception:  # justified: scan-resilience, corrupt ceremony state makes the aggregate unknown
        _unreadable("ceremony-state")
        ceremony_ok = False
    aggregate = _src.project_aggregate(ceremony)
    build, review, deliver = _evidence(events, run, ceremony, aggregate, sid)

    gate: dict[str, Any] = {
        "state": "unknown",
        "summary": "no run" if run_dir is None else "",
        "preview": True,
        "as_of": as_of,
    }
    if run_dir is not None and events is not None:
        try:
            gate.update(_src.gate_preview_fields(events, trw_dir, run_dir))
        except Exception:  # justified: fail-open, the preview must never break the snapshot
            _unreadable("gate_preview")

    inbox: dict[str, Any] = {"state": "none", "pending": None, "formation_id": None, "as_of": as_of}
    if run["state"] == "unknown":
        inbox["state"] = "unknown"
    elif run_dir is not None and sid:
        try:
            inbox.update(_src.inbox_fields(trw_dir, run_dir, sid, run_data))
        except Exception:  # justified: fail-open, any formation/comms failure makes the inbox unknown
            _unreadable("inbox")
            inbox.update(state="unknown", pending=None)

    degraded = "unknown"
    if sid:
        try:
            latch = trw_dir.joinpath(*_DEGRADED_LATCH_SUBDIR, sid)
            # Same rule as the hooks (lib-trw.sh): a symlinked latch is not a latch.
            degraded = "yes" if latch.is_file() and not latch.is_symlink() else "no"
            if degraded == "yes":
                latched_at = datetime.fromtimestamp(latch.stat().st_mtime, tz=timezone.utc)
                # Pinned run's log first; with no pin the server writes its tool_call rows to
                # context/session-events.jsonl. Those rows carry no session id (only agent_id and
                # trace ids), so any later trw_* tool_call there proves the MCP server is up for this
                # project, which is exactly what the latch denies. Bounded tail read only.
                if (events and _src.mcp_tool_call_after(events, latched_at)) or (
                    run_dir is None
                    and _src.mcp_tool_call_after(
                        _src.read_jsonl_tail(trw_dir.joinpath(*_SESSION_EVENTS_RELATIVE)), latched_at
                    )
                ):
                    degraded = "no"  # a later MCP tool call proves the latch stale
        except OSError:
            _unreadable("degraded-mode")

    unknown = [] if ceremony_ok else ["project_aggregate"]
    for dotted, state in (
        ("run", run["state"]),
        ("checkpoint", checkpoint["state"]),
        ("evidence.build", build["state"]),
        ("evidence.review", review["state"]),
        ("evidence.deliver", deliver["state"]),
        # No run means no gate to preview: not an unreadable source, so not listed.
        ("gate_preview", gate["state"] if run["state"] != "none" else "n/a"),
        ("inbox", inbox["state"]),
        ("degraded", degraded),
    ):
        if state == "unknown":
            unknown.append(dotted)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": as_of,
        "session_id": sid,
        "client": "claude-code" if env.get("CLAUDE_CODE_SESSION_ID") else None,
        "run": run,
        "checkpoint": checkpoint,
        "evidence": {"build": build, "review": review, "deliver": deliver, "as_of": as_of},
        "gate_preview": gate,
        "project_aggregate": aggregate,
        "inbox": inbox,
        "degraded": {"state": degraded},
        "unknown": unknown,
    }


# --- opt-in cache (FR04) ---------------------------------------------------


def cache_path(trw_dir: Path, session_id: str) -> Path:
    """``.trw/runtime/status/<session_id>.json``; *session_id* must be valid."""
    if not valid_session_id(session_id):
        raise ValueError("session id is not a safe path component")
    return trw_dir.joinpath(*_STATUS_CACHE_SUBDIR, f"{session_id}.json")


def read_cached_snapshot(
    trw_dir: Path, session_id: str, ttl_s: float, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """A cached snapshot younger than *ttl_s*, or ``None`` (absent, stale, corrupt, foreign)."""
    if ttl_s <= 0 or not valid_session_id(session_id):
        return None
    try:
        data = _src.read_json_object(cache_path(trw_dir, session_id))
    except Exception:  # trw-fail-silent-allow: FR04, a corrupt cache is ignored (logged) and rebuilt fresh
        logger.debug("status_cache_unreadable", exc_info=True)
        return None
    if data is None or data.get("schema_version") != SCHEMA_VERSION or data.get("session_id") != session_id:
        return None
    generated = _src.parse_ts(data.get("generated_at"))
    if generated is None:
        return None
    age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
    if age < 0 or age > ttl_s:
        return None
    return data


def write_cached_snapshot(trw_dir: Path, snapshot: dict[str, Any]) -> Path | None:
    """Atomically replace the snapshot's cache file at 0600. Returns the path, or ``None``."""
    sid = snapshot.get("session_id")
    if not isinstance(sid, str) or not valid_session_id(sid):
        return None
    from trw_memory.safe_fs import write_beneath

    data = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")
    # write_beneath: atomic temp+rename, no-follow on every component (the checkout write primitive).
    write_beneath(trw_dir, "/".join((*_STATUS_CACHE_SUBDIR, f"{sid}.json")), data, mode=0o600)
    return cache_path(trw_dir, sid)


def line_cache_path(trw_dir: Path, session_id: str) -> Path:
    """``.trw/runtime/status/<session_id>.line``; *session_id* must be valid."""
    if not valid_session_id(session_id):
        raise ValueError("session id is not a safe path component")
    return trw_dir.joinpath(*_STATUS_CACHE_SUBDIR, f"{session_id}.line")


def write_cached_line(trw_dir: Path, session_id: str | None, line: str) -> Path | None:
    """Atomically write the rendered *line* beside the JSON cache (same 0600 writer). Path or ``None``."""
    if not isinstance(session_id, str) or not valid_session_id(session_id):
        return None
    from trw_memory.safe_fs import write_beneath

    write_beneath(
        trw_dir, "/".join((*_STATUS_CACHE_SUBDIR, f"{session_id}.line")), (line + "\n").encode("utf-8"), mode=0o600
    )
    return line_cache_path(trw_dir, session_id)


def load_or_build_snapshot(
    trw_dir: Path,
    session_id: str | None,
    *,
    cache_ttl_s: float | None = None,
    allow_cache_write: bool = True,
    now: datetime | None = None,
    run_path: Path | None = None,
) -> dict[str, Any]:
    """Cache-aware entry for the CLI: a fresh cached snapshot, else a new one (cached when enabled)."""
    use_cache = bool(cache_ttl_s and cache_ttl_s > 0 and run_path is None and valid_session_id(session_id))
    if use_cache and session_id is not None and cache_ttl_s is not None:
        cached = read_cached_snapshot(trw_dir, session_id, cache_ttl_s, now=now)
        if cached is not None:
            return cached
    snapshot = build_status_snapshot(trw_dir, session_id, now=now, run_path=run_path)
    if use_cache and allow_cache_write:
        try:
            write_cached_snapshot(trw_dir, snapshot)
        except Exception:  # justified: fail-open, a failed cache write never fails the status call
            logger.debug("status_cache_write_failed", exc_info=True)
    return snapshot


__all__ = [
    "CHECKPOINT_STALE_AFTER_S",
    "SCHEMA_VERSION",
    "build_status_snapshot",
    "cache_path",
    "line_cache_path",
    "load_or_build_snapshot",
    "read_cached_snapshot",
    "valid_session_id",
    "write_cached_line",
    "write_cached_snapshot",
]
