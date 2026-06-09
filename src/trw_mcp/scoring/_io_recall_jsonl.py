"""Recall-tracking JSONL tail-reader for the scoring I/O boundary.

Belongs to the ``_io_boundary.py`` facade. Re-exported there for back-compat
(PRD-FIX-061-FR05) so ``correlate_recalls`` reads the tail of
``recall_tracking.jsonl`` without importing ``FileStateReader`` from the
state layer.

``_read_recall_tracking_jsonl`` looks ``_tail_lines`` up through the facade
module so tests that monkeypatch ``trw_mcp.scoring._io_boundary._tail_lines``
still intercept the call.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _read_recall_tracking_jsonl(
    receipt_path: Path,
    *,
    max_lines: int = 5000,
) -> list[dict[str, object]]:
    """Read the TAIL of the recall-tracking JSONL file, skipping malformed lines.

    Reads from the end of the file to avoid scanning 100K+ old records
    that will be filtered out by the correlation window anyway.  The
    ``max_lines`` cap keeps memory and CPU bounded even for very large
    files (the old implementation read all 172K+ lines).

    PRD-FIX-061-FR05: Extracted from ``correlate_recalls`` so that
    ``_correlation.py`` does not need to import ``FileStateReader``
    from the state layer.

    Args:
        receipt_path: Path to recall_tracking.jsonl.
        max_lines: Maximum number of recent lines to read (default 5000).

    Returns:
        List of record dicts; empty list if file missing or unreadable.
    """
    # Resolve via the facade so monkeypatches on
    # ``trw_mcp.scoring._io_boundary._tail_lines`` are honored.
    from trw_mcp.scoring import _io_boundary

    if not receipt_path.exists():
        return []

    records: list[dict[str, object]] = []
    try:
        # Read the tail of the file efficiently: seek backwards from EOF
        # to find the last ``max_lines`` newline-delimited records.
        raw_lines = _io_boundary._tail_lines(receipt_path, max_lines)
    except OSError as exc:  # justified: fail-open; structural-only signal, no row content
        logger.warning(
            "recall_tracking_read_failed",
            path=str(receipt_path),
            error_class=type(exc).__name__,
        )
        return []

    for tail_index, line in enumerate(raw_lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            _warn_recall_tracking_skip(receipt_path, tail_index, type(exc).__name__)
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            # Valid JSON but not an object (scalar/list): signal instead of
            # dropping silently. The type name carries no row content.
            _warn_recall_tracking_skip(receipt_path, tail_index, f"non_object:{type(record).__name__}")
    return records


def _warn_recall_tracking_skip(receipt_path: Path, tail_index: int, error_class: str) -> None:
    """Emit a structural-only skip warning for one recall-tracking row.

    Rows can carry query/outcome text and learning ids, so observability is
    restricted to path, a stable tail-window index, and an error class.
    """
    logger.warning(
        "recall_tracking_row_skipped",
        path=str(receipt_path),
        tail_index=tail_index,
        error_class=error_class,
    )


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    """Read the last ``max_lines`` lines from a file efficiently.

    Byte-oriented and consistent across small and large files: bytes are read
    then decoded with ``errors="replace"`` so a non-UTF-8 tail row is isolated
    to its own (replacement-char) line and skipped downstream by JSON parsing,
    rather than aborting the whole read. Uses backward seeking from EOF for
    large files; reads the whole file when it is small enough (< 64 KB).
    """
    import os

    file_size = path.stat().st_size
    if file_size == 0:
        return []

    # For small files, read the whole thing in byte mode so decode behavior
    # matches the large-file path (non-UTF-8 rows isolated, not fatal).
    if file_size < 65_536:
        with path.open("rb") as fh:
            raw_small = fh.read().split(b"\n")
        if raw_small and raw_small[-1] == b"":
            raw_small = raw_small[:-1]
        tail_small = raw_small[-max_lines:] if len(raw_small) > max_lines else raw_small
        return [ln.decode("utf-8", errors="replace") for ln in tail_small if ln]

    # For large files, seek backwards in chunks to find enough newlines
    chunk_size = min(file_size, max(4096, max_lines * 256))  # ~256 bytes/line estimate
    raw_lines: list[bytes] = []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        remaining = file_size
        buf = b""
        while remaining > 0 and len(raw_lines) < max_lines + 1:
            read_size = min(chunk_size, remaining)
            remaining -= read_size
            fh.seek(remaining)
            chunk = fh.read(read_size)
            buf = chunk + buf
            raw_lines = buf.split(b"\n")
        # Decode only the tail we need. Drop the split artifact from a trailing
        # newline before slicing so requesting N lines from an N-line file ending
        # in "\n" does not silently return N-1 records.
        if raw_lines and raw_lines[-1] == b"":
            raw_lines = raw_lines[:-1]
        tail = raw_lines[-max_lines:] if len(raw_lines) > max_lines else raw_lines
        return [ln.decode("utf-8", errors="replace") for ln in tail if ln]
