"""Atomic YAML/JSONL read/write with advisory file locks.

All state persistence goes through this module. Writes are atomic
(write to temp file, then rename) to prevent corruption on interrupts.
"""

from __future__ import annotations

__all__ = [
    "EventLogger",
    "FileEventLogger",
    "FileStateReader",
    "FileStateWriter",
    "StateReader",
    "StateWriter",
]

import contextlib
import contextvars
import json
import os
import tempfile
from collections.abc import Generator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol, cast

import structlog
from pydantic import BaseModel
from ruamel.yaml import YAML

from trw_mcp._locking import _lock_ex, _lock_sh, _lock_un
from trw_mcp.exceptions import StateError

# PRD-CORE-001: Base MCP tool suite — atomic file state persistence

logger = structlog.get_logger(__name__)

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


class StateReader(Protocol):
    """Read framework state from persistent storage."""

    def read_yaml(self, path: Path) -> dict[str, object]:
        """Read and parse a YAML file, returning its top-level mapping."""
        ...

    def read_jsonl(self, path: Path) -> list[dict[str, object]]:
        """Read a JSONL file, returning a list of parsed records."""
        ...

    def exists(self, path: Path) -> bool:
        """Check whether a file exists at the given path."""
        ...


class StateWriter(Protocol):
    """Write framework state to persistent storage."""

    def write_yaml(self, path: Path, data: dict[str, object]) -> None:
        """Atomically write *data* as YAML to *path*."""
        ...

    def append_jsonl(self, path: Path, record: dict[str, object]) -> None:
        """Append a single JSON record as a new line in *path*."""
        ...

    def write_text(self, path: Path, content: str) -> None:
        """Atomically write *content* as UTF-8 text to *path*."""
        ...

    def ensure_dir(self, path: Path) -> None:
        """Create *path* and any missing parents if they do not exist."""
        ...


class EventLogger(Protocol):
    """Append structured events to event stream."""

    def log_event(self, events_path: Path, event_type: str, data: dict[str, object]) -> None:
        """Append a timestamped event record to the JSONL stream at *events_path*."""
        ...


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


class FileStateReader:
    """File-based implementation of StateReader."""

    def read_yaml(self, path: Path) -> dict[str, object]:
        """Read and parse a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            Parsed YAML content as a dictionary.

        Raises:
            StateError: If file cannot be read or parsed.
        """
        if not path.exists():
            raise StateError(f"YAML file not found: {path}", path=str(path))
        try:
            with path.open("r", encoding="utf-8") as fh:
                _lock_sh(fh.fileno())
                try:
                    data = _safe_yaml().load(fh)
                finally:
                    _lock_un(fh.fileno())
        except Exception as exc:  # justified: boundary, wrap unknown I/O errors as StateError
            raise StateError(
                f"Failed to read YAML: {exc}",
                path=str(path),
            ) from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise StateError(
                f"YAML root must be a mapping, got {type(data).__name__}",
                path=str(path),
            )
        result: dict[str, object] = dict(data)
        return result

    def read_jsonl(self, path: Path) -> list[dict[str, object]]:
        """Read and parse a JSONL file (one JSON object per line).

        Args:
            path: Path to the JSONL file.

        Returns:
            List of parsed JSON objects.

        Raises:
            StateError: If file cannot be read or parsed.
        """
        if not path.exists():
            return []
        try:
            records: list[dict[str, object]] = []
            with path.open("r", encoding="utf-8") as fh:
                _lock_sh(fh.fileno())
                try:
                    for line_num, line in enumerate(fh, start=1):
                        stripped = line.strip()
                        if not stripped:
                            continue
                        record = json.loads(stripped)
                        if isinstance(record, dict):
                            records.append(record)
                        else:
                            logger.warning(
                                "jsonl_non_dict_line",
                                path=str(path),
                                line=line_num,
                            )
                finally:
                    _lock_un(fh.fileno())
            return records
        except json.JSONDecodeError as exc:
            raise StateError(
                f"Failed to parse JSONL: {exc}",
                path=str(path),
            ) from exc
        except StateError:
            raise
        except Exception as exc:  # justified: boundary, wrap unknown I/O errors as StateError
            raise StateError(
                f"Failed to read JSONL: {exc}",
                path=str(path),
            ) from exc

    def exists(self, path: Path) -> bool:
        """Check if a path exists.

        Args:
            path: Path to check.

        Returns:
            True if path exists.
        """
        return path.exists()


