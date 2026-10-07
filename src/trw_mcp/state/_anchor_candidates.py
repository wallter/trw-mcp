"""Detection-only report of rows whose text or anchors look damaged (feedback #167).

Two legacy defects left rows no automatic repair can fix:

* A retired write-path step replaced every path component in a learning's text
  with a one-way 8-hex digest (``a1b2c3d4/e5f6a7b8/9c0d1e2f.py``). The original
  text is gone; the row can only be flagged for the operator to review or
  restore from a backup.
* An older anchor generator attached one diff's anchors to many unrelated
  learnings, so a single code anchor can be shared by dozens of rows that never
  mention it.

This module only READS and reports. It never clears or rewrites an anchor: an
empty anchor list scores a perfect ``anchor_validity`` of 1.0, which is why
:mod:`trw_mcp.state._anchor_repair` deliberately avoids recomputing it, so a
bulk clear would turn suspect rows into rows that look perfectly validated.
The report is advisory; the operator decides.

The report is written to ``.trw/context/anchor_candidates.json`` after EVERY scan, including a clean one, so a
stale list never outlives the damage it named (row ids and anchor files only, never row text). The scan is
opt-in (``memory repair-anchors --report-candidates``) and bounded: one fetch of at most ``SCAN_MAX_ROWS``
newest rows, aggregated as it streams, with the deadline started BEFORE the fetch and checked after every
row (the fetch itself is not time-bounded; only the analysis is); a report that hit the row or time budget says ``complete: false``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry


logger = structlog.get_logger(__name__)


#: Rows sharing one anchor file before it is a candidate; a legitimately hot file stays well below this.
SHARED_ANCHOR_MIN_ROWS = 20
#: Newest rows examined in one scan (the store API pages by limit only, so this is ONE fetch, never a growing prefix).
SCAN_MAX_ROWS = 2000
#: Wall-clock budget for ANALYSING the fetched rows. The fetch itself is not time-bounded (the store API pages by
#: limit only), which is why the fetch is capped at SCAN_MAX_ROWS; exceeding this sets ``complete: false``.
SCAN_SECONDS = 20.0
#: Ids kept per list in the report; counts are exact for the rows scanned.
SAMPLE_IDS = 200
#: A shared anchor is only suspect when fewer than this share of its rows even mention it.
_RELATED_SHARE = 0.5
#: A stem or symbol shorter than this is too ambiguous to count as a mention on its own.
_MIN_NAME = 4
_REPORT = Path("context") / "anchor_candidates.json"

# 8 lowercase hex characters with at least one digit (a bare word such as ``deadbeef`` is not a digest).
_HEX8 = r"(?=[0-9a-f]*\d)[0-9a-f]{8}"
# Two or more digest segments joined by '/', or a digest segment followed by a digest-named file with an extension.
_DAMAGED = re.compile(rf"(?<![0-9A-Za-z]){_HEX8}(?:/{_HEX8}(?![0-9A-Za-z])(?:\.\w+)?)+(?![0-9A-Za-z])")


def looks_hash_damaged(text: str) -> bool:
    """True when *text* holds a path whose components were replaced by 8-hex digests."""
    return _DAMAGED.search(text) is not None


@dataclass(frozen=True)
class CandidateReport:
    """Row ids worth an operator's review; nothing here has been changed.

    ``hash_damaged`` and each ``shared_anchors`` list are samples (at most ``SAMPLE_IDS`` ids); the ``*_count``
    fields are exact for the ``scanned`` rows. ``complete`` is false when the row limit was reached or the analysis
    deadline passed. The fetch itself is not time-bounded: the row limit is its only bound.
    """

    hash_damaged: list[str] = field(default_factory=list)
    shared_anchors: dict[str, list[str]] = field(default_factory=dict)
    hash_damaged_count: int = 0
    shared_anchor_counts: dict[str, int] = field(default_factory=dict)
    scanned: int = 0
    complete: bool = True

    @property
    def found(self) -> bool:
        return bool(self.hash_damaged or self.shared_anchors)

    def as_json(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "note": "the fetch is not time-bounded; the deadline applies to analysis and sets complete=false",
            "scanned": self.scanned,
            "hash_damaged_count": self.hash_damaged_count,
            "hash_damaged": self.hash_damaged,
            "shared_anchor_counts": self.shared_anchor_counts,
            "shared_anchors": self.shared_anchors,
        }


def _mentions_token(text: str, name: str, *, min_len: int = 0) -> bool:
    """*name* appears in *text* as a whole token (no letter or digit on either side); shorter names never count."""
    return (
        len(name) >= max(min_len, 1)
        and re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text) is not None
    )


def _mentions(text: str, file: str, symbols: set[str]) -> bool:
    """True when *text* names the anchor.

    The full path and the file name match on token boundaries at ANY length (``a.c`` is a real name); only a bare
    stem or a symbol, which are common words when short, need ``_MIN_NAME`` characters.
    """
    lowered = text.lower()
    path = Path(file)
    return (
        _mentions_token(lowered, file.lower())
        or _mentions_token(lowered, path.name.lower())
        or _mentions_token(lowered, path.stem.lower(), min_len=_MIN_NAME)
        or any(_mentions_token(lowered, symbol.lower(), min_len=_MIN_NAME) for symbol in symbols)
    )


class _Scan:
    """Streaming aggregates of one scan; keeps counts and capped id samples, never entries."""

    def __init__(self) -> None:
        self.scanned = 0
        self.damaged_count = 0
        self.damaged: list[str] = []
        self.files: dict[str, list[int]] = {}  # file -> [rows, related rows]
        self.unrelated: dict[str, list[str]] = {}

    def add(self, entry: MemoryEntry) -> None:
        self.scanned += 1
        text = f"{entry.content}\n{entry.detail or ''}"
        if looks_hash_damaged(text):
            self.damaged_count += 1
            if len(self.damaged) < SAMPLE_IDS:
                self.damaged.append(entry.id)
        symbols_by_file: dict[str, set[str]] = {}
        for anchor in entry.anchors:
            symbols_by_file.setdefault(anchor.file, set()).add(anchor.symbol_name)
        for file, symbols in symbols_by_file.items():
            counts = self.files.setdefault(file, [0, 0])
            counts[0] += 1
            if _mentions(text, file, symbols):
                counts[1] += 1
            else:
                sample = self.unrelated.setdefault(file, [])
                if len(sample) < SAMPLE_IDS:
                    sample.append(entry.id)

    def report(self, *, complete: bool) -> CandidateReport:
        shared = {
            file: sorted(self.unrelated.get(file, []))
            for file, (rows, related) in self.files.items()
            if rows >= SHARED_ANCHOR_MIN_ROWS and related / rows < _RELATED_SHARE
        }
        return CandidateReport(
            sorted(self.damaged),
            shared,
            self.damaged_count,
            {file: self.files[file][0] - self.files[file][1] for file in shared},
            self.scanned,
            complete,
        )


def build_report(rows: list[MemoryEntry], *, complete: bool = True, deadline: float | None = None) -> CandidateReport:
    """Classify *rows* (no store access); stops early, marking the report incomplete, once *deadline* passes."""
    scan = _Scan()
    if deadline is not None and time.monotonic() > deadline:
        return scan.report(complete=False)  # the fetch itself used the whole budget
    for entry in rows:
        scan.add(entry)
        if deadline is not None and time.monotonic() > deadline:
            return scan.report(complete=False)  # checked after the row, so a final-row overrun is still reported
    return scan.report(complete=complete)


def anchor_candidate_report(trw_dir: Path) -> CandidateReport:
    """Scan the project namespace, publish the advisory report file, and log a summary; never writes a row.

    The file is rewritten after every scan, so a clean scan replaces an earlier report with an empty one.
    """
    from trw_mcp._checkout_write import write_checkout_file
    from trw_mcp.state._store_selection import selected_store

    store, namespace = selected_store(trw_dir)
    deadline = time.monotonic() + SCAN_SECONDS
    rows = store.list_entries(namespace, limit=SCAN_MAX_ROWS)
    report = build_report(rows, complete=len(rows) < SCAN_MAX_ROWS, deadline=deadline)
    del rows
    write_checkout_file(trw_dir, trw_dir / _REPORT, json.dumps(report.as_json(), indent=1))
    if report.found or not report.complete:
        logger.warning(
            "anchor_candidates_found",
            hash_damaged=report.hash_damaged_count,
            shared_anchor_files=len(report.shared_anchors),
            complete=report.complete,
            report=str(trw_dir / _REPORT),
        )
    return report
