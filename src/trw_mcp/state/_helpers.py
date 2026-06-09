"""Shared utility helpers for state modules.

Centralizes common patterns that were duplicated across analytics.py,
tiers.py, consolidation.py, dedup.py, and other state modules.

This module should NOT import from tools/ or other state modules to
avoid circular dependencies. It only depends on models/ and persistence.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Safe type extraction from dict[str, object] values
# ---------------------------------------------------------------------------


def safe_int(data: Mapping[str, object], key: str, default: int = 0) -> int:
    """Safely extract an integer from a dict with heterogeneous values.

    Handles str, int, float, and None values without raising.

    Args:
        data: Mapping with mixed-type values (dict or TypedDict).
        key: Key to extract.
        default: Fallback value if key is missing or conversion fails.

    Returns:
        Integer value, or default on any failure.
    """
    try:
        return int(str(data.get(key, default)))
    except (ValueError, TypeError):
        return default


def safe_float(data: Mapping[str, object], key: str, default: float = 0.0) -> float:
    """Safely extract a float from a dict with heterogeneous values.

    Args:
        data: Mapping with mixed-type values (dict or TypedDict).
        key: Key to extract.
        default: Fallback value if key is missing or conversion fails.

    Returns:
        Float value, or default on any failure.
    """
    try:
        return float(str(data.get(key, default)))
    except (ValueError, TypeError):
        return default


def safe_str(data: Mapping[str, object], key: str, default: str = "") -> str:
    """Safely extract a string from a dict with heterogeneous values.

    Args:
        data: Mapping with mixed-type values (dict or TypedDict).
        key: Key to extract.
        default: Fallback value if key is missing.

    Returns:
        String value, or default if missing.
    """
    val = data.get(key, default)
    return str(val) if val is not None else default


# ---------------------------------------------------------------------------
# Learning text helpers
# ---------------------------------------------------------------------------


def truncate_nudge_line(text: str, max_length: int = 80) -> str:
    """Truncate a nudge line to *max_length*, preferring word boundaries."""
    if len(text) <= max_length:
        return text
    boundary_start = max(max_length - 20, 0)
    for i in range(boundary_start, max_length):
        if text[i] == " ":
            return text[:i] + "\u2026"
    return text[:max_length]


# ---------------------------------------------------------------------------
# Entry file iteration
# ---------------------------------------------------------------------------


def rotate_jsonl(path: Path, max_bytes: int = 10 * 1024 * 1024) -> None:
    """Rotate a JSONL file when it exceeds *max_bytes*.

    Renames the current file to ``{name}.1`` (overwriting any existing
    ``.1``).  The caller starts writing to a fresh file.

    Fail-open: rotation failure does not block logging.

    Args:
        path: Path to the JSONL file to potentially rotate.
        max_bytes: Maximum file size before rotation (default 10 MB).
    """
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            rotated = path.with_suffix(path.suffix + ".1")
            path.rename(rotated)
    except OSError:
        pass  # fail-open: rotation failure doesn't block logging


def read_jsonl_tail(path: Path, max_entries: int) -> list[dict[str, object]]:
    """Read the last *max_entries* JSON objects from a JSONL log, skipping
    corrupt lines.

    Resilient by design: a single malformed line (e.g. a torn concurrent
    append, where two writers interleave a partial record) is dropped rather
    than discarding every valid record in the window. The previous idiom —
    ``[json.loads(line) for line in lines[-N:]]`` wrapped in one ``try`` —
    returned ``[]`` for the whole file when any line failed, silently wiping
    the entire history that drives nudge-fatigue and propensity scoring.

    Resilience extends to encoding, not just JSON syntax. The read decodes
    each line individually rather than the whole file at once: a single
    non-UTF-8 byte row (a torn append that splits a multi-byte sequence, or a
    binary-garbage line) is dropped on its own ``UnicodeDecodeError`` instead
    of failing the whole-file ``read_text`` and discarding every valid record
    in the window. The bytes are split on the ``\\n`` separator before any
    decode, so an undecodable row is contained to its own line.

    This mirrors the per-line recovery already used by the full-scan session
    reader (``surface_tracking._read_all_surface_events_for_session``), so the
    tail and full-scan paths over the same log degrade identically. Non-object
    lines (bare scalars/lists) are also dropped so callers can index records as
    dicts without guarding. Fail-open: returns ``[]`` when the file is missing
    or unreadable.

    Args:
        path: Path to the JSONL file.
        max_entries: Maximum number of records to return from the tail.

    Returns:
        Parsed dict records from the last *max_entries* lines, newest last;
        corrupt, undecodable, and non-object lines omitted.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_bytes()
    except OSError:
        logger.debug("jsonl_tail_read_failed", path=str(path), exc_info=True)
        return []
    # Slice the tail window on the raw byte lines *before* decoding so an
    # undecodable row outside the window is never touched and the whole-file
    # decode is avoided. See _parse_jsonl_byte_lines for the per-line recovery.
    return _parse_jsonl_byte_lines(raw.strip().split(b"\n")[-max_entries:], path, reader="tail")


