"""PRD-CORE-249-FR02 — the marker-bounded project handoff row store.

One declaration, one write, one read. This module owns the WRITE half and the
file format; :mod:`trw_mcp.tools._project_handoff_readback` owns the
``trw_session_start`` read.

**Why a marker-bounded merge and not a rewrite.** The handoff file is
human-editable by design: the machine owns the block's format, the human owns
the decision, and deleting a row IS resolving the item. Markers are matched
WHOLE-LINE only, the same discipline as ``index_sync._find_marker_line`` — a
substring match on an inline prose mention of a marker once truncated 705 lines
of a checked-in planning document (2026-06-11). Every byte outside the marker
span is preserved untouched; a file with no markers gets the block appended; a
file that does not exist is created with a one-line ownership header.

**Row identity.** A row is keyed on ``(run_id, gate_id)``. Re-writing a key
overwrites the row in place and PRESERVES its original ``first_seen``, so
repeated delivers of one run never append and never reset the age clock.
Declaring a previously-blocked gate ``satisfied`` on a later deliver removes its
row — the only removal path, so the machine never deletes a row it did not add.
Age is not stored; it is derived at read time, so a row cannot carry a stale age.

**Concurrency (NFR04).** The read-merge-replace runs under an exclusive advisory
lock on a sibling lock file, and the visible replacement is the single atomic
``mkstemp`` + ``fsync`` + ``os.replace`` of ``FileStateWriter.write_text``. Two
concurrent deliveries against one checkout therefore produce the row UNION, and
no reader observes a partial file. Where ``flock`` is a documented no-op the
guarantee degrades to last-writer-wins on the merge while atomicity of the
replace is retained (RISK-003).
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Final

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from trw_mcp._locking import _lock_ex_nb, _lock_un
from trw_mcp.models.plan_acceptance import (
    MAX_GATE_ID_CHARS,
    MAX_OWNER_CHARS,
    MAX_REASON_CHARS,
    AcceptanceStatus,
)

logger = structlog.get_logger(__name__)

HANDOFF_START_MARKER: Final[str] = "<!-- trw:handoff:start -->"
HANDOFF_END_MARKER: Final[str] = "<!-- trw:handoff:end -->"

#: One-line ownership header written inside the block so a human opening the
#: file knows who owns the rows and how to resolve one (RISK-006).
HANDOFF_OWNERSHIP_HEADER: Final[str] = (
    "<!-- Managed by TRW (PRD-CORE-249). Rows below are machine-written at deliver; "
    "delete a row to record it as resolved. Text outside the markers is yours. -->"
)

#: Header written once when the file is created (NFR05).
HANDOFF_FILE_HEADER: Final[str] = "# Open handoff\n\nWork this project's runs deferred, with the owner who holds it.\n"

_TABLE_HEADER: Final[tuple[str, str]] = (
    "| First seen | Gate | Class | Owner | Run | Reason |",
    "| --- | --- | --- | --- | --- | --- |",
)

#: Bounded lock acquisition (NFR04). On timeout the write is skipped fail-open
#: with a recorded reason rather than blocking delivery.
HANDOFF_LOCK_TIMEOUT_SECONDS: Final[float] = 5.0
_LOCK_POLL_SECONDS: Final[float] = 0.02

#: A row line: six pipe-delimited cells whose first cell is an ISO date.
_ROW_RE: Final[re.Pattern[str]] = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|")


class HandoffRow(BaseModel):
    """One durable deferral row. Field bounds are the NFR03 invariants."""

    model_config = ConfigDict(frozen=True)

    first_seen: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    gate_id: str = Field(max_length=MAX_GATE_ID_CHARS)
    blocking_class: str = Field(max_length=32)
    owner: str = Field(default="", max_length=MAX_OWNER_CHARS)
    run_id: str = Field(default="", max_length=MAX_GATE_ID_CHARS)
    reason: str = Field(default="", max_length=MAX_REASON_CHARS)

    @field_validator("first_seen")
    @classmethod
    def _real_calendar_date(cls, value: str) -> str:
        """Reject a well-SHAPED but impossible date such as ``2026-02-30``.

        The pattern alone admits it, and the readback then raises inside
        :meth:`age_days` -- which takes the whole ``open_handoff`` key with it and
        makes the field ABSENT rather than ``not_measured``, the one outcome FR03
        forbids. Validating here turns it into the ``ValueError`` that
        :func:`parse_rows` already treats as "a row a human broke".
        """
        date.fromisoformat(value)
        return value

    @property
    def key(self) -> tuple[str, str]:
        """``(run_id, gate_id)`` — the row identity FR02 keys on."""
        return (self.run_id, self.gate_id)

    def age_days(self, today: date) -> int:
        """Whole UTC calendar days since ``first_seen`` (0 for a row written today)."""
        return (today - date.fromisoformat(self.first_seen)).days


#: Characters a cell may not carry literally, and their percent escapes. Every
#: one is an escape route OUT of the cell or out of the block:
#:   ``%``       -- the escape introducer itself, so the mapping stays injective;
#:   ``|``       -- would split one cell into two and shift every later column;
#:   ``<`` ``>`` -- the only characters in the managed markers, so no declaration
#:                  text can reproduce one and terminate the block early (NFR03);
#:   newlines    -- would end the row line and spill the rest into the block.
#: ``%`` MUST be first on encode and last on decode.
_CELL_ESCAPES: Final[tuple[tuple[str, str], ...]] = (
    ("%", "%25"),
    ("|", "%7C"),
    ("<", "%3C"),
    (">", "%3E"),
    ("\r", "%0D"),
    ("\n", "%0A"),
)


def neutralise(text: str) -> str:
    """Encode one field so it is safe to render as a single table cell.

    This is one half of a REVERSIBLE codec: ``decode_cell(neutralise(x))`` is
    ``x.strip()`` for any ``x``. The previous escaping was one-way -- it wrote
    ``\\|`` while :func:`parse_rows` split on a bare ``|`` -- so a single pipe in
    an owner or reason produced a seven-cell row the parser skipped, and the NEXT
    write, rebuilding the block from only what it could parse, silently deleted
    that row and reset the age clock of any identifier declared again. Percent
    escapes are used precisely because they survive the split and reverse exactly.
    """
    cleaned = text.strip()
    for literal, escape in _CELL_ESCAPES:
        cleaned = cleaned.replace(literal, escape)
    return cleaned


def decode_cell(text: str) -> str:
    """Reverse :func:`neutralise`. Escapes are undone in the opposite order."""
    decoded = text
    for literal, escape in reversed(_CELL_ESCAPES):
        decoded = decoded.replace(escape, literal)
    return decoded


def row_from_status(status: AcceptanceStatus, run_id: str, first_seen: str) -> HandoffRow:
    """Build a row from an accepted-blocked declaration."""
    return HandoffRow(
        first_seen=first_seen,
        gate_id=status.gate_id,
        blocking_class=status.blocking_class or "unknown",
        owner=status.owner,
        run_id=run_id[:MAX_GATE_ID_CHARS],
        reason=status.reason,
    )


def render_row(row: HandoffRow) -> str:
    """One table line. Every free-text cell is neutralised before it is emitted."""
    cells = (
        row.first_seen,
        neutralise(row.gate_id),
        neutralise(row.blocking_class),
        neutralise(row.owner) or "-",
        neutralise(row.run_id) or "-",
        neutralise(row.reason) or "-",
    )
    return "| " + " | ".join(cells) + " |"


def _row_from_cells(cells: list[str]) -> HandoffRow:
    """Build a row from six already-split cells, decoding every free-text one."""
    return HandoffRow(
        first_seen=cells[0],
        gate_id=decode_cell(cells[1]),
        blocking_class=decode_cell(cells[2]),
        owner="" if cells[3] == "-" else decode_cell(cells[3]),
        run_id="" if cells[4] == "-" else decode_cell(cells[4]),
        reason="" if cells[5] == "-" else decode_cell(cells[5]),
    )


def parse_block(block: str) -> tuple[list[HandoffRow], list[str]]:
    """Split a managed block into typed rows and lines the parser could not read.

    Returns ``(rows, unreadable)``. Because :func:`neutralise` is reversible, a
    row this code WROTE is always readable, so an entry in ``unreadable`` is
    necessarily a human edit. Those lines are carried through the next write
    verbatim rather than dropped: silently deleting a line a human typed inside
    the block is the same class of harm as the truncation the whole-line marker
    rule exists to prevent.
    """
    rows: list[HandoffRow] = []
    unreadable: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not _ROW_RE.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        try:
            if len(cells) != 6:
                raise ValueError(f"expected 6 cells, got {len(cells)}")
            rows.append(_row_from_cells(cells))
        except ValueError as exc:  # justified: a hand-broken row is preserved, never fatal
            logger.warning("handoff_row_unreadable", reason=str(exc))
            unreadable.append(stripped)
    return rows, unreadable


def parse_rows(block: str) -> list[HandoffRow]:
    """The typed rows of a managed block body (see :func:`parse_block`)."""
    return parse_block(block)[0]


def render_block(rows: list[HandoffRow], unreadable: list[str] | None = None) -> str:
    """Render the full managed block, rows ordered oldest-first then by key.

    The ordering is total and derived only from row content, which is what makes
    a repeated write byte-identical (FR02 acceptance). ``unreadable`` carries the
    hand-broken row lines :func:`parse_block` could not type; they are re-emitted
    in their original order after the typed rows so a human edit inside the block
    survives the next write instead of vanishing.
    """
    ordered = sorted(rows, key=lambda r: (r.first_seen, r.run_id, r.gate_id))
    lines = [HANDOFF_START_MARKER, HANDOFF_OWNERSHIP_HEADER, ""]
    if ordered or unreadable:
        lines.extend(_TABLE_HEADER)
        lines.extend(render_row(row) for row in ordered)
        lines.extend(unreadable or [])
    else:
        lines.append("_No open handoff rows._")
    lines.extend(["", HANDOFF_END_MARKER])
    return "\n".join(lines)


def find_marker_span(content: str, start_marker: str, end_marker: str) -> tuple[int, int] | None:
    """Whole-line span between two markers, or ``None``.

    Whole-line only: prose that *mentions* a marker inline (in backticks, say)
    must not delimit the section. This is the ``index_sync._find_marker_line``
    contract, restated here because this module must not import a PRD-catalogue
    helper to write a handoff file. Shared with the FR05 remaining-work section,
    so both managed writes obey one marker rule rather than two.
    """
    start = re.search(rf"^[ \t]*{re.escape(start_marker)}[ \t]*$", content, flags=re.MULTILINE)
    end = re.search(rf"^[ \t]*{re.escape(end_marker)}[ \t]*$", content, flags=re.MULTILINE)
    if start is None or end is None or end.end() <= start.start():
        return None
    return start.start(), end.end()


def merge_marked_section(content: str, section: str, start_marker: str, end_marker: str) -> str:
    """Replace the managed span in ``content``, or append the section at the end.

    Every byte outside the span is preserved exactly (NFR05).
    """
    span = find_marker_span(content, start_marker, end_marker)
    if span is None:
        prefix = content.rstrip("\n")
        return (prefix + "\n\n" if prefix else "") + section + "\n"
    return content[: span[0]] + section + content[span[1] :]


def find_block_span(content: str) -> tuple[int, int] | None:
    """Whole-line span of the handoff managed block, or ``None``."""
    return find_marker_span(content, HANDOFF_START_MARKER, HANDOFF_END_MARKER)


def merge_block(content: str, new_block: str) -> str:
    """Replace (or append) the handoff managed block, preserving every other byte."""
    return merge_marked_section(content, new_block, HANDOFF_START_MARKER, HANDOFF_END_MARKER)


def resolve_handoff_path() -> Path:
    """Resolve the configured handoff target, re-checking containment (NFR03).

    The lexical validator on ``project_handoff_path`` refuses an absolute or
    ``..``-escaping value at config load; this re-check runs AFTER symlink
    resolution, which is the only place a symlinked directory can be caught.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root

    root = resolve_project_root().resolve()
    target = (root / get_config().project_handoff_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"project_handoff_path resolves outside the project root: {target}")
    return target


