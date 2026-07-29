"""Typed, documented tunables for the wiring detector (PRD-CORE-232 NFR / FR06).

Deliberately a detector-local frozen dataclass rather than a field on
``TRWConfig``:

- ``models/config/`` is outside this PRD's ownership boundary;
- more importantly, a runtime-config knob for a *gate* is a disable switch.
  Every value here is a threshold used to decide whether to *report*, never
  whether to *run*. There is no environment override and no kill switch —
  turning the detector off is a one-line ``Makefile`` edit that FR08's
  self-check then fails on, which is the intended and only route.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WiringDetectorConfig:
    """Thresholds for the observational checks.

    Attributes:
        predicate_coverage_floor: FR06. A gate whose activation condition is met
            by a smaller fraction of its declared domain than this is classified
            ``PREDICATE_COVERAGE``. 0.05 (5%) is the PRD's asserted floor: the
            PRD-CORE-190 wiring gate measured 0.6% at authoring time.
        inert_min_empty_kwargs: FR05. Minimum number of constant-empty keyword
            arguments a call site must pass before it is even considered inert.
            2 is deliberate under-claiming: a single ``x=None`` is overwhelmingly
            an ordinary optional argument, so one is never enough.
        max_source_bytes: skip pathologically large generated files during the
            AST pass so NFR02's 10-second budget cannot be blown by one blob.
        prd_glob: FR06's measured domain.
    """

    predicate_coverage_floor: float = 0.05
    inert_min_empty_kwargs: int = 2
    max_source_bytes: int = 1_000_000
    prd_glob: str = "docs/requirements-aare-f/prds/*.md"


DEFAULT_CONFIG = WiringDetectorConfig()
