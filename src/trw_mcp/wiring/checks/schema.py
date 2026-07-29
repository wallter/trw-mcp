"""FR04 — writer/reader record comparison. ``SCHEMA_DIVERGENCE``.

**The comparison is a (writer location, record shape, reader location) triple,
not a field-name diff.** That refinement is the whole check. In the
stop-ceremony defect the field name agreed perfectly — the hook grepped
``session-events.jsonl`` for the event type ``trw_deliver_complete``, and
``trw_deliver_complete`` was a real, correctly-spelled event type. The
divergence was *which file the record landed in*: the writer only emitted it
into a pinned run's ``meta/events.jsonl``. Measured at the time: 149 rows in the
session log, 0 matches, 21 actual deliveries, and six consecutive false
reminders in one observed session.

So the check asks: **is there a writer that knows BOTH this record type AND
this artifact?** Scoped to a single function body, because a module that
mentions the artifact in one function and the record type in an unrelated one is
not a writer of that record into that artifact.

It is a source-level check on purpose. Reading the live ``session-events.jsonl``
would make the result depend on local runtime state, and a fresh clone with no
log would fire a false ``SCHEMA_DIVERGENCE`` on every contract (NFR03).
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.wiring._source import contains_text, function_string_constants, iter_python_files, parse_module
from trw_mcp.wiring.model import EdgeClass, Finding
from trw_mcp.wiring.registry import ArtifactContract

WRITER_SCAN_ROOT = "trw-mcp/src/trw_mcp"


def _find_writers(repo_root: Path, record: str, artifact: str, *, max_bytes: int) -> tuple[list[str], list[str]]:
    """Return (functions writing record INTO artifact, functions mentioning record elsewhere).

    The second list is the actionable half of the evidence: it names where the
    record type *is* produced, which is exactly the fact the stop-ceremony
    defect hid.
    """
    matched: list[str] = []
    record_only: list[str] = []
    for path in iter_python_files(repo_root / WRITER_SCAN_ROOT, max_bytes=max_bytes):
        if not contains_text(path, record):
            continue
        tree = parse_module(path)
        if tree is None:
            continue
        relative = path.relative_to(repo_root).as_posix()
        for function_name, literals in function_string_constants(tree).items():
            if record not in literals:
                continue
            location = f"{relative}::{function_name}"
            if any(artifact in literal for literal in literals):
                matched.append(location)
            else:
                record_only.append(location)
    return sorted(matched), sorted(record_only)


def check_schema(repo_root: Path, contract: ArtifactContract, *, max_bytes: int) -> list[Finding]:
    """Classify a reader whose expected record type is never written into the artifact it reads."""
    record = contract.artifact
    artifact = contract.artifact_container
    reader_relative = contract.detail_value("reader_file")
    reader_path = repo_root / reader_relative

    if not reader_path.is_file():
        return [
            Finding(
                contract_id=contract.contract_id,
                edge_class=EdgeClass.SCHEMA_DIVERGENCE,
                producer_side=contract.producer,
                consumer_side=f"{reader_relative} (MISSING)",
                evidence=f"registered reader {reader_relative} does not exist, so this registry entry is stale",
                remedy=f"update the registry entry '{contract.contract_id}' to name the current reader, or drop it",
            )
        ]

    # Registry-drift guard: the entry claims this reader looks for this record.
    # If it no longer does, the entry is stale and must fail rather than pass quietly.
    if not contains_text(reader_path, record):
        return [
            Finding(
                contract_id=contract.contract_id,
                edge_class=EdgeClass.SCHEMA_DIVERGENCE,
                producer_side=contract.producer,
                consumer_side=reader_relative,
                evidence=(
                    f"registry claims {reader_relative} reads record type {record!r}, but that string no "
                    "longer appears in the reader — the contract moved and the registry did not"
                ),
                remedy=f"re-derive the '{contract.contract_id}' registry entry from the reader's current predicate",
            )
        ]

    matched, record_only = _find_writers(repo_root, record, artifact, max_bytes=max_bytes)
    if matched:
        return []

    elsewhere = (
        f"the record type IS produced at: {', '.join(record_only)} — but none of those writes target {artifact}"
        if record_only
        else f"no function anywhere under {WRITER_SCAN_ROOT} emits {record!r} at all"
    )
    return [
        Finding(
            contract_id=contract.contract_id,
            edge_class=EdgeClass.SCHEMA_DIVERGENCE,
            producer_side=f"no writer emits {record!r} into {artifact}",
            consumer_side=f"{reader_relative} greps {artifact} for {record!r}",
            evidence=(
                f"writer/reader triple mismatch — reader location {reader_relative}, record shape {record!r}, "
                f"artifact {artifact}. {elsewhere}. The reader's predicate can therefore never match, and a "
                "predicate that never matches trains its audience to ignore the channel"
            ),
            remedy=(
                f"write {record!r} into {artifact} from the path the reader observes, or change the reader to "
                "match the shape the writer actually emits"
            ),
        )
    ]
