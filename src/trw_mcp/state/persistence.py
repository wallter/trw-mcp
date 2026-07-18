# ruff: noqa: E402
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
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

import structlog

from trw_mcp._locking import _lock_ex, _lock_sh, _lock_un
from trw_mcp.exceptions import StateError

# PRD-CORE-001: Base MCP tool suite — atomic file state persistence

logger = structlog.get_logger(__name__)


def _atomic_write_text_file(path: Path, suffix: str, write: Callable[[TextIO], object]) -> None:
    """Write through the ``mkstemp`` descriptor, then replace the target."""
    fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), suffix=suffix)
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1  # ownership transferred to ``fh``
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)


# Suppress-internal-events ContextVar + INTERNAL_EVENT_TYPES extracted to
# _persistence_helpers (PRD-DIST-243 batch 15). Re-exported here for callers.
from trw_mcp.state._persistence_helpers import (
    INTERNAL_EVENT_TYPES as INTERNAL_EVENT_TYPES,
)

# YAML factories + json/model utilities extracted to _persistence_helpers
# (PRD-DIST-243 batch 12). Re-exported for backward compatibility.
from trw_mcp.state._persistence_helpers import (
    _new_yaml as _new_yaml,
)

# _resolve_hpo_event_context extracted to _persistence_helpers (PRD-DIST-243 batch 15b).
from trw_mcp.state._persistence_helpers import (
    _resolve_hpo_event_context as _resolve_hpo_event_context,
)
from trw_mcp.state._persistence_helpers import (
    _roundtrip_yaml as _roundtrip_yaml,
)
from trw_mcp.state._persistence_helpers import (
    _safe_yaml as _safe_yaml,
)
from trw_mcp.state._persistence_helpers import (
    _suppress_internal_events as _suppress_internal_events,
)

# json_serializer extracted to _persistence_helpers (PRD-DIST-243 batch 12).
from trw_mcp.state._persistence_helpers import (
    json_serializer as json_serializer,
)

# Protocol interfaces extracted to _persistence_protocols (PRD-DIST-243 batch 16).
# Re-exported here for backward compatibility with callers that type-annotate
# against StateReader / StateWriter / EventLogger via this facade.
from trw_mcp.state._persistence_protocols import (
    EventLogger as EventLogger,
)
from trw_mcp.state._persistence_protocols import (
    StateReader as StateReader,
)
from trw_mcp.state._persistence_protocols import (
    StateWriter as StateWriter,
)


class FileStateReader:
    """File-based implementation of StateReader."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir.resolve() if base_dir is not None else None

    def _check_contained(self, path: Path) -> Path:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as exc:
            raise StateError(
                f"state read path resolution failed: {type(exc).__name__}",
                path=str(path),
            ) from None
        if self._base_dir is not None and not resolved.is_relative_to(self._base_dir):
            raise StateError(f"state read path escapes base directory: {resolved}", path=str(resolved))
        return resolved

    def read_yaml(self, path: Path) -> dict[str, object]:
        """Read and parse a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            Parsed YAML content as a dictionary.

        Raises:
            StateError: If file cannot be read or parsed.
        """
        checked_path = self._check_contained(path)
        if not checked_path.exists():
            raise StateError(f"YAML file not found: {checked_path}", path=str(checked_path))
        try:
            with checked_path.open("r", encoding="utf-8") as fh:
                _lock_sh(fh.fileno())
                try:
                    data = _safe_yaml().load(fh)
                finally:
                    _lock_un(fh.fileno())
        except Exception as exc:  # justified: boundary, wrap unknown I/O errors as StateError
            raise StateError(
                f"Failed to read YAML: {exc}",
                path=str(checked_path),
            ) from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise StateError(
                f"YAML root must be a mapping, got {type(data).__name__}",
                path=str(checked_path),
            )
        result: dict[str, object] = dict(data)
        return result

    def read_jsonl(self, path: Path, *, strict: bool = False) -> list[dict[str, object]]:
        """Read and parse a JSONL file (one JSON object per line).

        By default this reader is *lenient* about a single malformed line: a
        torn/truncated row (e.g. the process was killed mid-append, leaving a
        partial final line) is skipped and counted rather than aborting the
        whole read. This matters because ``events.jsonl`` and its siblings are
        append-only logs read best-effort by delivery/ceremony helpers that
        collapse any raised ``StateError`` to an empty list — so under the old
        strict behavior one truncated tail line poisoned *every* event read for
        a run. Skipping the bad line preserves all the valid records.

        Non-JSON I/O failures (unreadable file, unexpected errors) still raise
        ``StateError`` in both modes — leniency is scoped to per-line JSON decode
        failures only, never to a genuinely broken read.

        Args:
            path: Path to the JSONL file.
            strict: When True, a malformed line raises ``StateError`` (the
                pre-2026-07 contract) instead of being skipped. Integrity-
                sensitive callers that must treat any corruption as fatal opt in
                here; the append-only-log callers (the default) stay lenient.

        Returns:
            List of parsed JSON objects. Malformed lines are skipped (lenient
            mode) with a single aggregated warning naming the skipped count.

        Raises:
            StateError: If the file cannot be read, or (``strict=True`` only) a
                line fails to parse as JSON.
        """
        checked_path = self._check_contained(path)
        if not checked_path.exists():
            return []
        records: list[dict[str, object]] = []
        skipped = 0
        try:
            with checked_path.open("r", encoding="utf-8") as fh:
                _lock_sh(fh.fileno())
                try:
                    for line_num, line in enumerate(fh, start=1):
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            record = json.loads(stripped)
                        except json.JSONDecodeError:
                            if strict:
                                raise
                            skipped += 1
                            continue
                        if isinstance(record, dict):
                            records.append(record)
                        else:
                            logger.warning(
                                "jsonl_non_dict_line",
                                path=str(checked_path),
                                line=line_num,
                            )
                finally:
                    _lock_un(fh.fileno())
        except json.JSONDecodeError as exc:
            raise StateError(
                f"Failed to parse JSONL: {exc}",
                path=str(checked_path),
            ) from exc
        except StateError:
            raise
        except Exception as exc:  # justified: boundary, wrap unknown I/O errors as StateError
            raise StateError(
                f"Failed to read JSONL: {exc}",
                path=str(checked_path),
            ) from exc
        if skipped:
            logger.warning(
                "jsonl_malformed_lines_skipped",
                path=str(checked_path),
                skipped=skipped,
                reason="json_decode",
            )
        return records

    def exists(self, path: Path) -> bool:
        """Check if a path exists.

        Args:
            path: Path to check.

        Returns:
            True if path exists.
        """
        return self._check_contained(path).exists()


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
            _atomic_write_text_file(path, ".yaml.tmp", lambda fh: _roundtrip_yaml().dump(data, fh))
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
            _atomic_write_text_file(path, ".tmp", lambda fh: fh.write(content))
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


# suppress_internal_events + lock_for_rmw extracted to _persistence_helpers
# (PRD-DIST-243 batch 15). Re-exported here for callers (analytics/entries.py
# imports lock_for_rmw via this facade; tests import both).
from trw_mcp.state._persistence_helpers import (
    lock_for_rmw as lock_for_rmw,
)
from trw_mcp.state._persistence_helpers import (
    suppress_internal_events as suppress_internal_events,
)


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


# model_to_dict extracted to _persistence_helpers (PRD-DIST-243 batch 12).
from trw_mcp.state._persistence_helpers import (
    model_to_dict as model_to_dict,
)
