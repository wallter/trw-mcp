"""Pre-existing findings: an exact-match ratchet, not a waiver list (OQ-02, §8 phase 1).

The detector ships enforcing on day one. Seven specimens already exist, and
blocking every unrelated commit on seven pre-existing product decisions would
get the gate deleted in week two — so they are acknowledged here. The mechanism
is deliberately not the ``seams:`` pattern, where a declaration nobody fills in
silently disables a check. Four properties make that impossible:

1. **Exact match, both directions.** A finding not listed here fails the run.
   A key listed here that no longer fires ALSO fails the run, as stale. The list
   can only shrink, and it shrinks under review rather than by decay.
2. **Omission is fail-closed.** Not listing something does not suppress it; the
   default for anything undeclared is "reported and blocking".
3. **Every entry carries a defect-ledger ID.** ``DEFECT-LEDGER.md`` requires a
   disposition (``WIRE`` / ``DELETE`` / ``ACCEPT`` / ``OPEN``) per entry, so an
   acknowledged finding always has a named owner in a document with its own
   maintenance protocol. The 2026-03-29 audit failed because 22 findings had no
   disposition field at all.
4. **No ``--update-baseline`` flag exists, on purpose.** Acknowledging a new
   finding is a source edit that shows up in a diff. A regenerate command turns
   a ratchet into a rubber stamp.

Timeboxing is behavioural rather than calendar-based: stale-entry detection
means the list is re-litigated on the run after any specimen is fixed, and
NFR03 forbids clock-dependent behaviour (a date-triggered failure would break
determinism and land on whoever happened to run ``make check`` that morning).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trw_mcp.wiring.model import Finding, RegistryError

_LEDGER_ID_RE = re.compile(r"^(UF-\d+|OQ-\d+|DEAD-\d+|PRD-[A-Z]+-\d+)$")


@dataclass(frozen=True)
class BaselineEntry:
    """One acknowledged pre-existing finding."""

    key: str
    ledger_id: str
    rationale: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise RegistryError("BaselineEntry.key must be non-empty")
        if not _LEDGER_ID_RE.match(self.ledger_id):
            raise RegistryError(
                f"BaselineEntry.ledger_id must reference a DEFECT-LEDGER.md entry "
                f"(UF-nnn / OQ-nn / DEAD-nnn); got {self.ledger_id!r} for {self.key!r}. "
                "An acknowledged finding without a tracked disposition is a waiver."
            )
        if len(self.rationale.strip()) < 10:
            raise RegistryError(f"BaselineEntry.rationale must explain why {self.key!r} is not blocking today")


@dataclass(frozen=True)
class BaselinePartition:
    """Result of comparing this run's findings against the baseline."""

    blocking: tuple[Finding, ...]
    acknowledged: tuple[Finding, ...]
    stale_keys: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not self.blocking and not self.stale_keys


# ---------------------------------------------------------------------------
# The acknowledged set. Every entry is a specimen named in
# docs/research/framework-simplification/DEFECT-LEDGER.md with an open
# disposition. Removing one is the goal; adding one requires a ledger entry.
# ---------------------------------------------------------------------------

BASELINE: tuple[BaselineEntry, ...] = (
    BaselineEntry(
        key="INERT_BRANCH::callsite:inert-required-inputs:trw-mcp/src/trw_mcp/tools/code_search.py:rank_semantic_chunks",
        ledger_id="UF-031",
        rationale="trw_code_search(mode='semantic') cannot return a result; implement-or-remove-the-mode is a public tool-surface decision",
    ),
    BaselineEntry(
        key="PREDICATE_COVERAGE::gate:prd-core-190-wiring",
        ledger_id="PRD-CORE-231",
        rationale="the wiring gate's activation covers 0.33% of the PRD corpus; widening it is owned by CORE-231 R4 and this detector must not modify it",
    ),
)


def partition(findings: list[Finding], baseline: tuple[BaselineEntry, ...] = BASELINE) -> BaselinePartition:
    """Split ``findings`` into blocking vs acknowledged, and detect stale entries."""
    known = {entry.key for entry in baseline}
    observed = {finding.key for finding in findings}
    blocking = tuple(finding for finding in findings if finding.key not in known)
    acknowledged = tuple(finding for finding in findings if finding.key in known)
    stale = tuple(sorted(key for key in known if key not in observed))
    return BaselinePartition(blocking=blocking, acknowledged=acknowledged, stale_keys=stale)


def entry_for(key: str, baseline: tuple[BaselineEntry, ...] = BASELINE) -> BaselineEntry | None:
    """Return the baseline entry acknowledging ``key``, if any."""
    for entry in baseline:
        if entry.key == key:
            return entry
    return None
