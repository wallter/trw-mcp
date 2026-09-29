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

import structlog

logger = structlog.get_logger(__name__)


__all__ = []
