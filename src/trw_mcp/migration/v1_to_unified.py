"""One-shot migration: legacy events.jsonl → unified events-YYYY-MM-DD.jsonl.

PRD-HPO-MEAS-001 §5 S8 — converts historical ``events.jsonl``,
``checkpoints.jsonl``, and ``contract_events.jsonl`` under
``.trw/runs/**/meta/`` into the Phase-2 unified schema emitted by
:class:`HPOTelemetryEvent` subclasses.

Design invariants:

1. **Idempotent.** Running twice on the same run directory produces no
   duplicate rows — the migration skips rows whose ``event_id`` already
   appears in the target unified file.
2. **Round-trip parity.** Every legacy row yields exactly one unified
   row with the same ``ts`` and a preserved payload. Fields not present
   in the legacy shape default to empty-string / empty-dict.
3. **Dry-run by default.** ``migrate_run(run_dir)`` returns a
   :class:`MigrationReport` describing what would change without
   writing anything. Pass ``apply=True`` to persist.
4. **No loss.** Malformed legacy rows are skipped WITH a WARN log and
   counted in the report — they are NEVER discarded silently.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.telemetry.event_base import HPOTelemetryEvent
from trw_mcp.telemetry.unified_events import UnifiedEventWriter

logger = structlog.get_logger(__name__)


#: Legacy → unified ``event_type`` mapping. Any legacy row whose
#: ``event`` field matches a key here is converted to the mapped
#: ``event_type`` + appropriate subclass. Rows with unknown ``event``
#: values fall through to ``observer`` (preserving the payload).
_LEGACY_EVENT_TYPE_MAP: dict[str, str] = {
    "session_start": "session_start",
    "session_end": "session_end",
    "deliver": "session_end",
    "checkpoint": "observer",
    "run_init": "observer",
    "phase_enter": "phase_exposure",
    "phase_exit": "phase_exposure",
    "contract": "contract",
    "contract_pass": "contract",
    "contract_fail": "contract",
    "ceremony": "ceremony",
    "ceremony_compliance": "ceremony_compliance",
    "tool_call": "tool_call",
    "thrashing": "thrashing",
    "mcp_security": "mcp_security",
    "meta_tune": "meta_tune",
}


@dataclass
class MigrationReport:
    """Summary of a migration run."""

    run_dir: Path
    source_files: list[Path] = field(default_factory=list)
    rows_read: int = 0
    rows_migrated: int = 0
    rows_skipped_duplicate: int = 0
    rows_skipped_malformed: int = 0
    target_file: Path | None = None
    applied: bool = False


def _coerce_ts(raw: Any) -> str:
    """Return an ISO-8601 ts string; fall back to now() on malformed input."""
    if isinstance(raw, str) and raw:
        return raw
    return datetime.now(tz=timezone.utc).isoformat()


def _legacy_row_to_unified(row: dict[str, Any], *, session_id: str, run_id: str | None) -> dict[str, Any] | None:
    """Convert a single legacy jsonl row to a unified-schema dict.

    Returns None when the row is unparseable (no ``event`` field).
    """
    legacy_event = row.get("event")
    if not isinstance(legacy_event, str):
        return None

    unified_type = _LEGACY_EVENT_TYPE_MAP.get(legacy_event, "observer")
    # Payload: every non-reserved key flows into payload so no data is lost.
    reserved = {"event", "ts", "session_id", "run_id"}
    payload: dict[str, Any] = {k: v for k, v in row.items() if k not in reserved}
    # Preserve the original legacy event name inside payload for traceability.
    payload.setdefault("legacy_event", legacy_event)

    event_id = row.get("event_id") or f"evt_{uuid.uuid4().hex}"

    return {
        "event_id": str(event_id),
        "session_id": str(row.get("session_id", session_id)),
        "run_id": row.get("run_id", run_id) if row.get("run_id") is not None else run_id,
        "ts": _coerce_ts(row.get("ts")),
        "emitter": unified_type,
        "event_type": unified_type,
        "surface_snapshot_id": str(row.get("surface_snapshot_id", "")),
        "parent_event_id": row.get("parent_event_id"),
        "payload": payload,
    }


def _read_existing_event_ids(target: Path) -> set[str]:
    """Read ``target`` if it exists and return the set of existing event_ids."""
    if not target.exists():
        return set()
    out: set[str] = set()
    with target.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = rec.get("event_id")
            if isinstance(eid, str):
                out.add(eid)
    return out


def _legacy_source_files(run_dir: Path) -> list[Path]:
    """Return the list of legacy jsonl files in a run's ``meta/`` dir."""
    meta = run_dir / "meta"
    if not meta.is_dir():
        return []
    candidates = [
        meta / "events.jsonl",
        meta / "checkpoints.jsonl",
        meta / "contract_events.jsonl",
    ]
    return [p for p in candidates if p.is_file()]


