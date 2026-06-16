"""AI-development tendency taxonomy + deterministic detectors (PRD-QUAL-109).

A closed, operator-curated taxonomy of AI-development tendencies (the subject of
the feature, exempt from the Substrate-First treadmill per PRD-DIST-218 §3),
plus deterministic detectors for the five grep-detectable signals proven in
``AUDIT-2026-05-17`` §8, plus an advisory report builder consumed by the
``trw-mcp tendencies`` CLI subcommand.

Public surface:
- :mod:`trw_mcp.tendencies.taxonomy` — ``TendencyType`` + ``TENDENCY_METADATA``.
- :mod:`trw_mcp.tendencies.detectors` — the ``Detector`` protocol + detectors.
- :mod:`trw_mcp.tendencies.report` — corpus walker + report builder.
"""

from __future__ import annotations

from trw_mcp.tendencies.taxonomy import (
    TENDENCY_METADATA,
    TendencyMetadata,
    TendencyType,
)

__all__ = [
    "TENDENCY_METADATA",
    "TendencyMetadata",
    "TendencyType",
]