class FileStateWriter:
    """File-based implementation of StateWriter with atomic writes."""

    def write_yaml(self, path: Path, data: dict[str, object]) -> None:
        """Atomically write data to a YAML file.

        Writes to a temporary file in the same directory, then renames.
        This prevents corruption if the process is interrupted.

        Args:
            path: Target YAML file path.
            data: Dictionary to serialize as YAML.

        Raises:
            StateError: If write fails.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to temp file in same directory (atomic rename requires same filesystem)
            fd, tmp_path_str = tempfile.mkstemp(
                dir=str(path.parent),
                suffix=".yaml.tmp",
            )
            tmp_path = Path(tmp_path_str)
            try:
                with tmp_path.open("w", encoding="utf-8") as fh:
                    _lock_ex(fh.fileno())
                    try:
                        _roundtrip_yaml().dump(data, fh)
                    finally:
                        _lock_un(fh.fileno())
                tmp_path.rename(path)
            except Exception:  # justified: cleanup, temp file removal must not mask original error
                tmp_path.unlink(missing_ok=True)
                raise
            finally:
                with contextlib.suppress(OSError):
                    os.close(fd)
            logger.debug("yaml_written", path=str(path))
        except StateError:
            raise
        except (OSError, ValueError) as exc:
            logger.exception("yaml_write_failed", path=str(path), error=str(exc))
            raise StateError(
                f"Failed to write YAML: {exc}",
                path=str(path),
            ) from exc

    def append_jsonl(self, path: Path, record: dict[str, object]) -> None:
        """Append a JSON record to a JSONL file.

        Args:
            path: Target JSONL file path.
            record: Dictionary to serialize as a single JSON line.

        Raises:
            StateError: If append fails.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, default=json_serializer) + "\n"
            with path.open("a", encoding="utf-8") as fh:
                _lock_ex(fh.fileno())
                try:
                    fh.write(line)
                    fh.flush()
                finally:
                    _lock_un(fh.fileno())
            logger.debug("jsonl_appended", path=str(path), event_type=record.get("event"))
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("jsonl_append_failed", path=str(path), error=str(exc))
            raise StateError(
                f"Failed to append JSONL: {exc}",
                path=str(path),
            ) from exc

    def write_text(self, path: Path, content: str) -> None:
        """Atomically write text content to a file.

        Uses the same temp-file-then-rename strategy as ``write_yaml``
        to prevent corruption on interrupted writes.

        Args:
            path: Target file path.
            content: Text content to write.

        Raises:
            StateError: If write fails.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path_str = tempfile.mkstemp(
                dir=str(path.parent),
                suffix=".tmp",
            )
            tmp_path = Path(tmp_path_str)
            try:
                with tmp_path.open("w", encoding="utf-8") as fh:
                    fh.write(content)
                    fh.flush()
                tmp_path.rename(path)
            except Exception:  # justified: cleanup, temp file removal must not mask original error
                tmp_path.unlink(missing_ok=True)
                raise
            finally:
                with contextlib.suppress(OSError):
                    os.close(fd)
        except StateError:
            raise
        except Exception as exc:  # justified: boundary, wrap unknown I/O errors as StateError
            raise StateError(
                f"Failed to write text file: {exc}",
                path=str(path),
            ) from exc

    def ensure_dir(self, path: Path) -> None:
        """Ensure a directory exists, creating parents as needed.

        Args:
            path: Directory path to create.

        Raises:
            StateError: If directory creation fails.
        """
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(
                f"Failed to create directory: {exc}",
                path=str(path),
            ) from exc


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


class FileEventLogger:
    """File-based implementation of EventLogger."""

    def __init__(self, writer: FileStateWriter | None = None) -> None:
        """Initialize with an optional writer.

        Args:
            writer: StateWriter to use. Creates a new one if None.
        """
        self._writer = writer or FileStateWriter()

    def log_event(
        self,
        events_path: Path,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        """Log a structured event to events.jsonl.

        PRD-FIX-053-FR06: When _suppress_internal_events is True in the current
        context and event_type is in INTERNAL_EVENT_TYPES, the event is not
        written to disk. User-facing tool events are never suppressed.

        Args:
            events_path: Path to events.jsonl file.
            event_type: Event type identifier (e.g., "run_init", "phase_enter").
            data: Additional event data.
        """
        if _suppress_internal_events.get() and event_type in INTERNAL_EVENT_TYPES:
            return
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **data,
        }
        self._writer.append_jsonl(events_path, record)
        logger.debug("event_logged", event_type=event_type, path=str(events_path))

        # PRD-HPO-MEAS-001 FR-3/FR-10: Phase 2 parallel-emit. Every legacy
        # event also lands in the unified events-YYYY-MM-DD.jsonl file so
        # trw_query_events returns a merged cross-emitter view. Fail-open
        # so unified-file failures never break the legacy write path.
        try:
            self._parallel_emit_unified(events_path, event_type, data)
        except Exception:  # justified: fail-open, Phase 2 retrofit must not block legacy emitters
            logger.debug("unified_parallel_emit_failed", event_type=event_type, exc_info=True)

    def _parallel_emit_unified(
        self,
        events_path: Path,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        """Phase 2 retrofit — emit a HPOTelemetryEvent shape alongside the legacy row.

        Mapping from legacy ``event_type`` to HPOTelemetryEvent subclass
        follows the v1_to_unified migration dictionary. Events whose path
        is under ``<run>/meta/`` produce ``<run>/meta/events-<date>.jsonl``
        siblings; events under the context/session-events fallback write
        to a same-dir dated file.
        """
        # Lazy imports to avoid a persistence ↔ telemetry cycle at module load.
        from trw_mcp.migration.v1_to_unified import _LEGACY_EVENT_TYPE_MAP
        from trw_mcp.telemetry.event_base import EVENT_TYPE_REGISTRY, ObserverEvent
        from trw_mcp.telemetry.unified_events import emit as emit_unified

        unified_type = _LEGACY_EVENT_TYPE_MAP.get(event_type, "observer")
        cls = EVENT_TYPE_REGISTRY.get(unified_type, ObserverEvent)

        # Carve reserved vs payload keys exactly like the migration tool.
        reserved = {"event", "ts", "session_id", "run_id"}
        payload: dict[str, object] = {k: v for k, v in data.items() if k not in reserved}
        payload.setdefault("legacy_event", event_type)

        session_id, run_id, surface_snapshot_id = _resolve_hpo_event_context(events_path, data)

        try:
            event = cls(
                session_id=session_id,
                run_id=run_id,
                emitter=str(cls.model_fields["emitter"].default or unified_type),
                event_type=unified_type,
                surface_snapshot_id=surface_snapshot_id,
                parent_event_id=str(data.get("parent_event_id", "")) or None,
                payload=payload,
            )
        except Exception:  # justified: fail-open, subclass constraints may reject legacy shapes
            logger.debug("unified_event_build_failed", event_type=event_type, exc_info=True)
            return

        parent = events_path.parent
        run_dir = parent.parent if parent.name == "meta" else None
        fallback_dir = None if parent.name == "meta" else parent
        emit_unified(event, run_dir=run_dir, fallback_dir=fallback_dir)


def model_to_dict(model: BaseModel) -> dict[str, object]:
    """Convert a Pydantic model to a plain dict suitable for YAML serialization.

    Converts enums to their values and dates to ISO strings.

    Args:
        model: Pydantic model instance.

    Returns:
        Plain dictionary with JSON-compatible values.
    """
    return cast("dict[str, object]", json.loads(model.model_dump_json()))
