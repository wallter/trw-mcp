"""Persistence utility helpers — extracted from persistence.py for module-size compliance.

Belongs to the ``persistence.py`` facade. Re-exported there for backward
compatibility with callers that import via the parent module.

Pure utility functions:
- ``_safe_yaml`` / ``_roundtrip_yaml`` / ``_new_yaml`` — YAML factory wrappers
- ``json_serializer`` — datetime/date-aware JSON encoder hook
- ``model_to_dict`` — Pydantic model → plain dict via JSON round-trip
- ``_suppress_internal_events`` / ``INTERNAL_EVENT_TYPES`` —
  contextvars flag + frozenset for FileEventLogger suppression
- ``suppress_internal_events`` / ``lock_for_rmw`` — context managers
  for internal-event suppression and advisory file locking
"""

from __future__ import annotations

import contextlib
import contextvars
import json
from collections.abc import Generator
from datetime import date, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from ruamel.yaml import YAML

from trw_mcp._locking import _lock_ex, _lock_un

# PRD-FIX-053-FR06: Contextvars flag to suppress FileEventLogger.log_event()
# during internal persistence operations. Set True inside write_yaml/append_jsonl
# so that any log_event calls triggered by those paths don't write to
# session-events.jsonl. Implemented via contextvars so it is thread-safe and
# call-stack scoped (resets automatically on context exit).
_suppress_internal_events: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_suppress_internal_events",
    default=False,
)

# Internal event types that are suppressed when _suppress_internal_events is set.
# User-facing tool events (tool_invocation, session_start, checkpoint, etc.) are
# NOT in this list and will never be suppressed.
INTERNAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "jsonl_appended",
        "yaml_written",
        "vector_upserted",
        "index_synced",
        "dedup_run",
        "tier_updated",
    }
)


def _safe_yaml() -> YAML:
    """Safe YAML loader for reading untrusted content.

    Uses ruamel.yaml's safe loader (typ="safe") which rejects !!python/object
    and other constructor tags that would enable RCE. Use for all read paths
    where the YAML source may be user-editable (e.g. config.yaml, run.yaml).

    ruamel.yaml's YAML class maintains internal state that is NOT thread-safe.
    Creating a fresh instance per operation prevents concurrent read corruption
    (PRD-CORE-014 FR03).
    """
    return YAML(typ="safe")


def _roundtrip_yaml() -> YAML:
    """Round-trip YAML for write operations that preserve formatting.

    Uses the default round-trip loader/dumper so that comments and key ordering
    are preserved when serializing framework-generated data. Only use this for
    write paths — never for parsing user-supplied YAML content.

    ruamel.yaml's YAML class maintains internal emitter state that is
    NOT thread-safe.  Creating a fresh instance per operation prevents
    concurrent write corruption (PRD-CORE-014 FR03).
    """
    yml = YAML()
    yml.default_flow_style = False
    yml.preserve_quotes = True
    return yml


def _new_yaml() -> YAML:
    """Deprecated alias kept for any call sites not yet migrated.

    New code should use _safe_yaml() for reads and _roundtrip_yaml() for writes.
    """
    return _roundtrip_yaml()


def json_serializer(obj: object) -> str:
    """JSON serializer for objects not serializable by default json code.

    Args:
        obj: Object to serialize.

    Returns:
        JSON-compatible string representation.

    Raises:
        TypeError: If object type is not supported.
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    msg = f"Object of type {type(obj).__name__} is not JSON serializable"
    raise TypeError(msg)


def model_to_dict(model: BaseModel) -> dict[str, object]:
    """Convert a Pydantic model to a plain dict suitable for YAML serialization.

    Converts enums to their values and dates to ISO strings.

    Args:
        model: Pydantic model instance.

    Returns:
        Plain dictionary with JSON-compatible values.
    """
    return cast("dict[str, object]", json.loads(model.model_dump_json()))


@contextlib.contextmanager
def suppress_internal_events() -> Generator[None, None, None]:
    """Context manager that suppresses internal event types in FileEventLogger.

    PRD-FIX-053-FR06: Set inside internal persistence operations so that any
    FileEventLogger.log_event() calls triggered by those paths skip writing
    INTERNAL_EVENT_TYPES to session-events.jsonl.

    Uses contextvars so the flag is thread-safe and call-stack scoped —
    it resets automatically when the with-block exits.

    Example::

        with suppress_internal_events():
            writer.write_yaml(path, data)  # no yaml_written event emitted
    """
    token = _suppress_internal_events.set(True)
    try:
        yield
    finally:
        _suppress_internal_events.reset(token)


def _resolve_hpo_event_context(
    events_path: Path,
    data: dict[str, object],
) -> tuple[str, str | None, str]:
    """Resolve ``(session_id, run_id, surface_snapshot_id)`` for unified HPO rows.

    Legacy emitters often omit these H1 fields. When the legacy write target
    is ``<run>/meta/events.jsonl``, recover them from ``run.yaml`` and/or
    ``run_surface_snapshot.yaml`` so the parallel-emitted unified row still
    satisfies FR-2 / FR-12.
    """
    # Lazy-import FileStateReader + StateError to avoid the circular dep
    # with persistence.py (which re-exports this function).
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateReader

    session_id = str(data.get("session_id", "")) if data.get("session_id") is not None else ""
    run_id = str(data.get("run_id")) if data.get("run_id") is not None else None
    surface_snapshot_id = (
        str(data.get("surface_snapshot_id", "")) if data.get("surface_snapshot_id") is not None else ""
    )

    if events_path.parent.name != "meta":
        return session_id, run_id, surface_snapshot_id

    meta_dir = events_path.parent
    run_dir = meta_dir.parent
    if run_id is None:
        run_id = run_dir.name

    run_yaml = meta_dir / "run.yaml"
    if run_yaml.exists():
        try:
            run_data = FileStateReader().read_yaml(run_yaml)
        except StateError:
            run_data = {}
        if not session_id and run_data.get("owner_session_id") is not None:
            session_id = str(run_data.get("owner_session_id", ""))
        if not surface_snapshot_id and run_data.get("surface_snapshot_id") is not None:
            surface_snapshot_id = str(run_data.get("surface_snapshot_id", ""))

    if not surface_snapshot_id:
        snapshot_path = meta_dir / "run_surface_snapshot.yaml"
        if snapshot_path.exists():
            try:
                snapshot_data = FileStateReader().read_yaml(snapshot_path)
            except StateError:
                snapshot_data = {}
            if snapshot_data.get("snapshot_id") is not None:
                surface_snapshot_id = str(snapshot_data.get("snapshot_id", ""))

    return session_id, run_id, surface_snapshot_id


@contextlib.contextmanager
def lock_for_rmw(path: Path) -> Generator[Path, None, None]:
    """Advisory exclusive lock for read-modify-write cycles.

    Acquires an exclusive lock on ``{path}.lock`` before yielding,
    releases after the block completes (or on exception).  This prevents
    concurrent R-M-W races on the same file (e.g., learnings/index.yaml).

    Args:
        path: The file being protected.  A sibling ``.lock`` file is used.

    Yields:
        The original *path* (unchanged) for convenience.
    """
    lock_path = path.parent / f"{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = lock_path.open("w", encoding="utf-8")
    try:
        _lock_ex(lock_fh.fileno())
        yield path
    finally:
        _lock_un(lock_fh.fileno())
        lock_fh.close()
