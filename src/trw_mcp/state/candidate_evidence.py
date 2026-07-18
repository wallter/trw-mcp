"""Candidate-commit evidence binding (PRD-CORE-219).

Relocated from ``tools/checkpoint.py`` (PRD-FIX-061-FR07): the state layer
(``state/git_commit_workflow.py``) consumes this, and state must never import
from ``tools/``. ``tools/checkpoint.py`` re-exports it for back-compat.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

logger = structlog.get_logger(__name__)


def record_candidate_evidence(run_dir: Path, journal: object) -> None:
    """Bind a published candidate's terminal evidence into the run (PRD-CORE-219).

    Appends a checkpoint record carrying the candidate ref, OID, reviewed
    parent, and manifest digest after terminal publication/handoff — the run's
    durable pointer to the immutable candidate. Deliberately contains NO
    transaction logic; the transaction state machine lives solely in
    ``state/git_commit_transaction.py`` per its authority contract.
    """
    from trw_mcp.models.git_commit_transaction import TransactionJournal

    if not isinstance(journal, TransactionJournal):
        raise TypeError("record_candidate_evidence requires a TransactionJournal")
    if not journal.candidate_oid or not journal.candidate_ref:
        raise ValueError("refusing to bind evidence for an unpublished candidate")

    writer = FileStateWriter()
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": f"candidate commit published: {journal.candidate_ref}",
        "candidate_evidence": {
            "transaction_id": journal.transaction_id,
            "candidate_ref": journal.candidate_ref,
            "candidate_oid": journal.candidate_oid,
            "reviewed_parent_oid": journal.parent_oid,
            "manifest_digest": journal.manifest_digest,
            "state": str(journal.state),
        },
    }
    writer.append_jsonl(checkpoints_path, record)

    events_path = run_dir / "meta" / "events.jsonl"
    if events_path.parent.exists():
        FileEventLogger(writer).log_event(
            events_path,
            "candidate_commit_published",
            {"candidate_ref": journal.candidate_ref, "candidate_oid": journal.candidate_oid},
        )
    logger.info(
        "candidate_evidence_bound",
        run_id=run_dir.name,
        ref=journal.candidate_ref,
        oid=journal.candidate_oid[:12],
    )
