"""Recall admission: the one predicate a stored row must pass to reach a caller.

Belongs to the ``memory_adapter.py`` facade. Search, listing and the by-id
fetch all hand their candidate rows to :class:`RecallAdmission`, so a row that
search would never return (wrong status, another namespace, superseded or
expired, a system canary, blocked by the recall filter, from a tampered store)
cannot be fetched by id either (PRD-CORE-294 FR01).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from trw_memory.models.config import MemoryConfig
from trw_memory.models.memory import MemoryStatus
from trw_memory.retrieval.temporal_selection import TemporalSelection
from trw_memory.security.recall_filter import filter_recall_window

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry


logger = structlog.get_logger(__name__)

__all__ = ["RecallAdmission", "fetch_admitted"]


@dataclass(frozen=True)
class RecallAdmission:
    """One request's admission policy: a per-row filter."""

    sec_cfg: MemoryConfig
    selection: TemporalSelection
    mem_status: MemoryStatus | None

    @classmethod
    def build(
        cls, trw_dir: Path, *, status: str | None, as_of: datetime | None, include_superseded: bool
    ) -> RecallAdmission:
        mem_status: MemoryStatus | None = None
        if status is not None:
            try:
                mem_status = MemoryStatus(status)
            except ValueError:
                logger.debug("invalid_status_ignored", status=status)
        selection = TemporalSelection(as_of=as_of, include_superseded=include_superseded, exclude_system_canaries=True)
        return cls(MemoryConfig(storage_path=str(trw_dir / "memory")), selection, mem_status)

    def admit(self, entries: Sequence[MemoryEntry]) -> list[MemoryEntry]:
        """The rows of *entries* a caller may see, in order (superseded last when included)."""
        from trw_memory.retrieval.recall_selection import entry_policy_fields
        from trw_memory.retrieval.source_policy import SourcePolicy
        from trw_memory.retrieval.validity_prior import apply_validity_prior

        selection = self.selection
        rows = [
            entry
            for entry in entries
            if (self.mem_status is None or entry.status == self.mem_status)
            and (selection.include_superseded or selection.eligible(entry))
            and entry.metadata.get("system_canary") != "true"
        ]
        # PRD-CORE-194 FR03: superseded rows are dropped, or appended after every open one.
        rows = apply_validity_prior(
            rows,
            as_of=selection.as_of,
            include_superseded=selection.include_superseded,
            reference_time=selection.reference_time,
        )
        # PRD-CORE-292 FR03: MemoryClient.recall's exclude_expired default.
        policy = SourcePolicy.resolve(reference_time=selection.as_of or selection.reference_time)
        rows = [entry for entry in rows if policy.allows(entry_policy_fields(entry))]
        if not self.sec_cfg.enable_recall_filter:
            return rows
        return list(filter_recall_window(rows, mode=self.sec_cfg.recall_filter_mode).accepted)

    def representative(self, rows: Iterable[MemoryEntry]) -> MemoryEntry | None:
        """The row that represents one id in one store, on BOTH recall paths (PRD-CORE-294 FR01).

        *rows* are every row the store holds under the id, one per namespace. The
        newest admitted row wins, an exact ``updated_at`` tie going to the higher
        namespace: a twin is usually a row copied with its timestamp, so ties are
        common and neither path's read order may decide them. Search shows this row
        at the id's best rank and ``ids=`` hydrates to it, so a stub always hydrates
        to exactly the row it showed. ``None``: no row is admitted.

        Raises:
            NamespaceEnumerationError: the store could not list its namespaces and
                no admitted row was found before the failure.
        """
        from trw_mcp.exceptions import NamespaceEnumerationError

        candidates: list[MemoryEntry] = []
        try:
            for row in rows:
                candidates.extend(self.admit([row]))
        except NamespaceEnumerationError:
            if not candidates:
                raise
        return max(candidates, key=lambda entry: (entry.updated_at, entry.namespace), default=None)

    def order(self, rows: list[MemoryEntry]) -> list[MemoryEntry]:
        """*rows* admitted store by store, with any included superseded row after every open one."""
        if not self.selection.include_superseded:
            return rows
        from trw_memory.retrieval.validity_prior import apply_validity_prior

        selection = self.selection
        return apply_validity_prior(
            rows, as_of=selection.as_of, include_superseded=True, reference_time=selection.reference_time
        )


def fetch_admitted(trw_dir: Path, ids: Sequence[str], *, status: str | None = "active") -> list[MemoryEntry]:
    """The rows *ids* name that search could also return, through the checkout's store.

    An id and a present-but-refused row are indistinguishable to the caller, so a
    quarantined or foreign row does not even confirm it exists (PRD-CORE-280 FR01:
    ``MemoryStore.recall`` with ``ids``).

    Raises:
        CanaryTamperError: the project store's canary halted recalls.
    """
    from trw_mcp.state._store_selection import RecallSpec, selected_store

    admission = RecallAdmission.build(trw_dir, status=status, as_of=None, include_superseded=False)
    store, _ = selected_store(trw_dir)
    return store.recall(RecallSpec(admission=admission, ids=tuple(dict.fromkeys(ids))))