class _ExclusiveFileLock:
    """Bounded exclusive advisory lock on a sibling ``.lock`` file."""

    def __init__(self, target: Path, timeout: float = HANDOFF_LOCK_TIMEOUT_SECONDS) -> None:
        self._path = target.with_name(target.name + ".lock")
        self._timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> _ExclusiveFileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                _lock_ex_nb(fd)
                self._fd = fd
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError(f"handoff lock not acquired within {self._timeout}s: {self._path}") from None
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            _lock_un(self._fd)
            os.close(self._fd)
            self._fd = None


def write_handoff_rows(
    *,
    run_id: str,
    accepted: list[AcceptanceStatus],
    resolved_gate_ids: list[str],
    today: date | None = None,
) -> dict[str, object]:
    """Merge this run's accepted-blocked rows into the handoff file.

    Args:
        run_id: The delivering run's id — half of every row key.
        accepted: Declarations accepted as blocked (``human-only``/``ops-only``).
        resolved_gate_ids: Identifiers this run declared ``satisfied``. Their
            rows for THIS run are removed; this is the only removal path.
        today: Injectable UTC date for the ``first_seen`` of a new row.

    Returns:
        A status dict recorded under ``project_handoff`` on the deliver result.
        Never raises — the caller is fail-open (NFR02).
    """
    stamp = (today or datetime.now(timezone.utc).date()).isoformat()
    try:
        target = resolve_handoff_path()
        with _ExclusiveFileLock(target):
            existing = target.read_text(encoding="utf-8") if target.is_file() else HANDOFF_FILE_HEADER
            span = find_block_span(existing)
            current, unreadable = parse_block(existing[span[0] : span[1]]) if span else ([], [])
            merged = _merge_rows(current, run_id, accepted, resolved_gate_ids, stamp)
            content = merge_block(existing, render_block(merged, unreadable))
            _atomic_replace(target, content)
        logger.info("project_handoff_written", run_id=run_id, rows=len(merged), path=str(target))
        return {"status": "written", "path": str(target), "rows": len(merged), "added": len(accepted)}
    except Exception as exc:  # justified: fail-OPEN — a handoff write must never block delivery
        logger.warning("project_handoff_write_failed", run_id=run_id, error=str(exc), exc_info=True)
        return {"status": "failed", "error": str(exc)}


