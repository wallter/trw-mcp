"""State persistence Protocol interfaces — extracted from persistence.py for module-size compliance.

Belongs to the ``persistence.py`` facade. Re-exported there for backward
compatibility with callers that type-annotate against StateReader / StateWriter /
EventLogger via the parent module.

Protocols (PEP 544 structural typing) decouple the persistence layer from the
concrete File* implementations — callers depend on the interface, not the class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


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
