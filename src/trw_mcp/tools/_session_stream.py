"""Parse ``.trw/context/session-events.jsonl`` for the deliver gate: tolerate ONLY a torn last line.

DELIVER-JOURNAL-TORN-TAIL-ONLY (swarm-e2e INC-106 sweep). The stream is append-only, so one torn line at the end is
an interrupted write and is skipped. A malformed line anywhere else is not a torn write: skipping it (as the three
readers in ``_delivery_event_checks`` used to) lets a damaged or hidden ``file_modified`` / ``change_evidence_unknown``
line LOWER the counts the deliver gate enforces. Such a stream is untrusted (``None``), the same rule
``state/_evidence_binding.py`` applies to a run journal (``journal_ok=False``); every caller fails closed on it.

Belongs to the ``_delivery_event_checks.py`` gate helpers (split out for the 350 effective-LOC gate).
"""

from __future__ import annotations

import json

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["session_stream_records"]


def session_stream_records(text: str, *, path: str = "") -> list[dict[str, object]] | None:
    """Every record (a JSON object per line), skipping a TORN final line; ``None`` when the stream is untrusted.

    Torn means the last line has no terminating newline: an interrupted append writes the newline last or not at
    all. Anything else malformed -- a bad line in the middle, a bad last line that DOES end in a newline, or a line
    that parses but is not an object (the writer only writes objects) -- makes the stream untrusted.
    """
    lines = text.splitlines()
    torn_candidate = len(lines) - 1 if lines and not text.endswith("\n") else -1
    records: list[dict[str, object]] = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            if index == torn_candidate:
                continue  # trw-fail-silent-allow: an unterminated LAST line is an interrupted append
            record = None
        if not isinstance(record, dict):
            logger.warning("session_stream_untrusted", outcome="fail_closed", line=index + 1, path=path)
            return None
        records.append(record)
    return records
