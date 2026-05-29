"""Run-yaml + event-jsonl I/O helpers for the stale-run sweep.

Belongs to the ``_run_gc.py`` facade. Re-exported there for back-compat.

Holds the YAML prefilter regexes, header-only YAML reader, full
ruamel round-trip loader, atomic YAML writer, append-only events helper,
and UTC-now formatter that the staleness sweep uses on every run.yaml.

Extracted as DIST-243 batch 28 to keep the parent ``_run_gc.py`` module
under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

logger = structlog.get_logger(__name__)


# Fast pre-filter for top-level YAML fields. Hits one regex per run.yaml
# (~1ms for 200 files) instead of a full ruamel round-trip parse (~35s for
# 200 files; multi-second per file when run.yaml has bloated to several MB
# from accumulated audit_pattern_promotions / deferred_results arrays).
# Anything ambiguous (no match, unexpected value) falls through to the
# authoritative parse path so the regex never silently changes semantics.
_STATUS_PREFILTER = re.compile(r"^status:\s*['\"]?([\w-]+)['\"]?", re.MULTILINE)
_PROTECTED_PREFILTER = re.compile(r"^protected:\s*(true|false|True|False)\s*$", re.MULTILINE)


def _read_yaml_header(run_yaml_path: Path) -> str | None:
    """Read the first 4KB of *run_yaml_path* as text for prefilter scans.

    Capped at 4KB so the prefilter is O(1) regardless of file size — the
    target fields (``status``, ``protected``) are top-level and always
    appear within the first hundred or so bytes of a TRW run.yaml.
    """
    try:
        with run_yaml_path.open("rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return None
    try:
        return chunk.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return None


def _prefilter_status(run_yaml_path: Path) -> str | None:
    """Return the lowercased ``status:`` value via a fast text scan, or None.

    Returns ``None`` on read error or when the field is missing — callers
    must fall back to ``_load_run_yaml`` so the authoritative parser still
    owns the decision.
    """
    text = _read_yaml_header(run_yaml_path)
    if text is None:
        return None
    match = _STATUS_PREFILTER.search(text)
    if match is None:
        return None
    return match.group(1).strip().lower() or None


def _prefilter_protected(run_yaml_path: Path) -> bool | None:
    """Return the boolean ``protected:`` value or None when undetermined.

    None means "could not determine" — the field may be missing, set to
    a non-boolean value, or buried past the 4KB header window. Callers
    fall back to the full YAML parse in that case.
    """
    text = _read_yaml_header(run_yaml_path)
    if text is None:
        return None
    match = _PROTECTED_PREFILTER.search(text)
    if match is None:
        return None
    return match.group(1).lower() == "true"


def _load_run_yaml(run_yaml_path: Path) -> dict[str, Any] | None:
    """Round-trip load *run_yaml_path*, returning ``None`` on any parse failure.

    Uses ruamel ``YAML(typ="rt")`` so we preserve every field on rewrite —
    the sweep only mutates ``status``; every other field (phase, confidence,
    wave data, complexity signals, etc.) round-trips unchanged.
    """
    yaml = YAML(typ="rt")
    try:
        with run_yaml_path.open("r", encoding="utf-8") as fh:
            data = yaml.load(fh)
    except (OSError, YAMLError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # Copy into a plain dict[str, Any] for type-checker happiness while
    # preserving the ruamel mapping so downstream dump preserves ordering.
    return data


def _dump_run_yaml_atomic(run_yaml_path: Path, data: dict[str, Any]) -> None:
    """Atomically write *data* back to *run_yaml_path* using ruamel round-trip.

    Pattern mirrors :class:`trw_mcp.state.persistence.FileStateWriter`:
    write to a ``.tmp`` sibling, fsync, ``os.replace``.
    """
    yaml = YAML(typ="rt")
    run_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        dir=str(run_yaml_path.parent),
        suffix=".yaml.tmp",
    )
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, run_yaml_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _iso_utc_now() -> str:
    """Return current UTC time as an ISO8601 string with ``Z`` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _append_event_best_effort(
    events_path: Path,
    event: str,
    payload: dict[str, Any],
) -> None:
    """Append a single JSON line to *events_path* — log on failure, never raise.

    FR14 obligation: record the abandonment decision in the run's own audit
    trail.  If append fails (disk full, perms), log ``sweep_event_append_failed``
    WARN and carry on — we do NOT revert the status change because operators
    would rather have a stale-abandoned run with no audit entry than an
    actively-competing ``active`` run the sweep silently gave up on.
    """
    record = {"ts": _iso_utc_now(), "event": event, "data": payload}
    try:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
    except OSError as exc:
        logger.warning(
            "sweep_event_append_failed",
            path=str(events_path),
            error=type(exc).__name__,
            detail=str(exc),
        )
