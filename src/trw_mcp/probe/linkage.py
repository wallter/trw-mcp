"""Hypothesis linkage + Dissent Ledger write-back (PRD-CORE-144 FR-05/FR-06).

Belongs to the ``probe`` facade.

FR-05: when a plan branch declares an assumption ``{hypothesis_id, claim,
priority, polarity}``, the harness writes the probe verdict back to the
branch's assumption record ATOMICALLY (tempfile + rename — FR-05 A3).

FR-06: when the probe verdict contradicts the assumption polarity, a
DissentEntry is appended to the run-scoped Dissent Ledger (JSONL), linked
to the full ProbeResult by ``probe_evidence_ref`` (FR-06 A2). OQ-02 (ledger
home) defaults here to a run-scoped JSONL artifact; a later DAG-node store
(PRD-HPO-DAG-001) can supersede this writer without changing callers.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

import structlog

from trw_mcp.models.probe import (
    DissentEntry,
    ProbeAssumption,
    ProbeResult,
)
from trw_mcp.probe.verdict import detect_dissent

logger = structlog.get_logger(__name__)


def write_verdict_back(assumption: ProbeAssumption, result: ProbeResult, *, dest: Path) -> ProbeAssumption:
    """Atomically write the probe verdict ref into the assumption record (FR-05 A3).

    Returns the updated assumption (with ``probe_result_ref`` set). The write
    is tempfile + rename so a crash never leaves a torn record.
    """
    updated = assumption.model_copy(update={"probe_result_ref": result.run_id})
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(updated.model_dump_json())
        os.replace(tmp_name, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return updated


def record_dissent_if_contradicted(
    assumption: ProbeAssumption,
    result: ProbeResult,
    *,
    ledger_path: Path,
    probe_evidence_ref: str,
) -> DissentEntry | None:
    """Append a DissentEntry to the ledger when the probe contradicts the claim (FR-06).

    Returns the recorded entry, or ``None`` when there was no contradiction.
    Append is best-effort fail-open: a ledger write error logs and returns the
    entry without raising into plan adjudication.
    """
    entry = detect_dissent(assumption, result, probe_evidence_ref=probe_evidence_ref)
    if entry is None:
        return None
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")
    except OSError as exc:
        logger.warning(
            "dissent_ledger_write_failed",
            component="probe.linkage",
            op="record_dissent",
            outcome="degraded",
            error=str(exc),
            hypothesis_id=assumption.hypothesis_id,
        )
        return entry
    logger.info(
        "dissent_recorded",
        component="probe.linkage",
        op="record_dissent",
        outcome="recorded",
        hypothesis_id=assumption.hypothesis_id,
        probe_verdict=result.verdict,
    )
    return entry


def read_dissent_ledger(ledger_path: Path) -> list[DissentEntry]:
    """Read all DissentEntry rows from a JSONL ledger (empty when absent)."""
    if not ledger_path.exists():
        return []
    return [
        DissentEntry.model_validate(json.loads(line))
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


__all__ = [
    "read_dissent_ledger",
    "record_dissent_if_contradicted",
    "write_verdict_back",
]