def read_jsonl_resilient(path: Path) -> list[dict[str, object]]:
    """Full-scan JSONL read that skips corrupt, torn, and undecodable lines.

    The resilient full-scan counterpart to :func:`read_jsonl_tail`: identical
    per-line decode-and-skip discipline, but returns *every* valid record in
    file order instead of only the tail window.

    Contrast with :meth:`trw_mcp.state.persistence.FileStateReader.read_jsonl`,
    which raises ``StateError`` on the first malformed line. That strictness is
    correct for callers that treat any corruption as fatal (and is contract-
    tested), but wrong for content-free advisory diagnostics over append-only
    logs (e.g. ``events.jsonl``): there, a single torn concurrent append must
    degrade to "drop that one line", not "abort the whole read". Use this
    reader on the advisory path; keep ``read_jsonl`` on the strict path.

    Fail-open: returns ``[]`` when the file is missing or unreadable.

    Args:
        path: Path to the JSONL file.

    Returns:
        Parsed dict records in file order; corrupt, undecodable, and
        non-object lines omitted.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_bytes()
    except OSError:
        logger.debug("jsonl_resilient_read_failed", path=str(path), exc_info=True)
        return []
    return _parse_jsonl_byte_lines(raw.split(b"\n"), path, reader="resilient")


def _parse_jsonl_byte_lines(
    byte_lines: list[bytes],
    path: Path,
    *,
    reader: str,
) -> list[dict[str, object]]:
    """Decode and JSON-parse pre-split JSONL byte lines, skipping bad rows.

    Shared core of :func:`read_jsonl_tail` and :func:`read_jsonl_resilient`.
    Splitting on the newline byte before decoding contains a single non-UTF-8
    byte row to its own line: it raises ``UnicodeDecodeError`` here and is
    skipped, rather than aborting a whole-file decode and dropping every valid
    record. Blank and non-object lines are dropped so callers can index records
    as dicts without guarding.

    Args:
        byte_lines: Raw byte lines (already newline-split, possibly tail-sliced).
        path: Source path, used only for skip-diagnostic logging.
        reader: Name of the calling reader (``"tail"`` / ``"resilient"``),
            emitted as a field on each ``jsonl_line_skipped`` event so skips
            from the two readers stay distinguishable in logs.

    Returns:
        Parsed dict records in input order; corrupt rows omitted.
    """
    records: list[dict[str, object]] = []
    for byte_line in byte_lines:
        stripped = byte_line.strip()
        if not stripped:
            continue
        try:
            text = stripped.decode("utf-8")
        except UnicodeDecodeError:
            logger.debug("jsonl_line_skipped", reader=reader, path=str(path), reason="decode")
            continue
        try:
            rec = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.debug("jsonl_line_skipped", reader=reader, path=str(path), reason="json")
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def iter_yaml_entry_files(entries_dir: Path) -> Iterator[Path]:
    """Iterate over YAML entry files in a directory, skipping index.yaml.

    This is the canonical way to iterate learning entries. Yields paths
    sorted by name for deterministic ordering.

    Args:
        entries_dir: Directory containing YAML entry files.

    Yields:
        Path objects for each .yaml file (excluding index.yaml).
    """
    if not entries_dir.is_dir():
        return
    for yaml_file in sorted(entries_dir.glob("*.yaml")):
        if yaml_file.name == "index.yaml":
            continue
        yield yaml_file


def is_active_entry(data: Mapping[str, object]) -> bool:
    """Check if a learning entry dict has active status.

    The default status is 'active' for entries that don't have an
    explicit status field.

    Args:
        data: Entry mapping loaded from YAML (dict or TypedDict).

    Returns:
        True if the entry is active.
    """
    return str(data.get("status", "active")) == "active"


# ---------------------------------------------------------------------------
# Framework version (PRD-FIX-045-FR03)
# ---------------------------------------------------------------------------


def read_framework_version() -> str:
    """Read the framework version from the bundled framework.md file.

    Parses the first line of data/framework.md. Returns 'unknown' if
    the file is missing or unparseable.
    """
    fw_path = Path(__file__).resolve().parent.parent / "data" / "framework.md"
    if fw_path.exists():
        first_line = fw_path.read_text(encoding="utf-8").split("\n", 1)[0]
        if "\u2014" in first_line:
            return first_line.split("\u2014")[0].strip().split()[0]
        return first_line.split()[0] if first_line.strip() else "unknown"
    return "unknown"


# ---------------------------------------------------------------------------
# Backward-compat module-level singleton shim (FIX-044 DRY)
# ---------------------------------------------------------------------------


def _compat_getattr(name: str) -> object:
    """Backward-compat shim for module-level ``_config``/``_reader``/``_writer`` access.

    Tests patch these module attributes directly. This helper provides
    lazy construction so the attributes exist on first access.

    Usage — in each consumer module, keep a module-level ``__getattr__`` that
    delegates here (Python requires the function to live in the module)::

        from trw_mcp.state._helpers import _compat_getattr

        def __getattr__(name: str) -> object:
            return _compat_getattr(name)

    .. deprecated:: v0.13
        Migrate test patches to use ``get_config()`` / ``FileStateReader()`` /
        ``FileStateWriter()`` directly.

    Raises:
        AttributeError: If *name* is not one of the three known singletons.
    """
    if name == "_config":
        warnings.warn(
            "Module-level '_config' is deprecated. Import from the canonical module instead. Will be removed in v1.0.",
            DeprecationWarning,
            stacklevel=3,
        )
        from trw_mcp.models.config import get_config

        return get_config()
    if name == "_reader":
        warnings.warn(
            "Module-level '_reader' is deprecated. Import from the canonical module instead. Will be removed in v1.0.",
            DeprecationWarning,
            stacklevel=3,
        )
        from trw_mcp.state.persistence import FileStateReader

        return FileStateReader()
    if name == "_writer":
        warnings.warn(
            "Module-level '_writer' is deprecated. Import from the canonical module instead. Will be removed in v1.0.",
            DeprecationWarning,
            stacklevel=3,
        )
        from trw_mcp.state.persistence import FileStateWriter

        return FileStateWriter()
    raise AttributeError(f"module has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_project_config(trw_dir: Path) -> TRWConfig:
    """Load a target project's config.yaml into a TRWConfig instance.

    This is the canonical way to load project config. Consolidates the
    duplicate implementations that existed in audit.py and export.py.

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        TRWConfig instance (defaults if config.yaml is missing or invalid).
    """
    from pydantic import ValidationError

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    config_path = trw_dir / "config.yaml"
    if config_path.exists():
        reader = FileStateReader()
        try:
            data = reader.read_yaml(config_path)
            return TRWConfig.model_validate(
                {k: v for k, v in data.items() if v is not None},
            )
        except ValidationError as exc:
            logger.warning(
                "config_validation_failed",
                path=str(config_path),
                errors=str(exc),
            )
            return TRWConfig()
        except (OSError, ValueError) as exc:
            logger.warning(
                "config_read_failed",
                path=str(config_path),
                error=str(exc),
            )
            return TRWConfig()
    return TRWConfig()
