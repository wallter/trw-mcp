"""Read-only source readers for the v1 status snapshot (PRD-CORE-354 FR01/FR02).

Belongs to the :mod:`trw_mcp.services.status_snapshot` facade, which assembles the
blocks these functions return. Every reader here only reads; a reader that cannot
read its source raises and the facade turns that into ``state: "unknown"``.

Heavy imports are lazy: the deliver-gate helpers load only for the gate preview,
and the formation/comms packages (which pull in fastmcp) load only when the
pinned run actually belongs to a formation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: A checkpoint older than this renders as stale (FR03).
CHECKPOINT_STALE_AFTER_S = 30 * 60

_PINS_RELATIVE = ("runtime", "pins.json")
_FORMATION_MANIFEST = "formation.yaml"
_BUILD_EVENT = "build_check_complete"
_REVIEW_EVENT = "review_complete"
#: ``trw_deliver_complete`` is the MCP deliver's record; ``deliver`` is the
#: offline ``local deliver`` record (gate_evaluated: false). Both mean the run's
#: own deliver was called.
_DELIVER_EVENTS = frozenset({"trw_deliver_complete", "deliver"})
_REVIEW_VERDICTS = frozenset({"pass", "warn", "block"})


def unknown_build() -> dict[str, Any]:
    return {"state": "unknown", "scope": "unknown", "ts": None, "test_count": None, "build_scope": None}


def parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:  # trw-fail-silent-allow: an unparseable timestamp is reported as unknown by every caller
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def read_json_object(path: Path) -> dict[str, Any] | None:
    """Parsed JSON object at *path*; ``None`` when absent. Raises on unreadable/corrupt."""
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path} is not a JSON object")
    return raw


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every parseable object line in *path*. A torn or corrupt line is skipped
    (append-only logs tear routinely); an unreadable file raises ``OSError``."""
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:  # trw-fail-silent-allow: a torn line in an append-only log is ordinary
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def resolve_pinned_run(trw_dir: Path, session_id: str) -> Path | None:
    """The run pinned to *session_id* -- pin-only, never a recency guess.

    Reads ``<trw_dir>/runtime/pins.json`` directly (the caller names the project)
    and applies the pin store's own eviction rule, so a pin the server would evict
    is not shown as live. Raises on an unreadable or corrupt store.
    """
    store = read_json_object(trw_dir.joinpath(*_PINS_RELATIVE))
    if store is None or session_id not in store:
        return None
    from trw_mcp.state._pin_store import _apply_eviction_passes

    entry = _apply_eviction_passes({session_id: store[session_id]}).get(session_id)
    run_path = entry.get("run_path") if isinstance(entry, dict) else None
    return Path(run_path) if isinstance(run_path, str) and run_path else None


def read_run_yaml(run_path: Path) -> dict[str, Any]:
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    text = (run_path / "meta" / "run.yaml").read_text(encoding="utf-8")
    data = yaml.load(text, Loader=loader)  # noqa: S506 - a SafeLoader (C or pure) is passed explicitly
    if not isinstance(data, dict):
        raise TypeError("run.yaml is not a mapping")
    return data


def checkpoint_fields(run_path: Path, moment: datetime) -> dict[str, Any]:
    """``state``/``count``/``last_ts``/``age_s`` from the run's checkpoints.jsonl."""
    records = read_jsonl(run_path / "meta" / "checkpoints.jsonl")
    fields: dict[str, Any] = {"state": "none", "count": len(records), "last_ts": None, "age_s": None}
    if not records:
        return fields
    last_raw = records[-1].get("ts")
    fields["last_ts"] = last_raw if isinstance(last_raw, str) else None
    last = parse_ts(last_raw)
    if last is None:
        fields["state"] = "unknown"
        return fields
    age = max(0, int((moment - last).total_seconds()))
    fields.update(age_s=age, state="stale" if age > CHECKPOINT_STALE_AFTER_S else "ok")
    return fields


def _event_ts(ev: dict[str, Any]) -> str | None:
    ts = ev.get("ts")
    return ts if isinstance(ts, str) else None


def mcp_tool_call_after(events: list[dict[str, Any]], moment: datetime) -> bool:
    """True when the run log holds a ``trw_*`` MCP ``tool_call`` stamped after *moment*.

    The degraded-mode latch is a hook-side inference written once and cleared only
    at session start; a later server-written ``tool_call`` row is direct evidence
    that the MCP transport was attached after the latch, so the latch is stale.
    """
    for ev in reversed(events):
        if ev.get("event") != "tool_call" or not str(ev.get("tool_name") or "").startswith("trw_"):
            continue
        ts = parse_ts(ev.get("ts"))
        if ts is not None and ts > moment:
            return True
    return False


#: Upper bound on the session-events tail read for the degraded-latch check.
SESSION_EVENTS_TAIL_BYTES = 256 * 1024


