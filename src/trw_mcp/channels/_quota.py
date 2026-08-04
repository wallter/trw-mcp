"""Canonical channel tier vocabulary.

This module used to implement the one-pass tier-down quota enforcement loop for
channel distill segments (PRD-DIST-2400 Phase C, FR13 / R-05 oscillation
prevention). Commit ``b5d104f080`` ("FR01 stages 2-4 — remove the 12
instruction-file injection channels") deleted every caller: the segments whose
byte budget the ladder existed to enforce no longer exist. The enforcement code
survived the removal and stayed exported and unit-tested, so it kept reading as
live — a decomposition that left the callee behind, the inverse of the
caller-left-behind defect commit ``ff261e41f3`` fixed.

``enforce_quota_with_tier_down``, ``check_quota``, ``tier_down`` and
``tier_index`` were removed on 2026-07-30. ``enforce_quota_with_tier_down``
additionally documented its ``content`` parameter as "used as seed" while never
reading it — it rendered exclusively through ``render_at_tier``.

What remains is the piece that IS consumed: the tier ladder itself, read by
``channels/meta_tune/_throttle.py`` as the canonical ordering. Note that
``_throttle`` deliberately keeps its own index helpers rather than reusing the
removed ones — it reverses the ladder to ascending order and resolves an unknown
tier to the TOP (T4), where the removed ``tier_index`` resolved one to the floor
(T0). Opposite fail-safes, both intentional; consolidating them would have been
a behaviour change, not a cleanup.
"""

from __future__ import annotations

__all__ = ["TIER_DOWN_LADDER"]

# Canonical tier ladder — highest fidelity to lowest.
TIER_DOWN_LADDER: tuple[str, ...] = ("T4", "T3", "T2", "T1", "T0")
