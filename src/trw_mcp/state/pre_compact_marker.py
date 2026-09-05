"""One typed owner for the pre-compaction marker's path and shape.

PRD-CORE-258-FR04. The marker at ``.trw/context/pre_compact_state.json`` had
four bespoke construction sites across three modules and three independently
written failure postures, and nothing typed its contents. This module owns the
filename, the path, the document, and the read — every consumer goes through it.

Two writers, two key spellings. ``trw_pre_compact_checkpoint`` writes
``timestamp`` from :meth:`datetime.isoformat`; the bundled PreCompact shell hook
writes ``ts`` from ``date -u '+%Y-%m-%dT%H:%M:%SZ'`` (and the literal ``unknown``
when ``date`` fails). The hook path is the ordinary Claude Code compaction path,
so a reader that knew only ``timestamp`` would call the most common real marker
in production unreadable while passing every fixture-written test. Both
spellings are read; ``unknown`` is correctly rejected as a non-ISO instant.

The marker is UNTRUSTED input (PRD-CORE-258-NFR03): its timestamp is parsed with
:func:`datetime.fromisoformat` and re-emitted with :meth:`datetime.isoformat`, so
no raw marker text can reach an agent-visible response. A document that cannot be
parsed is reported as unreadable — never as a plausible default.

Dependency direction: ``state`` imports neither ``middleware``, ``tools`` nor
``bootstrap``. ``resolve_trw_dir`` is imported INSIDE the function body on
purpose — a module-level ``from ... import`` binds at import time and would turn
every ``trw_mcp.state._paths.resolve_trw_dir`` patch in the suite into a silent
no-op.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "PRE_COMPACT_MARKER_FILENAME",
    "MarkerReadResult",
    "PreCompactMarker",
    "pre_compact_marker_path",
    "read_pre_compact_marker",
    "read_pre_compact_marker_detail",
    "write_pre_compact_marker",
]

logger = structlog.get_logger(__name__)

#: The one place this filename is spelled in Python.
PRE_COMPACT_MARKER_FILENAME = "pre_compact_state.json"

#: Why a marker could not be read. Operator diagnostics only — deliberately NOT
#: part of any response contract (PRD-CORE-258-NFR02).
MISSING_FILE_RACE = "missing_file_race"
INVALID_JSON = "invalid_json"
MISSING_TIMESTAMP = "missing_timestamp"
NON_ISO_TIMESTAMP = "non_iso_timestamp"


def pre_compact_marker_path(trw_dir: Path | None = None) -> Path:
    """Return the marker path under *trw_dir*, defaulting to the resolved ``.trw``."""

    if trw_dir is None:
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
    return trw_dir / "context" / PRE_COMPACT_MARKER_FILENAME


class PreCompactMarker(BaseModel):
    """The subset of the pre-compaction document any consumer may rely on.

    ``extra="ignore"``: the writers emit a dozen more keys (run path, phase,
    events, pending ceremony) and must stay free to evolve without breaking
    every reader. ``owner_pid`` is recorded for operator diagnostics and is
    never consulted by the arming decision — the PreCompact hook runs in a
    process unrelated to the server, so a pid comparison could never match on
    the path that matters most.
    """

    model_config = ConfigDict(extra="ignore")

    timestamp: str
    trigger: str = ""
    phase: str = ""
    directive: str = ""
    context_anchor: str = ""
    owner_pin_key: str = ""
    owner_pid: int = 0

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_an_instant(cls, value: str) -> str:
        """Reject anything that is not an ISO-8601 instant, and normalize it.

        Normalizing here is the security property, not a nicety: the value a
        consumer reads is the re-serialization of a parsed instant, so ANSI
        escapes, newlines and injection markers in the raw field cannot survive
        into agent-visible text (NFR03).
        """
        return datetime.fromisoformat(value).isoformat()

    @property
    def instant(self) -> datetime:
        """The parsed compaction instant. Always available: validation enforces it."""
        return datetime.fromisoformat(self.timestamp)


@dataclass(frozen=True, slots=True)
class MarkerReadResult:
    """A marker read that reports WHY it failed rather than only that it did."""

    marker: PreCompactMarker | None = None
    unreadable_reason: str | None = None


def _classify(raw: object) -> MarkerReadResult:
    """Turn a decoded JSON document into a marker or a named failure reason."""

    if not isinstance(raw, dict):
        return MarkerReadResult(unreadable_reason=INVALID_JSON)
    stamp = raw.get("timestamp")
    if not isinstance(stamp, str) or not stamp.strip():
        # The bundled PreCompact hook spells it ``ts``.
        stamp = raw.get("ts")
    if not isinstance(stamp, str) or not stamp.strip():
        return MarkerReadResult(unreadable_reason=MISSING_TIMESTAMP)
    try:
        instant = datetime.fromisoformat(stamp)
    except ValueError:
        return MarkerReadResult(unreadable_reason=NON_ISO_TIMESTAMP)
    payload = dict(raw)
    payload["timestamp"] = instant.isoformat()
    try:
        return MarkerReadResult(marker=PreCompactMarker.model_validate(payload))
    except ValueError:
        # A modelled field carrying an unusable type: the document exists and
        # decodes, but it is not a marker.
        return MarkerReadResult(unreadable_reason=INVALID_JSON)


def read_pre_compact_marker_detail(trw_dir: Path | None = None) -> MarkerReadResult:
    """Read the marker, naming the parse step that failed when one did."""

    path = pre_compact_marker_path(trw_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return MarkerReadResult(unreadable_reason=MISSING_FILE_RACE)
    except OSError:
        logger.debug("pre_compact_marker_read_failed", marker_path=str(path), exc_info=True)
        return MarkerReadResult(unreadable_reason=INVALID_JSON)
    try:
        raw = json.loads(text)
    except ValueError:
        return MarkerReadResult(unreadable_reason=INVALID_JSON)
    return _classify(raw)


def read_pre_compact_marker(trw_dir: Path | None = None) -> PreCompactMarker | None:
    """Return the typed marker, or ``None`` for absent/unreadable/non-ISO input."""

    return read_pre_compact_marker_detail(trw_dir).marker


def write_pre_compact_marker(state: Mapping[str, object], trw_dir: Path | None = None) -> None:
    """Write the marker document atomically (PRD-CORE-258-FR07).

    A single ``write_text`` truncates in place, so a reader arriving mid-write
    saw a file that EXISTS — which is what arms the post-compaction gate — and
    whose JSON did not parse, and every reader on that path degrades silently:
    the directive/context-anchor readback returns its empty pair, the shell
    hooks drop their jq extractions, and the gate payload would call a perfectly
    good marker unreadable. Writing a sibling temporary file in the same
    directory and promoting it with :func:`os.replace`, which is atomic within a
    filesystem, means a concurrent reader observes either the previous complete
    document or the new one — never a partial write.

    A raise between creation and promotion leaves the previous complete marker
    in place and no temporary sibling behind.

    The bundled PreCompact shell hook has the same race on both of its write
    branches and is deliberately NOT changed here: it is outside this PRD's
    declared file ownership and is recorded as RISK-009 with the remedy named.
    """

    path = pre_compact_marker_path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(dict(state), indent=2))
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)