def read_jsonl_tail(path: Path, max_bytes: int = SESSION_EVENTS_TAIL_BYTES) -> list[dict[str, Any]]:
    """Parseable object lines from the last *max_bytes* of *path* (a partial first line is dropped).

    Absent file is ``[]``; an unreadable one raises ``OSError``. A symlink is not followed.
    """
    if path.is_symlink() or not path.is_file():
        return []
    with path.open("rb") as fh:
        size = fh.seek(0, 2)
        start = max(0, size - max_bytes)
        fh.seek(start)
        raw = fh.read()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # the first line is cut mid-record
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:  # trw-fail-silent-allow: a torn line in an append-only log is ordinary
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def build_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The run's latest build check, judged by the deliver gate's own predicate."""
    from trw_mcp.tools._delivery_build_gates import _build_event_payload, _build_passed

    for ev in reversed(events):
        if str(ev.get("event", "")) != _BUILD_EVENT:
            continue
        data = _build_event_payload(ev)
        try:
            test_count: int | None = int(str(data.get("test_count")))
        except (TypeError, ValueError):
            test_count = None
        scope_raw = data.get("scope")
        return {
            "state": "passed" if _build_passed(ev) else "failed",
            "scope": "run",
            "ts": _event_ts(ev),
            "test_count": test_count,
            "build_scope": scope_raw if isinstance(scope_raw, str) and scope_raw else None,
        }
    return None


def review_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for ev in reversed(events):
        if str(ev.get("event", "")) == _REVIEW_EVENT:
            verdict = str(ev.get("verdict", "")).strip().lower()
            state = verdict if verdict in _REVIEW_VERDICTS else "unknown"
            return {"state": state, "scope": "run", "ts": _event_ts(ev)}
    return None


def deliver_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """``called`` only from deliver evidence: a completion event or a successful ``trw_deliver`` tool_call.

    A run status (``complete``/``delivered``) is NOT deliver evidence (FR02, HB-1).
    """
    for ev in reversed(events):
        name = str(ev.get("event") or ev.get("event_type") or "")
        if name in _DELIVER_EVENTS or (
            name == "tool_call" and ev.get("tool_name") == "trw_deliver" and ev.get("success") is True
        ):
            return {"state": "called", "scope": "run", "ts": _event_ts(ev)}
    return None


def session_build(ceremony: dict[str, Any] | None, session_id: str | None) -> dict[str, Any] | None:
    """This session's OWN build result (``session_build_results[sid]``), scope ``session``."""
    if not session_id or not isinstance(ceremony, dict):
        return None
    results = ceremony.get("session_build_results")
    result = results.get(session_id) if isinstance(results, dict) else None
    if result not in {"passed", "failed"}:
        return None
    stamps = ceremony.get("session_build_results_at")
    stamp = stamps.get(session_id) if isinstance(stamps, dict) else None
    return {
        "state": result,
        "scope": "session",
        "ts": stamp if isinstance(stamp, str) else None,
        "test_count": None,
        "build_scope": None,
    }


def project_aggregate(ceremony: dict[str, Any] | None) -> dict[str, Any]:
    """Project-wide ceremony aggregates: reported, labelled, never a tick (FR02)."""
    source = ceremony or {}
    build = source.get("build_check_result")
    verdict = source.get("review_verdict")
    deliver = source.get("deliver_called")
    return {
        "build_check_result": build if isinstance(build, str) else None,
        "review_verdict": verdict if isinstance(verdict, str) else None,
        "deliver_called": deliver if isinstance(deliver, bool) else None,
        "scope": "project_aggregate",
    }


def gate_preview_fields(events: list[dict[str, Any]], trw_dir: Path, run_path: Path) -> dict[str, Any]:
    """``state``/``summary`` from the deliver gate's own read-only readiness scan."""
    from trw_mcp.tools._orchestration_gate_scan import compute_deliver_gate_status

    summary = str(compute_deliver_gate_status(list(events), trw_dir, run_path).get("deliver_gate_summary", ""))
    if summary.startswith("READY"):
        return {"state": "ready", "summary": summary}
    if summary.startswith("BLOCKED"):
        return {"state": "blocked", "summary": summary}
    return {"state": "unknown", "summary": summary}


def inbox_fields(trw_dir: Path, run_path: Path, session_id: str, run_data: dict[str, Any]) -> dict[str, Any]:
    """Pending peer-message COUNT for this session's formation member -- never a body.

    Member resolution reuses comms' own trust checks (stamp, registry, canonical
    owner, run + pin match). Unlike ``pending_hint`` no parent-process binding is
    required: a statusLine is not the client's child, and a count is body-free.
    """
    stamped = run_data.get("formation_id")
    if not (run_path / _FORMATION_MANIFEST).is_file() and not (isinstance(stamped, str) and stamped):
        return {"state": "none", "pending": None, "formation_id": None}

    import time

    from trw_mcp.comms._bootstrap import _lead_pending
    from trw_mcp.comms._identity import (
        ELIGIBLE_STATUSES,
        CallerBinding,
        _assert_trusted_formation,
        _matching_members,
        derive_group_id,
    )
    from trw_mcp.formation import load as load_formation

    context = load_formation(run_path, trw_dir=trw_dir)
    if context is None:
        return {"state": "none", "pending": None, "formation_id": None}
    _assert_trusted_formation(run_path, context, trw_dir=trw_dir)
    formation_id = context.manifest.formation_id
    matched = _matching_members(context.manifest, run_path, session_id)
    if not matched:
        return {"state": "none", "pending": None, "formation_id": formation_id}
    if len(matched) > 1:
        raise ValueError("ambiguous formation member")
    member = matched[0]
    if str(member.status) not in ELIGIBLE_STATUSES:
        return {"state": "ok", "pending": 0, "formation_id": formation_id}
    binding = CallerBinding(
        group_id=derive_group_id(trw_dir.parent, context.manifest_path),
        formation_id=formation_id,
        member_id=member.member_id,
        session_id=session_id,
        run_path=run_path,
        manifest_path=context.manifest_path,
        is_orchestrator=context.is_orchestrator,
    )
    # The comms package's own mode=ro pending-count read (one COUNT, no fetch, no ACK).
    pending = _lead_pending(binding, time.time())
    count = pending.get("count")
    if pending.get("measurement") != "measured" or not isinstance(count, int):
        raise ValueError("mailbox not measured")
    return {"state": "ok", "pending": count, "formation_id": formation_id}
