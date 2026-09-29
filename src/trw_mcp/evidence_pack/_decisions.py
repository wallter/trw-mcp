"""Decisions section of the evidence pack (PRD-CORE-323 FR02, NFR02 event caps).

Checkpoints come from ``checkpoints.jsonl`` only. The checkpoint writer appends
there and also logs a ``checkpoint`` event (``tools/_orchestration_checkpoint.py``),
so the ``checkpoint`` event class is never copied from the event stream. Each
checkpoint row is copied without its ``state`` key, a full run.yaml snapshot the
writer attaches to every row. Other events come from the legacy ``events.jsonl``
(``_run_log``); the classes in :data:`DECISION_EVENT_CLASSES` are copied in source
order with file and line. Tool calls are never copied, only counted per tool;
learning linkage is always ``not_recorded`` plus the ``trw_learn`` call count, and
the memory store is never opened. For each scoped PRD, every level-2 section whose
heading contains "Decision" is copied with its line (``export_snapshot``).

Soundness scope: proves each listed entry is present at the cited file and line. It
does not prove that every decision taken was recorded, or that a checkpoint message
is accurate.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from trw_mcp.evidence_pack._redaction import EntryWriter, JsonValue
from trw_mcp.evidence_pack._run_log import MAX_EVENT_READ_LINES, RunLog, event_name, select_events
from trw_mcp.evidence_pack._wording import AS_OF_EXPORT_SNAPSHOT, AS_OF_RUN_RECORD

#: FR02: the event classes copied from events.jsonl, in source order.
DECISION_EVENT_CLASSES: frozenset[str] = frozenset({"decision", "phase_enter", "run_init", "delivery_gate_overridden"})
#: NFR02 caps, applied to the listing before any entry is sealed.
MAX_CHECKPOINTS = 2000
MAX_DECISION_EVENTS = 5000


def _checkpoints(checkpoints: RunLog, writer: EntryWriter) -> dict[str, JsonValue]:
    entries: list[JsonValue] = []
    for line, row in checkpoints.rows(MAX_CHECKPOINTS):
        source = {"path": checkpoints.source, "line": line}
        if row is None:
            entries.append(writer.seal(source=source, as_of=AS_OF_RUN_RECORD, fields={}, reason="line_unparsable"))
            continue
        fields = {key: value for key, value in row.items() if key != "state"}
        entries.append(writer.seal(source=source, as_of=AS_OF_RUN_RECORD, fields=fields))
    return {
        "log_present": checkpoints.present,
        "total": len(checkpoints.lines),
        "kept": len(entries),
        "entries": entries,
    }


def _tool_summary(events: RunLog, rows: list[tuple[int, dict[str, object] | None]], writer: EntryWriter) -> JsonValue:
    """Per-tool call counts, the trw_learn count and ``learning_linkage: not_recorded``."""
    from trw_mcp.models.run import TOOL_CALL_EVENTS

    source = {"path": events.source}
    if not events.present:
        return writer.seal(
            source=source,
            as_of=AS_OF_RUN_RECORD,
            fields={"learning_linkage": "not_recorded"},
            reason="no_legacy_event_log",
        )
    tools: Counter[str] = Counter()
    not_copied: Counter[str] = Counter()
    unparsable = 0
    for _line, row in rows:
        if row is None:
            unparsable += 1
            continue
        name = event_name(row)
        if name in TOOL_CALL_EVENTS:
            tool = row.get("tool_name", row.get("tool"))
            tools[tool if isinstance(tool, str) and tool else "<unnamed>"] += 1
        elif name not in DECISION_EVENT_CLASSES:
            not_copied[name or "<unnamed>"] += 1
    fields = {
        "tool_call_counts": dict(sorted(tools.items())),
        "trw_learn_calls": tools.get("trw_learn", 0),
        "learning_linkage": "not_recorded",
        "not_copied_event_counts": dict(sorted(not_copied.items())),
        "unparsable_lines": unparsable,
        "read_bound_reached": len(events.lines) > MAX_EVENT_READ_LINES,
    }
    return writer.seal(source=source, as_of=AS_OF_RUN_RECORD, fields=fields)


def prd_decision_sections(path: Path, project_root: Path, writer: EntryWriter) -> list[JsonValue]:
    """Every level-2 section of one PRD whose heading contains "Decision", with its line.

    Runtime caller: :func:`decisions_section`. Fenced code blocks are skipped, so a
    ``## Decision`` inside a code sample is not a heading. An unreadable PRD yields one
    unknown entry; the requirements section reports the same file.
    """
    source_path = path.relative_to(project_root).as_posix()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:  # trw-fail-silent-allow: an unreadable PRD becomes an unknown entry naming the reason
        return [
            writer.seal(source={"path": source_path}, as_of=AS_OF_EXPORT_SNAPSHOT, fields={}, reason="prd_unreadable")
        ]
    found: list[JsonValue] = []
    start: int | None = None
    fenced = False
    for index, line in enumerate([*lines, "## <end>"]):
        at_end = index == len(lines)
        if line.lstrip().startswith("```") and not at_end:
            fenced = not fenced
        if not at_end and (fenced or not line.startswith("## ")):
            continue
        if start is not None:
            found.append(
                writer.seal(
                    source={"path": source_path, "line": start + 1},
                    as_of=AS_OF_EXPORT_SNAPSHOT,
                    fields={"heading": lines[start][3:].strip(), "text": "\n".join(lines[start + 1 : index]).strip()},
                )
            )
        start = index if not at_end and "Decision" in line else None
    return found


def decisions_section(
    events: RunLog,
    event_rows: list[tuple[int, dict[str, object] | None]],
    checkpoints: RunLog,
    prd_paths: list[Path],
    project_root: Path,
    writer: EntryWriter,
) -> dict[str, JsonValue]:
    """Checkpoints, decision-class events, per-tool counts and PRD Decision sections.

    Runtime caller: ``_pack.build_pack`` (``trw-mcp run evidence-pack`` -> ``run_run`` ->
    ``build_pack`` -> here). *event_rows* is ``events.rows()``, parsed once per export
    and shared with the verdict section. Caps: :data:`MAX_CHECKPOINTS` checkpoint lines
    and :data:`MAX_DECISION_EVENTS` decision-class events; each block records kept and
    total counts.
    """
    if events.present:
        kept, total = select_events(event_rows, DECISION_EVENT_CLASSES, MAX_DECISION_EVENTS)
        event_entries: list[JsonValue] = [
            writer.seal(source={"path": events.source, "line": line}, as_of=AS_OF_RUN_RECORD, fields=row)
            for line, row in kept
        ]
        # NFR02: the read itself is bounded (MAX_EVENT_READ_LINES); a file with more raw
        # lines than that is never silently under-counted without saying so.
        event_block: dict[str, JsonValue] = {
            "total": total,
            "kept": len(event_entries),
            "entries": event_entries,
            "read_bound_reached": len(events.lines) > MAX_EVENT_READ_LINES,
        }
    else:
        missing = writer.seal(
            source={"path": events.source}, as_of=AS_OF_RUN_RECORD, fields={}, reason="no_legacy_event_log"
        )
        event_block = {"total": 0, "kept": 0, "entries": [missing]}
    prd_entries: list[JsonValue] = []
    for path in prd_paths:
        prd_entries.extend(prd_decision_sections(path, project_root, writer))
    return {
        "section": "decisions",
        "checkpoints": _checkpoints(checkpoints, writer),
        "events": event_block,
        "tool_calls": _tool_summary(events, event_rows, writer),
        "prd_decisions": prd_entries,
    }