def _merge_rows(
    current: list[HandoffRow],
    run_id: str,
    accepted: list[AcceptanceStatus],
    resolved_gate_ids: list[str],
    stamp: str,
) -> list[HandoffRow]:
    """Key-merge this run's rows into the existing set, preserving ``first_seen``."""
    by_key = {row.key: row for row in current}
    for gate_id in resolved_gate_ids:
        by_key.pop((run_id, gate_id), None)
    for status in accepted:
        key = (run_id, status.gate_id)
        first_seen = by_key[key].first_seen if key in by_key else stamp
        by_key[key] = row_from_status(status, run_id, first_seen)
    return list(by_key.values())


def _atomic_replace(target: Path, content: str) -> None:
    """Single visible replacement, so no reader ever observes a partial file."""
    from trw_mcp.state.persistence import FileStateWriter

    FileStateWriter().write_text(target, content)


__all__ = [
    "HANDOFF_END_MARKER",
    "HANDOFF_FILE_HEADER",
    "HANDOFF_LOCK_TIMEOUT_SECONDS",
    "HANDOFF_OWNERSHIP_HEADER",
    "HANDOFF_START_MARKER",
    "HandoffRow",
    "decode_cell",
    "find_block_span",
    "find_marker_span",
    "merge_block",
    "merge_marked_section",
    "neutralise",
    "parse_block",
    "parse_rows",
    "render_block",
    "render_row",
    "resolve_handoff_path",
    "row_from_status",
    "write_handoff_rows",
]
