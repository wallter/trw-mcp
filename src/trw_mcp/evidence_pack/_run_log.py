"""The run's two JSONL logs, read once per export (PRD-CORE-323 FR02, FR04, NFR02).

Single event source. Events come from the run's legacy ``meta/events.jsonl`` only.
``FileEventLogger`` also mirrors every row into a dated ``events-YYYY-MM-DD.jsonl``
sibling (``state/persistence.py``), so reading both would count each event twice;
the dated file is never opened, and an absent ``events.jsonl`` has no fallback.
Checkpoints come from ``meta/checkpoints.jsonl`` only.

Each line keeps its 1-based line number, so every entry can cite ``path`` and
``line``. A line that is not a JSON object is kept as ``None``: the caller reports
it, never drops it silently.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: NFR02: a single line longer than this is never handed to ``json.loads``; a run log is
#: line-delimited so one adversarially huge line must not force unbounded parse work
#: regardless of any event-count cap. It is treated as unparsable, like malformed JSON.
MAX_LINE_CHARS = 262_144

#: NFR02: the hard ceiling on how many events.jsonl lines are ever parsed for one export,
#: applied at the ``_pack.build_pack`` call site. Set well above the largest per-class cap
#: (``_decisions.MAX_DECISION_EVENTS`` = 5,000) so every existing per-class cap is reached
#: from a fully-read file; a run directory built to hold far more lines than any real caps
#: need still bounds the read (PRD-CORE-323 NFR02: "the per-section caps bound what is read").
MAX_EVENT_READ_LINES = 30_000


@dataclass(frozen=True)
class RunLog:
    """One JSONL log: whether it exists, its project-relative source, and its lines in source order."""

    present: bool
    source: str
    lines: tuple[tuple[int, str], ...]

    def rows(self, limit: int | None = None) -> list[tuple[int, dict[str, object] | None]]:
        """Parse the first *limit* lines (all when ``None``) into ``(line_number, row or None)``.

        Runtime callers: ``_decisions.decisions_section`` (checkpoints, capped before
        parsing) and ``_pack.build_pack`` (events, capped to :data:`MAX_EVENT_READ_LINES`
        and shared). Soundness scope: a row is ``None`` when the line is not a JSON object
        *or* longer than :data:`MAX_LINE_CHARS`; nothing else is judged here.
        """
        selected = self.lines if limit is None else self.lines[:limit]
        parsed: list[tuple[int, dict[str, object] | None]] = []
        for index, line in selected:
            if len(line) > MAX_LINE_CHARS:  # trw-fail-silent-allow: an oversized line is unparsable, never json.loads'd
                parsed.append((index, None))
                continue
            try:
                row = json.loads(line)
            except (
                ValueError
            ):  # trw-fail-silent-allow: an unparsable line is returned as None and reported by the caller
                row = None
            parsed.append((index, row if isinstance(row, dict) else None))
        return parsed


def read_run_log(run: Path, run_source: str, name: str) -> RunLog:
    """Read ``meta/<name>`` of the run, splitting lines without parsing them.

    Runtime caller: ``_pack.build_pack`` for ``events.jsonl`` and ``checkpoints.jsonl``.
    A missing or unreadable file is ``present=False``. Blank lines are skipped but keep
    their place in the numbering, so every cited line is the file's own line number.
    """
    path = run / "meta" / name
    source = f"{run_source}/meta/{name}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # trw-fail-silent-allow: an absent log is reported by its section as a named reason
        return RunLog(present=False, source=source, lines=())
    numbered = tuple((number, line) for number, line in enumerate(text.splitlines(), start=1) if line.strip())
    return RunLog(present=True, source=source, lines=numbered)


def event_name(row: dict[str, object]) -> str:
    """The event class of a legacy events.jsonl row (``event``; ``event_type`` in older rows)."""
    name = row.get("event", row.get("event_type", ""))
    return name if isinstance(name, str) else ""


def row_utc_ms(row: dict[str, object]) -> int | None:
    """The row's recorded ``ts`` in epoch milliseconds, or None when absent or not ISO-8601.

    Runtime caller: ``_verdict.verdict_section`` (the run start used to scope journal
    tombstones). Reads the recorded value only, never the clock.
    """
    stamp = row.get("ts")
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:  # trw-fail-silent-allow: an unparsable ts means the run start is unknown, handled conservatively
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1000)


def select_events(
    rows: list[tuple[int, dict[str, object] | None]], classes: Collection[str], limit: int
) -> tuple[list[tuple[int, dict[str, object]]], int]:
    """The first *limit* rows whose event class is in *classes*, in source order, and how many there are.

    Runtime callers: ``_decisions.decisions_section`` (decision-class events) and
    ``_verdict.verdict_section`` (deliver events). The cap bounds what is sealed;
    the total is returned so the section records kept and total counts (NFR02).
    """
    matching = [(line, row) for line, row in rows if row is not None and event_name(row) in classes]
    return matching[:limit], len(matching)
