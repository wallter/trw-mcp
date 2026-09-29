"""Polarity + contradiction detection for the Dissent Ledger (PRD-CORE-144 FR-06).

Belongs to the ``probe`` facade. Re-exported from ``probe/__init__.py``.

Contradiction detection uses a DECLARED polarity function, not a substring
match (FR-06 A1). A plan-branch assumption declares ``polarity`` ("positive"
or "negative"); the probe returns a ``verdict``. The contradiction table:

    polarity=positive + verdict=refutes  -> contradiction
    polarity=negative + verdict=supports -> contradiction
    verdict=inconclusive                 -> never a contradiction (RISK-005)
"""

from __future__ import annotations

__all__ = []
