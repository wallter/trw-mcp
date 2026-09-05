"""Per-outcome accounting for one ``merge_team_learnings`` batch.

Belongs to the ``pull.py`` facade; ``merge_team_learnings`` returns it.

**Why a result object.** The merge returned a single ``int`` — ``inserted +
merged`` — and logged ``outcome="success"`` unconditionally. Every other
disposition an item can reach (no ``source_learning_id``, a payload that would
not deserialise, a security-gate quarantine, a store error) was dropped on the
floor between the loop body and the caller. A batch of fifty items of which
forty-nine were refused therefore reported exactly what a batch of one clean
item reports: ``1``, ``outcome="success"``. There was no number anywhere in the
response that a caller could compare against what it sent.

``attempted`` is that missing number. ``status`` is derived, never passed in, so
the two can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TeamMergeResult"]


@dataclass(frozen=True, slots=True)
class TeamMergeResult:
    """The disposition of every item in one team-learning merge batch.

    Attributes:
        attempted: Items handed to the merge, including ones never applied.
        inserted: New local rows written.
        merged: Existing local rows updated (conflict-resolved).
        skipped_no_id: Items carrying no ``source_learning_id`` to key on.
        invalid: Items whose payload could not be deserialised.
        quarantined: Items the security gate diverted rather than stored.
        failed: Items that raised while being stored.
        unavailable: True when the merge could not run at all (missing optional
            dependencies, no ``trw_dir``). Distinct from an empty batch, which
            ran and had nothing to do.
    """

    attempted: int = 0
    inserted: int = 0
    merged: int = 0
    skipped_no_id: int = 0
    invalid: int = 0
    quarantined: int = 0
    failed: int = 0
    unavailable: bool = False

    @property
    def applied(self) -> int:
        """Items that reached local storage — the old return value."""
        return self.inserted + self.merged

    @property
    def rejected(self) -> int:
        """Items that were attempted and did not reach storage."""
        return self.skipped_no_id + self.invalid + self.quarantined + self.failed

    @property
    def status(self) -> str:
        """``unavailable``, ``partial`` when anything was rejected, else ``success``."""
        if self.unavailable:
            return "unavailable"
        return "partial" if self.rejected else "success"

    def as_log_fields(self) -> dict[str, object]:
        """The per-outcome counts as structlog kwargs (never nested)."""
        return {
            "attempted": self.attempted,
            "inserted": self.inserted,
            "merged": self.merged,
            "applied": self.applied,
            "skipped_no_id": self.skipped_no_id,
            "invalid": self.invalid,
            "quarantined": self.quarantined,
            "failed": self.failed,
            "rejected": self.rejected,
            "outcome": self.status,
        }