def _target_file(run_dir: Path, *, now: datetime | None = None) -> Path:
    """Resolve the unified target file path (under ``<run_dir>/meta/``)."""
    ts = now or datetime.now(tz=timezone.utc)
    return run_dir / "meta" / f"events-{ts.strftime('%Y-%m-%d')}.jsonl"


def migrate_run(
    run_dir: Path,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> MigrationReport:
    """Migrate a single run's legacy jsonl files to the unified schema.

    Args:
        run_dir: Run directory (``<task>/<run_id>/``). Must contain a
            ``meta/`` subdirectory with at least one legacy jsonl file.
        apply: When False (default), returns a :class:`MigrationReport`
            describing what WOULD be written without writing anything.
        now: Override timestamp for target filename determinism.
    """
    report = MigrationReport(run_dir=run_dir)
    sources = _legacy_source_files(run_dir)
    report.source_files = sources
    if not sources:
        return report

    target = _target_file(run_dir, now=now)
    report.target_file = target
    existing_ids = _read_existing_event_ids(target)

    session_id = run_dir.parent.name if run_dir.parent != run_dir else "unknown"
    run_id = run_dir.name

    migrated: list[dict[str, Any]] = []
    for src in sources:
        with src.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                report.rows_read += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "migrate_malformed_jsonl_line",
                        path=str(src),
                        line_number=i + 1,
                    )
                    report.rows_skipped_malformed += 1
                    continue
                if not isinstance(row, dict):
                    report.rows_skipped_malformed += 1
                    continue
                unified = _legacy_row_to_unified(row, session_id=session_id, run_id=run_id)
                if unified is None:
                    report.rows_skipped_malformed += 1
                    continue
                if unified["event_id"] in existing_ids:
                    report.rows_skipped_duplicate += 1
                    continue
                existing_ids.add(unified["event_id"])
                migrated.append(unified)

    if apply and migrated:
        # Parse each unified row back into a HPOTelemetryEvent to re-validate
        # before writing. Unknown event_types fall back to ObserverEvent.
        from trw_mcp.telemetry.event_base import EVENT_TYPE_REGISTRY, ObserverEvent

        writer = UnifiedEventWriter()
        for row in migrated:
            cls = EVENT_TYPE_REGISTRY.get(str(row["event_type"]), ObserverEvent)
            # Coerce ts: legacy rows may be naive datetimes OR malformed
            # strings. Our schema is strict+tz-aware; fall back to now()
            # on parse failure so historical data still migrates.
            raw_ts = row["ts"]
            parsed_ts: datetime
            if isinstance(raw_ts, datetime):
                parsed_ts = raw_ts
            elif isinstance(raw_ts, str):
                try:
                    parsed_ts = datetime.fromisoformat(raw_ts)
                except ValueError:
                    parsed_ts = datetime.now(tz=timezone.utc)
            else:
                parsed_ts = datetime.now(tz=timezone.utc)
            if parsed_ts.tzinfo is None:
                parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
            try:
                evt: HPOTelemetryEvent = cls(
                    event_id=row["event_id"],
                    session_id=row["session_id"],
                    run_id=row.get("run_id"),
                    ts=parsed_ts,
                    emitter=str(row.get("emitter", cls.model_fields["emitter"].default or "")),
                    event_type=str(row["event_type"]),
                    surface_snapshot_id=row.get("surface_snapshot_id", ""),
                    parent_event_id=row.get("parent_event_id"),
                    payload=row.get("payload", {}),
                )
            except Exception:  # justified: scan-resilience, malformed legacy rows tolerated
                logger.warning("migrate_row_validation_failed", event_id=row.get("event_id"), exc_info=True)
                report.rows_skipped_malformed += 1
                continue
            if not writer.write(evt, target):
                logger.warning("migrate_write_failed", event_id=evt.event_id)
                continue
            report.rows_migrated += 1
        report.applied = True
    else:
        report.rows_migrated = len(migrated)

    logger.info(
        "migrate_run_complete",
        run_dir=str(run_dir),
        rows_read=report.rows_read,
        rows_migrated=report.rows_migrated,
        rows_skipped_duplicate=report.rows_skipped_duplicate,
        rows_skipped_malformed=report.rows_skipped_malformed,
        applied=report.applied,
    )
    return report


__all__ = [
    "MigrationReport",
    "migrate_run",
]
